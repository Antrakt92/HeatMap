"""Read-only Windows volume identity; never infer a disk from its display name."""
import ctypes
from ctypes import wintypes
import re


class _DiskExtent(ctypes.Structure):
    _fields_ = [('disk_number', wintypes.DWORD), ('start', ctypes.c_longlong),
                ('length', ctypes.c_longlong)]


class _VolumeExtents(ctypes.Structure):
    _fields_ = [('count', wintypes.DWORD), ('extent', _DiskExtent)]


_kernel = ctypes.WinDLL('kernel32', use_last_error=True)
_kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
_kernel.CreateFileW.restype = wintypes.HANDLE
_kernel.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p,
    wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
_kernel.DeviceIoControl.restype = wintypes.BOOL
_kernel.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel.CloseHandle.restype = wintypes.BOOL


def volume_disk_number(letter):
    """Return a proven single-extent DiskNumber, or None for unknown/spanned volumes."""
    if not re.fullmatch(r'[A-Za-z]:', letter):
        return None
    # Access 0 is metadata-only; allow normal filesystem readers and writers.
    handle = _kernel.CreateFileW('\\\\.\\' + letter, 0, 7, None, 3, 0, None)
    if handle == ctypes.c_void_p(-1).value:
        return None
    try:
        result, returned = _VolumeExtents(), wintypes.DWORD()
        # IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS. Refuse partial/multi-extent data
        # rather than assigning an arbitrary temperature to a spanned volume.
        ok = _kernel.DeviceIoControl(handle, 0x00560000, None, 0, ctypes.byref(result),
                                    ctypes.sizeof(result), ctypes.byref(returned), None)
        if not ok or returned.value < ctypes.sizeof(result) or result.count != 1:
            return None
        return int(result.extent.disk_number)
    finally:
        _kernel.CloseHandle(handle)
