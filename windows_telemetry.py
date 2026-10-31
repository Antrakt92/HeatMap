"""Read Windows thermal zones and GPU counters through native PDH, without drivers.

ACPI zone names do not establish CPU/GPU die identity. Keep them separate from
CPU package and GPU core sensors. No guessed temperature or fan RPM is emitted.
API: https://learn.microsoft.com/windows/win32/api/pdh/nf-pdh-pdhgetformattedcounterarrayw
"""
import ctypes
from ctypes import wintypes
import math
import re
import time
from native_discovery import discover_thermal_profile, component_temperatures
from windows_gpu import sample_gpu


class _ValueUnion(ctypes.Union):
    _fields_ = [("number", ctypes.c_double), ("integer", ctypes.c_longlong)]


class _Value(ctypes.Structure):
    _fields_ = [("status", wintypes.DWORD), ("value", _ValueUnion)]


class _Item(ctypes.Structure):
    _fields_ = [("name", wintypes.LPWSTR), ("value", _Value)]


def thermal_zones(standard, precise):
    """PDH Temperature is Kelvin; High Precision Temperature is tenths Kelvin."""
    result = []
    for name in sorted(standard.keys() | precise.keys()):
        raw = precise.get(name)
        value = raw / 10 - 273.15 if raw is not None and raw > 0 else None
        if value is None or not math.isfinite(value) or not 0 < value <= 150:
            raw = standard.get(name)
            value = raw - 273.15 if raw is not None else None
        if value is not None and math.isfinite(value) and 0 < value <= 150:
            result.append({"name": name, "temp": round(value, 1)})
    return result


def gpu_utilization(samples, adapter_luid=None):
    """Sum processes sharing an engine, then take the busiest engine, as Windows does."""
    engines = {}
    adapters = set()
    def normalize(value):
        match = re.fullmatch(r"luid_0x([0-9a-f]+)_0x([0-9a-f]+)", value, re.I)
        return (int(match[1], 16), int(match[2], 16)) if match else None
    selected = normalize(adapter_luid) if adapter_luid else None
    if adapter_luid and selected is None:
        return None
    for name, value in samples.items():
        match = re.fullmatch(r"pid_\d+_(luid_0x[0-9a-f]+_0x[0-9a-f]+)_phys_(\d+)_eng_(\d+)_engtype_.+", name, re.I)
        if not match or not math.isfinite(value) or value < 0:
            continue
        identity = normalize(match[1])
        if selected is not None and (identity != selected or int(match[2]) != 0):
            continue
        adapters.add((identity, match[2]))
        key = (identity, match[2], match[3])
        engines[key] = engines.get(key, 0) + value
    if selected is None and len(adapters) > 1:
        return None
    return round(min(100, max(engines.values())), 1) if engines else None


class WindowsTelemetry:
    PATHS = {
        "thermal": r"\Thermal Zone Information(*)\Temperature",
        "precise": r"\Thermal Zone Information(*)\High Precision Temperature",
        "gpu": r"\GPU Engine(*)\Utilization Percentage",
        "frequency": r"\Processor Information(*)\Processor Frequency",
    }

    def __init__(self):
        self.profile, self.profile_error = {}, None
        try:
            self.profile = discover_thermal_profile()
        except Exception as exc:
            self.profile_error = str(exc)
        self.api = ctypes.WinDLL("pdh.dll")
        handle = ctypes.c_void_p
        status = wintypes.DWORD
        ptr = ctypes.POINTER
        for name, args in {
            "PdhOpenQueryW": [wintypes.LPCWSTR, ctypes.c_size_t, ptr(handle)],
            "PdhAddEnglishCounterW": [handle, wintypes.LPCWSTR, ctypes.c_size_t, ptr(handle)],
            "PdhCollectQueryData": [handle],
            "PdhGetFormattedCounterArrayW": [handle, status, ptr(status), ptr(status), ctypes.c_void_p],
            "PdhCloseQuery": [handle],
        }.items():
            function = getattr(self.api, name)
            function.argtypes = args
            function.restype = status
        self.query = None
        self.counters = {}
        self._next_open_retry = 0
        self._open_query()

    def _open_query(self):
        self._next_open_retry = time.monotonic() + 30
        self.query = ctypes.c_void_p()
        self.counters = {}
        if self.api.PdhOpenQueryW(None, 0, ctypes.byref(self.query)):
            self.query = None
            return
        try:
            for key, path in self.PATHS.items():
                counter = ctypes.c_void_p()
                if not self.api.PdhAddEnglishCounterW(self.query, path, 0, ctypes.byref(counter)):
                    self.counters[key] = counter
            if not self.counters:
                self.close()
                return
            # Rate counters require a baseline. No sleeps or subprocesses here.
            self.api.PdhCollectQueryData(self.query)
        except BaseException:
            self.close()
            raise

    def _read(self, counter):
        size, count = wintypes.DWORD(), wintypes.DWORD()
        read = self.api.PdhGetFormattedCounterArrayW
        status = read(counter, 0x200 | 0x8000, ctypes.byref(size), ctypes.byref(count), None)
        if status != 0x800007D2 or not 0 < size.value <= 4 * 1024 * 1024:
            return {}
        for _ in range(3):  # Process/engine instances may change between calls.
            buffer = ctypes.create_string_buffer(size.value)
            status = read(counter, 0x200 | 0x8000, ctypes.byref(size), ctypes.byref(count), buffer)
            if status == 0x800007D2 and 0 < size.value <= 4 * 1024 * 1024:
                continue
            if status or count.value * ctypes.sizeof(_Item) > len(buffer):
                return {}
            items = ctypes.cast(buffer, ctypes.POINTER(_Item))
            return {items[i].name: items[i].value.value.number for i in range(count.value)
                    if items[i].value.status in (0, 1) and items[i].name
                    and math.isfinite(items[i].value.value.number)}
        return {}

    def sample(self):
        if not self.query and time.monotonic() >= getattr(self, "_next_open_retry", 0):
            self._open_query()
        available = self.query and self.api.PdhCollectQueryData(self.query) == 0
        if not available and self.query:
            self.close()
            self._next_open_retry = time.monotonic() + 30
        values = {key: self._read(counter) for key, counter in self.counters.items()} if available else {}
        gpu = {}
        try:
            gpu = sample_gpu()
        except (OSError, AttributeError):
            pass
        clocks = [v for v in values.get("frequency", {}).values() if 0 < v < 15000]
        zones = thermal_zones(values.get("thermal", {}), values.get("precise", {}))
        result = {
            "thermal_zones": zones,
            "gpu_load": gpu_utilization(values.get("gpu", {}), gpu.get("gpu_adapter_luid")),
            "gpu_load_source": "Windows GPU Engine (busiest engine)",
            "cpu_clock": round(max(clocks)) if clocks else None,
        }
        result.update(component_temperatures(zones, getattr(self, "profile", {})))
        result.update(gpu)
        if result.get("gpu_temp") is not None:
            result["gpu_core_temp"] = result["gpu_temp"]
        if getattr(self, "profile_error", None):
            result["native_thermal_profile_error"] = self.profile_error
        return result

    def close(self):
        if self.query:
            self.api.PdhCloseQuery(self.query)
            self.query = None
        self.counters = {}
