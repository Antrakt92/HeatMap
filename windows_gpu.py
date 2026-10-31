"""Read current WDDM GPU engine frequency through the standard Windows API.

Frequency is the driver's current NodeOrdinal=0 frequency, never MaxFrequency.
https://learn.microsoft.com/windows-hardware/drivers/ddi/d3dkmthk/ns-d3dkmthk-_d3dkmt_node_perfdata
"""
import ctypes as c
from ctypes import wintypes as w
from functools import lru_cache


class _Luid(c.Structure):
    _fields_ = [("low", w.DWORD), ("high", w.LONG)]


class _Adapter(c.Structure):
    _fields_ = [("handle", w.UINT), ("luid", _Luid), ("sources", w.ULONG), ("precise", w.BOOL)]


class _Enumeration(c.Structure):
    _fields_ = [("count", w.ULONG), ("adapters", c.POINTER(_Adapter))]


class _Query(c.Structure):
    _fields_ = [("handle", w.UINT), ("kind", w.UINT), ("data", c.c_void_p), ("size", w.UINT)]


class _Node(c.Structure):
    _fields_ = [("node", w.UINT), ("physical", w.UINT), ("frequency", c.c_uint64),
                ("maximum", c.c_uint64), ("maximum_oc", c.c_uint64), ("voltage", w.ULONG),
                ("voltage_max", w.ULONG), ("voltage_oc", w.ULONG), ("latency", c.c_uint64)]


def current_mhz(frequency, maximum):
    if not 0 <= frequency <= 20_000_000_000:
        return None
    if frequency == 0 and not 0 < maximum <= 20_000_000_000:
        return None
    return round(frequency / 1_000_000)


@lru_cache(maxsize=1)
def _gpu_api():
    api = c.WinDLL("gdi32.dll")
    api.D3DKMTEnumAdapters2.argtypes = [c.POINTER(_Enumeration)]
    api.D3DKMTEnumAdapters2.restype = w.ULONG
    api.D3DKMTQueryAdapterInfo.argtypes = [c.POINTER(_Query)]
    api.D3DKMTQueryAdapterInfo.restype = w.ULONG
    api.D3DKMTCloseAdapter.argtypes = [c.POINTER(w.UINT)]
    api.D3DKMTCloseAdapter.restype = w.ULONG
    return api


def sample_gpu():
    api = _gpu_api()
    adapters = (_Adapter * 64)()
    enumeration = _Enumeration(64, adapters)

    def query(adapter, kind, data):
        request = _Query(adapter.handle, kind, c.addressof(data), c.sizeof(data))
        return api.D3DKMTQueryAdapterInfo(c.byref(request)) == 0

    try:
        if api.D3DKMTEnumAdapters2(c.byref(enumeration)) or not 0 < enumeration.count <= 64:
            return {}
        candidates = []
        for adapter in adapters[:enumeration.count]:
            flags = w.UINT()
            if not query(adapter, 15, flags) or flags.value & (1 << 2):
                continue  # Software adapters cannot supply hardware clocks.
            names = (w.WCHAR * (260 * 4))()
            if query(adapter, 8, names) and names.value.strip():
                candidates.append((adapter, names.value.strip()))
        adreno = [item for item in candidates if "adreno" in item[1].casefold()]
        selected = adreno if len(adreno) == 1 else candidates
        if len(selected) != 1:
            return {}  # Do not join one adapter's temperature to another's clock.
        adapter, name = selected[0]
        result = {"gpu_adapter_luid": f"luid_0x{adapter.luid.high & 0xffffffff:08x}_0x{adapter.luid.low:08x}",
                  "gpu_name": name}
        node = _Node(0, 0)
        if not query(adapter, 61, node):
            return result
        clock = current_mhz(node.frequency, node.maximum)
        if clock is not None:
            result.update(gpu_clock=clock, gpu_clock_source="Windows WDDM engine 0", gpu_clock_adapter=name)
        return result
    finally:
        closed = set()
        for adapter in adapters:
            if adapter.handle and adapter.handle not in closed:
                handle = w.UINT(adapter.handle)
                api.D3DKMTCloseAdapter(c.byref(handle))
                closed.add(adapter.handle)
