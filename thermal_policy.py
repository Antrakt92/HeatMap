"""Pure thermal interpretation and conservative case airflow policy."""
import math
from dataclasses import dataclass


def finite(value, minimum=0, maximum=150):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) and minimum <= value <= maximum else None


def gpu_delta(data):
    core = finite(data.get("gpu_core_temp"), 1)
    hotspot = finite(data.get("gpu_hotspot_temp"), 1)
    return round(hotspot - core) if core is not None and hotspot is not None else None


def delta_severity(data):
    delta = gpu_delta(data)
    hotspot = finite(data.get("gpu_hotspot_temp"), 1)
    # A cold/idle delta alone is not evidence of a cooling problem.
    if delta is None or hotspot is None or hotspot < 80:
        return 0
    return 2 if delta >= 35 else 1 if delta >= 25 else 0


def interpolate(value, points):
    if value <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if value <= x1:
            return y0 + (y1 - y0) * (value - x0) / (x1 - x0)
    return points[-1][1]


def case_fan_demand(data):
    """Use the hottest normalized demand, never average away a hot component."""
    curves = {
        "cpu_temp": ((40, 60), (60, 70), (75, 90), (80, 100)),
        "gpu_core_temp": ((40, 60), (60, 80), (75, 100)),
        "gpu_hotspot_temp": ((60, 60), (80, 80), (95, 100)),
        "gpu_memory_temp": ((60, 60), (80, 80), (95, 100)),
    }
    # This explicit desktop profile requires CPU + all three AMD GPU readings.
    # Unknown/failed input must increase cooling, not look like a cool machine.
    if any(finite(data.get(key), 1) is None for key in curves):
        return 100, "Missing temperature: full airflow"
    if delta_severity(data) == 2:
        return 100, "Large GPU hotspot gap: full airflow"
    demand = math.ceil(max(interpolate(data[key], points) for key, points in curves.items()))
    return demand, "Temperature curve"


@dataclass
class FanRamp:
    value: int = 100
    cool_since: float | None = None
    last_time: float | None = None

    def update(self, demand, now):
        demand = max(60, min(100, math.ceil(demand)))
        elapsed = 0 if self.last_time is None else max(0, min(5, now - self.last_time))
        self.last_time = now
        if demand >= self.value:
            self.value = demand
            self.cool_since = None
        elif self.value - demand >= 3:
            if self.cool_since is None:
                self.cool_since = now
            if now - self.cool_since >= 15:
                self.value = max(demand, self.value - max(1, int(2 * elapsed)))
        else:
            self.cool_since = None
        return self.value


@dataclass(frozen=True)
class Finding:
    key: str
    severity: int
    text: str


class ThermalAdvisor:
    """Immediate temperature alarms; persistent gap/stall warnings avoid one-frame noise."""
    def __init__(self):
        self.since = {}
        self.seen_running_fans = set()
        self.seen_gpu_temperatures = {}
        self.gpu_id = None

    def reset(self):
        self.since.clear()

    def evaluate(self, data, now, temperature_thresholds, disk_thresholds):
        findings = []
        active = set()
        self.gpu_id = data.get("gpu_id") or self.gpu_id
        seen = self.seen_gpu_temperatures.setdefault(self.gpu_id, set())
        for key, label in (("gpu_hotspot_temp", "GPU Hotspot"), ("gpu_memory_temp", "VRAM temp")):
            if finite(data.get(key), 1) is not None:
                seen.add(key)
            elif key in seen:
                findings.append(Finding("missing:" + key, 1, f"Unavailable: {label}"))
        for key, label in (("cpu_temp", "CPU"), ("gpu_temp", "GPU Core"),
                           ("gpu_hotspot_temp", "GPU Hotspot"), ("gpu_memory_temp", "VRAM temp")):
            value = finite(data.get(key), 1)
            if value is not None:
                warning, critical = temperature_thresholds[key]
                if value >= warning:
                    findings.append(Finding(key, 2 if value >= critical else 1,
                                            f"{label} {round(value)}°C: " +
                                            ("reduce load / check cooling" if value >= critical else "warm")))
        gap_level = delta_severity(data)
        if gap_level:
            active.add("gpu_gap")
            start = self.since.setdefault("gpu_gap", now)
            if now - start >= 10:
                findings.append(Finding("gpu_gap", gap_level,
                                        f"GPU hotspot gap +{gpu_delta(data)}°C: verify sensors / check cooling"))
        cpu_hot = (finite(data.get("cpu_temp")) or 0) >= 70
        gpu_hot = ((finite(data.get("gpu_hotspot_temp")) or 0) >= 85 or
                   (finite(data.get("gpu_core_temp")) or 0) >= 80 or
                   (finite(data.get("gpu_memory_temp")) or 0) >= 85)
        fans = [(fan, cpu_hot if any(marker in fan.get("name", "").lower() for marker in ("cpu", "processor")) else cpu_hot or gpu_hot)
                for fan in data.get("fans", [])]
        fans.extend((fan, gpu_hot) for fan in data.get("gpu_fans", []))
        for fan, hot in fans:
            key = str(fan.get("id") or fan.get("name"))
            rpm = finite(fan.get("rpm"), 0, 10000)
            if rpm is not None and rpm > 0:
                self.seen_running_fans.add(key)
            if rpm == 0 and key in self.seen_running_fans and hot:
                active.add(key)
                start = self.since.setdefault(key, now)
                if now - start >= 10:
                    findings.append(Finding(key, 2, f"{fan['name']}: 0 RPM under load"))
        for disk in data.get("disks", []):
            value = finite(disk.get("temp"), 1)
            if value is not None and value >= disk_thresholds(disk["name"])[0]:
                level = 2 if value >= disk_thresholds(disk["name"])[1] else 1
                findings.append(Finding("disk:" + disk["name"], level, f"{disk['name']}: {round(value)}°C"))
            used = finite(disk.get("used_pct"), 0, 100)
            if used is not None and used >= 80:
                findings.append(Finding("space:" + disk["name"], 2 if used >= 90 else 1,
                                        f"{disk['name']}: {round(used)}% full"))
        for key, label, warning, critical in (("ram_pct", "RAM", 80, 95),
                                               ("gpu_vram_pct", "VRAM usage", 90, 98)):
            value = finite(data.get(key), 0, 100)
            if value is not None and value >= warning:
                findings.append(Finding(key, 2 if value >= critical else 1, f"{label}: {round(value)}%"))
        self.since = {key: value for key, value in self.since.items() if key in active}
        return sorted(findings, key=lambda item: -item.severity)
