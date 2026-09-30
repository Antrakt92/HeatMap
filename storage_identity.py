"""Read-only Windows volume identity; never infer a disk from its display name."""
import ctypes
from ctypes import wintypes
import re
import struct


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
_kernel.QueryDosDeviceW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
_kernel.QueryDosDeviceW.restype = wintypes.DWORD


def physical_disk_numbers():
    """Enumerate Windows disk aliases without WMI or touching disk contents.

    None denotes an incomplete inventory; an empty set is a verified absence.
    The names supply physical disk numbers, never inferred volume associations.
    """
    size = 32768
    while size <= 1048576:
        buffer = ctypes.create_unicode_buffer(size)
        length = _kernel.QueryDosDeviceW(None, buffer, size)
        if length:
            numbers = set()
            for alias in buffer[:length].split('\0'):
                match = re.fullmatch(r'PhysicalDrive(\d+)', alias)
                if match and int(match[1]) <= 0x7fffffff:
                    numbers.add(int(match[1]))
            return numbers
        if ctypes.get_last_error() != 122:  # ERROR_INSUFFICIENT_BUFFER
            return None
        size *= 2
    return None


def _property_model(raw):
    """Parse STORAGE_DEVICE_DESCRIPTOR without exposing serial numbers."""
    if len(raw) < 36:
        raise ValueError("Truncated storage descriptor")
    size = struct.unpack_from('<I', raw, 4)[0]
    offset = struct.unpack_from('<I', raw, 16)[0]
    if not 36 <= offset < size <= len(raw):
        raise ValueError("Invalid storage model offset")
    text = raw[offset:size].split(b'\0', 1)
    if len(text) != 2:
        raise ValueError("Unterminated storage model")
    return text[0].decode('ascii', errors='replace').strip()


def _property_temperatures(raw):
    """STORAGE_TEMPERATURE_DATA_DESCRIPTOR: 24-byte header, 16-byte entries.

    Index zero is the primary temperature and may be composite. Critical/warning values in the
    header and entries are thresholds, never substitute temperature readings.
    https://learn.microsoft.com/windows/win32/api/winioctl/ns-winioctl-storage_temperature_info
    """
    if len(raw) < 40:
        raise ValueError("Truncated storage temperature descriptor")
    size = struct.unpack_from('<I', raw, 4)[0]
    count = struct.unpack_from('<H', raw, 12)[0]
    if not 1 <= count <= 16 or not 24 + 16 * count <= size <= len(raw):
        raise ValueError("Invalid storage temperature count/size")
    readings, primary = [], None
    indices = set()
    for entry in range(count):
        index, temperature = struct.unpack_from('<Hh', raw, 24 + 16 * entry)
        if index in indices:
            raise ValueError("Duplicate storage temperature identity")
        indices.add(index)
        if not 1 <= temperature <= 150:
            continue  # -32768 indicates unknown; other sentinel values stay unavailable.
        if index == 0:
            primary = temperature
        readings.append({'name': 'Temperature' if index == 0 else f'Temperature {index + 1}',
                         'temp': temperature})
    return primary, readings


def disk_properties(number, identified=lambda sample: None):
    """Read only Windows model/temperature properties of this physical path.

    Call inside a bounded disposable process, not the main sensor thread.
    The driver may support model identification but not temperature queries.
    """
    if type(number) is not int or not 0 <= number <= 0x7fffffff:
        raise ValueError("Invalid physical disk number")
    handle = _kernel.CreateFileW(r'\\.\PhysicalDrive' + str(number), 0, 7, None, 3, 0, None)
    if handle == ctypes.c_void_p(-1).value:
        return None
    try:
        sample = {'disk_number': number, 'temp': None}
        for prop in (0, 52):  # StorageDeviceProperty / StorageDeviceTemperatureProperty
            query = ctypes.create_string_buffer(struct.pack('<III', prop, 0, 0))
            output, returned = ctypes.create_string_buffer(4096), wintypes.DWORD()
            ok = _kernel.DeviceIoControl(handle, 0x002d1400, query, 12, output, len(output),
                                        ctypes.byref(returned), None)
            if not ok:
                continue
            raw = output.raw[:returned.value]
            if prop == 0:
                sample['name'] = _property_model(raw)
                identified(dict(sample))
            else:
                sample['temp'], readings = _property_temperatures(raw)
                if len(readings) > 1:
                    sample['temperatures'] = readings
        return sample if sample.get('name') else None
    finally:
        _kernel.CloseHandle(handle)


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
