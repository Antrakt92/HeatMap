"""Pinned signed PawnIO bridge for B550 secondary-fan EC access only.

Protocol: namazso/PawnIO.Modules 0.2.10, IsaBridgeEC.p; LHM 0.9.5 PawnIo.cs.
The installed PawnIO driver validates the module signature when loading it.
"""
import ctypes
from ctypes import wintypes
import hashlib
import json
from pathlib import Path
import struct

MODULE_SHA256 = '2c5ec9d09ba42c980c917f83f528e9643ceee386c84ffa8765c521cd1bbba304'
MODULE_SIZE = 51164
MODULE_FILE = 'hardware_modules/IsaBridgeEC.bin'
ROOT = Path(__file__).resolve().parent


def verified_module(root=ROOT):
    metadata = json.loads((root / 'runtime_sources.json').read_text(encoding='utf-8'))['shared_fan_module']
    if (metadata.get('version') != '0.2.10' or metadata.get('file') != MODULE_FILE
            or metadata.get('sha256') != MODULE_SHA256 or metadata.get('size') != MODULE_SIZE):
        raise RuntimeError('Shared fan module provenance does not match the reviewed runtime')
    locked = json.loads((root / 'runtime-lock.json').read_text(encoding='utf-8')).get('supplemental_modules')
    if locked != [dict(file=MODULE_FILE, version='0.2.10', size=MODULE_SIZE,
                       sha256=MODULE_SHA256, source_key='shared_fan_module')]:
        raise RuntimeError('Shared fan module lock is missing or inconsistent')
    data = (root / MODULE_FILE).read_bytes()
    if len(data) != MODULE_SIZE or hashlib.sha256(data).hexdigest() != MODULE_SHA256:
        raise RuntimeError('Shared fan module integrity check failed')
    return data


class IsaBus:
    def __enter__(self):
        # Same recursive, thread-owned mutex used by the pinned LHM backend.
        from System.Threading import Mutex, AbandonedMutexException
        self.mutex = Mutex.OpenExisting('Global\\Access_ISABUS.HTP.Method')
        try:
            acquired = self.mutex.WaitOne(1000)
        except AbandonedMutexException:
            self.mutex.ReleaseMutex()
            self.mutex.Close()
            raise RuntimeError('ISA bus owner exited unexpectedly; refusing fan writes') from None
        if not acquired:
            self.mutex.Close()
            raise RuntimeError('ISA bus is busy')
        return self

    def __exit__(self, *_args):
        self.mutex.ReleaseMutex()
        self.mutex.Close()


class SignedEcBridge:
    """Call only the signed module's bounded EC and mapping operations.

    Callers hold IsaBus for the entire transaction, including ordinary LHM I/O.
    No legacy LHM MMIO controller method is used for fan takeover/restoration.
    """
    def __init__(self):
        data = verified_module()
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        self.kernel.CreateFileW.restype = wintypes.HANDLE
        self.kernel.DeviceIoControl.argtypes = (wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p,
            wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p)
        self.kernel.DeviceIoControl.restype = wintypes.BOOL
        self.kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        self.kernel.CloseHandle.restype = wintypes.BOOL
        self.handle = self.kernel.CreateFileW('\\\\.\\PawnIO', 0xC0000000, 3, None, 3, 0, None)
        if self.handle == ctypes.c_void_p(-1).value:
            self.handle = None
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            self._ioctl((41394 << 16) | (0x821 << 2), data, 0)
            mapping = self.execute('ioctl_find_superio_mmio', (), 6)
            if (mapping[2] != 0x8688 or mapping[5] != 0x8733 or mapping[0] <= 0
                    or mapping[3] <= 0 or mapping[1] != 8192 or mapping[4] != 8192
                    or mapping[0] == mapping[3]):
                raise RuntimeError('Unexpected B550 shared-fan MMIO identity')
            self.original_state = self.execute('ioctl_iomem_mmio_get_org_state', (), 1)[0]
            if self.original_state not in (-1, 0, 1, 2):
                raise RuntimeError('Unsupported original bridge state')
        except Exception:
            self.close()
            raise

    def _ioctl(self, code, data, output_size):
        source = ctypes.create_string_buffer(data, max(len(data), 1))
        output = ctypes.create_string_buffer(max(output_size, 1))
        returned = wintypes.DWORD()
        if not self.kernel.DeviceIoControl(self.handle, code, source, len(data), output,
                                           output_size, ctypes.byref(returned), None):
            raise ctypes.WinError(ctypes.get_last_error())
        if returned.value != output_size:
            raise RuntimeError('Incomplete PawnIO reply')
        return output.raw[:output_size]

    def execute(self, name, values=(), count=0):
        allowed = {'ioctl_find_superio_mmio', 'ioctl_iomem_mmio_get_org_state',
                   'ioctl_iomem_mmio_get_cur_state', 'ioctl_iomem_mmio_set_state',
                   'ioctl_map_superio_mmio', 'ioctl_unmap_superio_mmio', 'ioctl_access_superio_mmio'}
        if name not in allowed:
            raise ValueError('Unsupported shared-fan operation')
        data = name.encode('ascii').ljust(32, b'\0') + struct.pack('<' + 'q' * len(values), *values)
        reply = self._ioctl((41394 << 16) | (0x841 << 2), data, count * 8)
        return struct.unpack('<' + 'q' * count, reply)

    def _access(self, offset, value=None):
        if offset not in (0x900, 0x947) or (value is not None and (offset != 0x947 or type(value) is not int or value not in (0, 1))):
            raise ValueError('Only the version and binary fan-automation flag are accessible')
        mapped = False
        attempted = False
        try:
            self.execute('ioctl_map_superio_mmio')
            mapped = True
            attempted = True
            self.execute('ioctl_iomem_mmio_set_state', (2,))
            if self.execute('ioctl_iomem_mmio_get_cur_state', (), 1)[0] != 2:
                raise RuntimeError('Secondary bridge mapping was not confirmed')
            return self.execute('ioctl_access_superio_mmio', (1, offset, 1, int(value is not None), value or 0), 1)[0]
        finally:
            try:
                if attempted:
                    self.execute('ioctl_iomem_mmio_set_state', (-1,))
                    if self.execute('ioctl_iomem_mmio_get_cur_state', (), 1)[0] != self.original_state:
                        raise RuntimeError('Original bridge addressing was not restored')
            finally:
                if mapped:
                    self.execute('ioctl_unmap_superio_mmio')

    def read_mode(self):
        # SMFI uses the binary enable byte. The version=1 check in upstream
        # belongs to the separate ECIO protocol, which this board does not expose.
        value = self._access(0x947)
        if value not in (0, 1):
            raise RuntimeError('Shared-fan EC mode is unreadable or unsupported')
        return value

    def write_mode(self, value):
        self._access(0x947, value)
        if self.read_mode() != value:
            raise RuntimeError('Shared-fan EC mode write was not confirmed')

    def close(self):
        if self.handle is not None:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
