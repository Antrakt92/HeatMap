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


class CaseAirflowPolicy:
    """Add bounded, upward-only assistance to the established temperature curves."""
    # B550 AORUS PRO AC's firmware-owned IT8792E channels; never infer by name alone.
    _CASE_HEADERS = {
        "/lpc/it8792e/0/fan/0": ("System Fan #5 / Pump", "System Fan #5"),
        "/lpc/it8792e/0/fan/1": ("System Fan #6 / Pump", "System Fan #6"),
        "/lpc/it8792e/0/fan/2": ("System Fan #4",),
    }

    def __init__(self):
        self.history = []
        self.last_time = None
        self.gpu_id = None
        self.seen_running = set()
        self.stopped_since = {}

    def _stalled_header(self, data, now):
        hot = any((finite(data.get(key), 1) or 0) >= threshold for key, threshold in (
            ("cpu_temp", 70), ("gpu_core_temp", 80),
            ("gpu_hotspot_temp", 85), ("gpu_memory_temp", 85)))
        by_id = {}
        for fan in data.get("fans", []):
            by_id.setdefault(fan.get("id"), []).append(fan)
        pending = {}
        stalled = None
        for identifier, names in self._CASE_HEADERS.items():
            matches = by_id.get(identifier, [])
            if len(matches) != 1 or matches[0].get("name") not in names:
                continue
            fan = matches[0]
            rpm = finite(fan.get("rpm"), 0, 10000)
            if rpm is not None and rpm >= 200:
                self.seen_running.add(identifier)
            if rpm == 0 and hot and identifier in self.seen_running:
                start = self.stopped_since.get(identifier, now)
                pending[identifier] = start
                if now - start >= 2:
                    stalled = fan["name"].removesuffix(" / Pump")
        self.stopped_since = pending
        return stalled

    def update(self, data, now):
        demand, reason = case_fan_demand(data)
        if finite(now, 0, 1e15) is None:
            self.history.clear()
            self.last_time = None
            self.stopped_since.clear()
            return 100, "Invalid sample time: full airflow"
        gpu_id = data.get("gpu_id")
        if (self.last_time is not None and (now <= self.last_time or now - self.last_time > 5)
                or gpu_id != self.gpu_id):
            self.history.clear()
            self.stopped_since.clear()
        self.last_time, self.gpu_id = now, gpu_id
        stalled = self._stalled_header(data, now)
        if demand == 100:
            self.history.clear()
            return demand, reason
        if stalled is not None:
            self.history.clear()
            return 100, stalled + ": confirmed 0 RPM under load; airflow assist"
        temperatures = {key: data[key] for key in CASE_FAN_CURVES}
        # Bounded even if a caller samples much faster than the normal two seconds.
        self.history = [(stamp, values) for stamp, values in self.history if now - stamp <= 10][-63:]
        self.history.append((now, temperatures))
        duration = now - self.history[0][0]
        if len(self.history) < 3 or duration < 6:
            return demand, reason
        assisted, limiting = demand, None
        for key, points in CASE_FAN_CURVES.items():
            values = [values[key] for _, values in self.history]
            rise = values[-1] - values[0]
            positive_steps = sum(after > before for before, after in zip(values, values[1:]))
            if rise < 2 or positive_steps < 2 or values[-1] < values[-2]:
                continue
            projected = values[-1] + min(5, 5 * rise / duration)
            projected_demand = min(100, demand + 10, math.ceil(interpolate(projected, points)))
            if projected_demand > assisted:
                assisted, limiting = projected_demand, key
        if limiting is not None:
            return assisted, CASE_FAN_LABELS[limiting] + " rising: airflow assist"
        return demand, reason


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
        self.seen_running_fans = {}
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
            for key, (_label, scope) in list(self.seen_running_fans.items()):
                if scope == "gpu":
                    del self.seen_running_fans[key]
                    self.since.pop(key, None)
                    self.since.pop("tach_missing:" + key, None)
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
        fans = [(fan, "cpu" if any(marker in fan.get("name", "").lower() for marker in ("cpu", "processor")) else "case")
                for fan in data.get("fans", [])]
        fans.extend((fan, "gpu") for fan in data.get("gpu_fans", []))
        present = {str(fan.get("id") or fan.get("name")) for fan, _scope in fans}
        # Reinitialization can remove the sensor object instead of returning a
        # null RPM. Preserve its label and heat source, never its last speed.
        fans.extend((dict(id=key, name=label, rpm=None), scope)
                    for key, (label, scope) in self.seen_running_fans.items() if key not in present)
        for fan, scope in fans:
            hot = cpu_hot if scope == "cpu" else gpu_hot if scope == "gpu" else cpu_hot or gpu_hot
            label = str(fan.get("name", "Fan")).removesuffix(" / Pump")
            key = str(fan.get("id") or fan.get("name"))
            rpm = finite(fan.get("rpm"), 0, 10000)
            if rpm is not None and rpm > 0:
                self.seen_running_fans[key] = (label, scope)
            if (rpm == 0 or rpm is None) and key in self.seen_running_fans and hot:
                # Missing tach feedback is not proof the fan has physically stopped.
                timer_key = "tach_missing:" + key if rpm is None else key
                active.add(timer_key)
                start = self.since.setdefault(timer_key, now)
                if now - start >= 10:
                    if rpm is None:
                        findings.append(Finding(timer_key, 1, f"{label}: tachometer unavailable under load"))
                    else:
                        findings.append(Finding(key, 2, f"{label}: 0 RPM under load"))
        for volume in data.get("volumes", []):
            used = finite(volume.get("used_pct"), 0, 100)
            free = finite(volume.get("free_bytes"), 0, 2**64 - 1)
            warning, critical = temperature_thresholds["disk_used"]
            if used is not None and free is not None and used >= warning:
                findings.append(Finding("volume:" + volume["name"], 2 if used >= critical else 1,
                                        f"Volume {volume['name']} {round(used)}% full · {free / 2**30:.1f} GiB free"))
        for error in data.get("volume_errors", []):
            findings.append(Finding("volume_error:" + error, 1, "Volume space unavailable: " + error))
        for disk in data.get("disks", []):
            value = finite(disk.get("temp"), 1)
            if value is not None and value >= disk_thresholds(disk["name"])[0]:
                level = 2 if value >= disk_thresholds(disk["name"])[1] else 1
                findings.append(Finding("disk:" + disk["name"], level, f"{disk['name']}: {round(value)}°C"))
        # Elevated usage already colors its row; reserve panel space for critical pressure.
        for key, label in (("ram_pct", "RAM"), ("gpu_vram_pct", "VRAM usage")):
            critical = temperature_thresholds[key][1]
            value = finite(data.get(key), 0, 100)
            if value is not None and value >= critical:
                findings.append(Finding(key, 2, f"{label}: {round(value)}%"))
        self.since = {key: value for key, value in self.since.items() if key in active}
        return sorted(findings, key=lambda item: -item.severity)
