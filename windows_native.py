"""Platform selection for the driver-free Windows monitoring backend."""
import ctypes
import platform
from pathlib import Path
import re
import sys

CONSTRAINTS_PATH = Path(__file__).with_name("constraints-known-good.txt")


def _psutil_pin():
    pins = re.findall(r'^psutil\s*==\s*([0-9]+(?:\.[0-9]+)*)\s*(?:#.*)?$',
                      CONSTRAINTS_PATH.read_text(encoding="utf-8"), re.M)
    if len(pins) != 1:
        raise ValueError("Expected one exact psutil pin in constraints-known-good.txt")
    return pins[0]


def windows_only_mode():
    """Detect the host CPU, including x64 Python emulated on ARM Windows."""
    if sys.platform != "win32":
        return False
    if platform.machine().casefold() in {"arm64", "aarch64"}:
        return True
    process_machine = ctypes.c_ushort()
    native_machine = ctypes.c_ushort()
    try:
        query = ctypes.windll.kernel32.IsWow64Process2
        query.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ushort),
                          ctypes.POINTER(ctypes.c_ushort)]
        query.restype = ctypes.c_bool
        if not query(ctypes.c_void_p(-1), ctypes.byref(process_machine), ctypes.byref(native_machine)):
            raise ctypes.WinError()
        return native_machine.value == 0xAA64
    except AttributeError:  # Older x64 Windows without IsWow64Process2.
        return False


def check_windows_dependencies():
    """Do not import the x64 CLR bridge or require any hardware driver."""
    import importlib.metadata
    messages = []
    if sys.maxsize <= 2 ** 32 or sys.version_info < (3, 10):
        messages.append("Windows monitoring requires 64-bit Python 3.10 or newer.")
    try:
        import psutil
        expected = _psutil_pin()
        if importlib.metadata.version("psutil") != expected:
            messages.append(f"Windows monitoring requires psutil=={expected}.")
        psutil.virtual_memory()
        psutil.cpu_percent(interval=0)
        import tkinter
        tkinter.Tcl()
    except Exception as exc:
        messages.append(f"Windows monitoring dependency check failed: {exc}")
    return not messages, messages
