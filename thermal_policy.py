"""Pure thermal interpretation and conservative case airflow policy."""
import math
from dataclasses import dataclass


CASE_FAN_CURVES = {
    "cpu_temp": ((40, 60), (60, 70), (75, 90), (80, 100)),
    "gpu_core_temp": ((40, 60), (60, 80), (75, 100)),
    "gpu_hotspot_temp": ((60, 60), (80, 80), (95, 100)),
    "gpu_memory_temp": ((60, 60), (80, 80), (95, 100)),
}
CASE_FAN_LABELS = {
    "cpu_temp": "CPU", "gpu_core_temp": "GPU Core",
    "gpu_hotspot_temp": "GPU Hotspot", "gpu_memory_temp": "GPU Memory",
}


def finite(value, minimum=0, maximum=150):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    # Check bounds before float conversion inside isfinite, including huge ints.
    return value if minimum <= value <= maximum and math.isfinite(value) else None


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
    # This explicit desktop profile requires CPU + all three AMD GPU readings.
    # Unknown/failed input must increase cooling, not look like a cool machine.
    missing = [CASE_FAN_LABELS[key] for key in CASE_FAN_CURVES if finite(data.get(key), 1) is None]
    if missing:
        return 100, "Missing temperature: " + ", ".join(missing) + "; full airflow"
    if delta_severity(data) == 2:
        return 100, "Large GPU hotspot gap: full airflow"
    demands = {key: interpolate(data[key], points) for key, points in CASE_FAN_CURVES.items()}
    limiting = max(demands, key=demands.__getitem__)
    return math.ceil(demands[limiting]), CASE_FAN_LABELS[limiting] + " curve"


@dataclass
class FanRamp:
    value: int = 100
    cool_since: float | None = None
    last_time: float | None = None
    _fall_fraction: float = 0.0

    def update(self, demand, now):
        valid_demand = finite(demand, 0, 100)
        demand = 100 if valid_demand is None else max(60, math.ceil(valid_demand))
        if self.last_time is not None:
            now = max(now, self.last_time)
        elapsed = 0 if self.last_time is None else min(5, now - self.last_time)
        self.last_time = now
        if demand >= self.value:
            self.value = demand
            self.cool_since = None
            self._fall_fraction = 0.0
        elif self.value - demand >= 3:
            if self.cool_since is None:
                self.cool_since = now
                self._fall_fraction = 0.0
            if now - self.cool_since >= 15:
                # Fractional intervals share one budget; repeated calls cannot
                # turn a two-point-per-second fall into one point per call.
                # The hold interval contributes no credit to the later fall.
                eligible_elapsed = min(elapsed, max(0, now - (self.cool_since + 15)))
                budget = self._fall_fraction + 2 * eligible_elapsed
                decrease = int(budget)
                self._fall_fraction = budget - decrease
                self.value = max(demand, self.value - decrease)
        else:
            self.cool_since = None
            self._fall_fraction = 0.0
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
        gpu_id = data.get("gpu_id") or self.gpu_id
        if gpu_id != self.gpu_id:
            self.since.pop("gpu_gap", None)
        self.gpu_id = gpu_id
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
            warning, critical = temperature_thresholds["disk_used"]
            if used is not None and used >= warning:
                findings.append(Finding("space:" + disk["name"], 2 if used >= critical else 1,
                                        f"{disk['name']}: {round(used)}% full"))
        # Elevated usage already colors its row; reserve panel space for critical pressure.
        for key, label in (("ram_pct", "RAM"), ("gpu_vram_pct", "VRAM usage")):
            critical = temperature_thresholds[key][1]
            value = finite(data.get(key), 0, 100)
            if value is not None and value >= critical:
                findings.append(Finding(key, 2, f"{label}: {round(value)}%"))
        self.since = {key: value for key, value in self.since.items() if key in active}
        return sorted(findings, key=lambda item: -item.severity)
