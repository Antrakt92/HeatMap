"""
Desktop Temperature Overlay
Desktop widget showing hardware temperatures and usage.
Sits on the desktop layer — above wallpaper, below all app windows.
Requires admin privileges to read hardware sensors.
"""
import base64
import ctypes
import csv
import math
import re
import ctypes.wintypes
from dataclasses import dataclass
import json
import locale
import logging
import logging.handlers
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import winreg
import winsound
import xml.etree.ElementTree as ET

import psutil

from thermal_policy import ThermalAdvisor, gpu_delta, delta_severity
from case_fans import FanWorkerClient

VERSION = "1.0.0"


# --- Paths ---
APP_DIR = os.path.dirname(os.path.abspath(__file__))
LIB_DIR = os.path.join(APP_DIR, "lib")
LIB_MANIFEST_PATH = os.path.join(APP_DIR, "lib_manifest.json")
CONFIG_PATH = os.path.join(APP_DIR, "overlay_config.json")
_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"


def _get_log_path(env=None, app_dir=APP_DIR):
    env = os.environ if env is None else env
    local_appdata = env.get("LOCALAPPDATA")
    if local_appdata:
        return os.path.join(local_appdata, "HeatMap", "HeatMap.log")
    return os.path.join(app_dir, "HeatMap.log")


log = logging.getLogger("HeatMap")


def _configure_logging():
    logging.basicConfig(level=logging.WARNING, format=_LOG_FORMAT, datefmt=_LOG_DATEFMT)
    log.setLevel(logging.WARNING)

    log_path = _get_log_path()
    abs_log_path = os.path.abspath(log_path)
    for handler in log.handlers:
        if getattr(handler, "_heatmap_log_path", None) == abs_log_path:
            return log_path

    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=1024 * 1024, backupCount=3, encoding="utf-8"
        )
        handler.setLevel(logging.WARNING)
        handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_LOG_DATEFMT))
        handler._heatmap_log_path = abs_log_path
        log.addHandler(handler)
    except Exception:
        log.warning("Failed to configure file logging at %s", log_path, exc_info=True)
    return log_path


LOG_PATH = _get_log_path()

SENSOR_STATUS_KEY = "_sensor_status"
SENSOR_REINIT_KEY = "_sensor_reinit"
SENSOR_STORAGE_FAILED_KEY = "_storage_read_failed"
SENSOR_STATUS_PSUTIL_FALLBACK = "psutil_fallback"
SENSOR_STATUS_PARTIAL = "partial"
SENSOR_STATUS_WARMING_UP = "warming_up"
SENSOR_STATUS_DRIVER_MISSING = "driver_missing"
SENSOR_STATUS_CPU_UNAVAILABLE = "cpu_unavailable"
SENSOR_STATUS_STALE = "stale"
SENSOR_STALE_SECONDS = 10
SENSOR_WARMUP_SECONDS = 60
SENSOR_INIT_RETRY_SECONDS = 30
STATUS_CONFIG_SAVE_ERROR = "config_save_error"
STATUS_CONFIG_ADJUSTED = "config_adjusted"
CONFIG_STATUSES = (STATUS_CONFIG_SAVE_ERROR, STATUS_CONFIG_ADJUSTED)
STATUS_TEXT = {
    STATUS_CONFIG_SAVE_ERROR: "Config save failed",
    STATUS_CONFIG_ADJUSTED: "Config adjusted",
    SENSOR_STATUS_PSUTIL_FALLBACK: "Sensors: psutil fallback",
    SENSOR_STATUS_PARTIAL: "Sensors: partial data",
    SENSOR_STATUS_WARMING_UP: "Sensors: warming up",
    SENSOR_STATUS_DRIVER_MISSING: "Driver: install PawnIO",
    SENSOR_STATUS_CPU_UNAVAILABLE: "CPU sensor unavailable",
    SENSOR_STATUS_STALE: "Sensors: waiting for fresh data",
}
STATUS_COLOR = {
    STATUS_CONFIG_SAVE_ERROR: "#f87171",
    STATUS_CONFIG_ADJUSTED: "#facc15",
    SENSOR_STATUS_PSUTIL_FALLBACK: "#facc15",
    SENSOR_STATUS_PARTIAL: "#facc15",
    SENSOR_STATUS_WARMING_UP: "#facc15",
    SENSOR_STATUS_DRIVER_MISSING: "#f87171",
    SENSOR_STATUS_CPU_UNAVAILABLE: "#f87171",
    SENSOR_STATUS_STALE: "#facc15",
}

# --- Windows API constants ---
GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_CHILD = 0x40000000
WS_POPUP = 0x80000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
HWND_BOTTOM = 1
HWND_TOP = 0
SW_SHOWNOACTIVATE = 4
GW_HWNDPREV = 3
WS_EX_TOPMOST = 0x00000008
DWMWA_EXCLUDED_FROM_PEEK = 12
user32 = ctypes.windll.user32
user32.SetWindowLongW.argtypes = [ctypes.wintypes.HWND, ctypes.c_int, ctypes.c_long]
user32.SetWindowLongW.restype = ctypes.c_long
user32.GetWindowLongW.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = ctypes.c_long
user32.SetWindowPos.argtypes = [
    ctypes.wintypes.HWND, ctypes.wintypes.HWND,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    ctypes.c_uint,
]
user32.FindWindowW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p]
user32.FindWindowW.restype = ctypes.wintypes.HWND
user32.SendMessageTimeoutW.argtypes = [
    ctypes.wintypes.HWND, ctypes.c_uint, ctypes.wintypes.WPARAM,
    ctypes.wintypes.LPARAM, ctypes.c_uint, ctypes.c_uint,
    ctypes.POINTER(ctypes.c_size_t),
]
user32.SendMessageTimeoutW.restype = ctypes.wintypes.LPARAM
user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM), ctypes.wintypes.LPARAM]
user32.FindWindowExW.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.HWND, ctypes.c_wchar_p, ctypes.c_wchar_p]
user32.FindWindowExW.restype = ctypes.wintypes.HWND
user32.SetParent.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.HWND]
user32.SetParent.restype = ctypes.wintypes.HWND
user32.GetParent.argtypes = [ctypes.wintypes.HWND]
user32.GetParent.restype = ctypes.wintypes.HWND
user32.IsWindow.argtypes = [ctypes.wintypes.HWND]
user32.IsWindow.restype = ctypes.c_bool
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
user32.IsIconic.argtypes = [ctypes.wintypes.HWND]
user32.IsIconic.restype = ctypes.wintypes.BOOL
user32.IsWindowVisible.argtypes = [ctypes.wintypes.HWND]
user32.IsWindowVisible.restype = ctypes.wintypes.BOOL
user32.ShowWindow.argtypes = [ctypes.wintypes.HWND, ctypes.c_int]
user32.ShowWindow.restype = ctypes.wintypes.BOOL
user32.GetWindow.argtypes = [ctypes.wintypes.HWND, ctypes.c_uint]
user32.GetWindow.restype = ctypes.wintypes.HWND
dwmapi = ctypes.windll.dwmapi
dwmapi.DwmSetWindowAttribute.argtypes = [
    ctypes.wintypes.HWND, ctypes.wintypes.DWORD, ctypes.c_void_p, ctypes.wintypes.DWORD,
]
dwmapi.DwmSetWindowAttribute.restype = ctypes.c_long

class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.wintypes.DWORD),
        ("rcMonitor", ctypes.wintypes.RECT),
        ("rcWork", ctypes.wintypes.RECT),
        ("dwFlags", ctypes.wintypes.DWORD),
    ]

user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = ctypes.c_bool
user32.WindowFromPoint.argtypes = [POINT]
user32.WindowFromPoint.restype = ctypes.wintypes.HWND
user32.GetAncestor.argtypes = [ctypes.wintypes.HWND, ctypes.c_uint]
user32.GetAncestor.restype = ctypes.wintypes.HWND
user32.GetClassName = user32.GetClassNameW
user32.GetClassName.argtypes = [ctypes.wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
user32.GetClassName.restype = ctypes.c_int
GA_PARENT = 1
user32.GetWindowThreadProcessId.argtypes = [ctypes.wintypes.HWND, ctypes.POINTER(ctypes.wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = ctypes.wintypes.DWORD
_MONITOR_ENUM_PROC = ctypes.WINFUNCTYPE(
    ctypes.c_bool,
    ctypes.wintypes.HMONITOR,
    ctypes.wintypes.HDC,
    ctypes.POINTER(ctypes.wintypes.RECT),
    ctypes.wintypes.LPARAM,
)
user32.EnumDisplayMonitors.argtypes = [
    ctypes.wintypes.HDC,
    ctypes.POINTER(ctypes.wintypes.RECT),
    _MONITOR_ENUM_PROC,
    ctypes.wintypes.LPARAM,
]
user32.EnumDisplayMonitors.restype = ctypes.c_bool
user32.GetMonitorInfoW.argtypes = [ctypes.wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype = ctypes.c_bool
_MY_PID = os.getpid()

# Virtual screen metrics (all monitors combined)
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int
kernel32 = ctypes.windll.kernel32
kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
kernel32.CreateMutexW.restype = ctypes.wintypes.HANDLE
kernel32.ReleaseMutex.argtypes = [ctypes.wintypes.HANDLE]
kernel32.ReleaseMutex.restype = ctypes.c_bool
kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
kernel32.CloseHandle.restype = ctypes.c_bool
kernel32.GetLastError.restype = ctypes.wintypes.DWORD
kernel32.GetWindowsDirectoryW.argtypes = [ctypes.c_wchar_p, ctypes.wintypes.UINT]
kernel32.GetWindowsDirectoryW.restype = ctypes.wintypes.UINT
kernel32.ProcessIdToSessionId.argtypes = [ctypes.wintypes.DWORD, ctypes.POINTER(ctypes.wintypes.DWORD)]
kernel32.ProcessIdToSessionId.restype = ctypes.c_bool
wtsapi32 = ctypes.windll.wtsapi32
wtsapi32.WTSQuerySessionInformationW.argtypes = [
    ctypes.wintypes.HANDLE,
    ctypes.wintypes.DWORD,
    ctypes.c_int,
    ctypes.POINTER(ctypes.c_void_p),
    ctypes.POINTER(ctypes.wintypes.DWORD),
]
wtsapi32.WTSQuerySessionInformationW.restype = ctypes.c_bool
wtsapi32.WTSFreeMemory.argtypes = [ctypes.c_void_p]
wtsapi32.WTSFreeMemory.restype = None
_INSTANCE_MUTEX_NAME = r"Local\HeatMapOverlay"
_ERROR_ALREADY_EXISTS = 183
_instance_mutex_handle = None


def _rect_tuple(rect):
    return rect.left, rect.top, rect.right, rect.bottom


def _get_monitor_areas():
    areas = []

    @_MONITOR_ENUM_PROC
    def callback(monitor, _hdc, _rect, _lparam):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            areas.append((_rect_tuple(info.rcMonitor), _rect_tuple(info.rcWork)))
        return True

    if user32.EnumDisplayMonitors(0, None, callback, 0) and areas:
        return tuple(areas)
    left = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    top = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    rect = (
        left,
        top,
        left + user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
        top + user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
    )
    return ((rect, rect),)


def _rect_contains_point(rect, x, y):
    left, top, right, bottom = rect
    return left <= x < right and top <= y < bottom


def _rect_intersection_area(first, second):
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    return max(0, right - left) * max(0, bottom - top)


def _distance_to_rect_squared(rect, x, y):
    dx = max(rect[0] - x, 0, x - rect[2])
    dy = max(rect[1] - y, 0, y - rect[3])
    return dx * dx + dy * dy


def _select_monitor_for_window(x, y, width, height, monitor_areas):
    if not monitor_areas:
        return None
    window_rect = (x, y, x + max(1, width), y + max(1, height))
    ranked = [
        (_rect_intersection_area(window_rect, work), monitor, work)
        for monitor, work in monitor_areas
    ]
    best = max(ranked, key=lambda item: item[0])
    if best[0] > 0:
        return best[1], best[2]
    center_x = x + max(1, width) // 2
    center_y = y + max(1, height) // 2
    return min(
        monitor_areas,
        key=lambda area: _distance_to_rect_squared(area[1], center_x, center_y),
    )


def _clamp_overlay_to_monitor_areas(x, y, width, height, monitor_areas):
    selected = _select_monitor_for_window(x, y, width, height, monitor_areas)
    if selected is None:
        return int(x), int(y)
    _monitor, work = selected
    return _clamp_overlay_position(
        x,
        y,
        width,
        height,
        work[0],
        work[1],
        work[2] - work[0],
        work[3] - work[1],
    )


def _exposed_right_edge_monitor(x, y, monitor_areas, width=6):
    for area in monitor_areas:
        monitor, work = area
        # The taskbar (including Show Desktop) is not a Peek target. A right
        # taskbar moves the trigger to the last pixels of usable workspace.
        if not _rect_contains_point(work, x, y) or x < work[2] - width:
            continue
        if any(
            other is not area
            and other[0][0] <= monitor[2]
            and other[0][2] > monitor[2]
            and other[0][1] <= y < other[0][3]
            for other in monitor_areas
        ):
            continue
        return area
    return None


def acquire_single_instance():
    global _instance_mutex_handle
    if _instance_mutex_handle:
        return True
    handle = kernel32.CreateMutexW(None, True, _INSTANCE_MUTEX_NAME)
    if not handle:
        log.warning("Could not create single-instance mutex")
        return False
    if kernel32.GetLastError() == _ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False
    _instance_mutex_handle = handle
    return True


def release_single_instance():
    global _instance_mutex_handle
    handle = _instance_mutex_handle
    _instance_mutex_handle = None
    if not handle:
        return
    kernel32.ReleaseMutex(handle)
    kernel32.CloseHandle(handle)

# --- Desktop widget: embed window into the desktop layer ---
def find_desktop_worker_w():
    """Find a dedicated WorkerW wallpaper host behind desktop icons."""
    progman = user32.FindWindowW("Progman", None)
    if not progman:
        return None

    # Send Progman a 0x052C message to spawn a WorkerW behind the icons
    result = ctypes.c_size_t(0)
    user32.SendMessageTimeoutW(progman, 0x052C, 0, 0, 0x0000, 1000, ctypes.byref(result))

    worker_w = None

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def enum_callback(hwnd, lparam):
        nonlocal worker_w
        shell_view = user32.FindWindowExW(hwnd, 0, "SHELLDLL_DefView", None)
        if shell_view:
            if hwnd == progman:
                # Parenting an external Tk window directly to Progman makes
                # Explorer destroy it during shell restart. Keep searching and
                # let the caller use its independent HWND_BOTTOM fallback.
                return True
            # Older Explorer layouts place the icon view under a WorkerW; the
            # following WorkerW is the dedicated wallpaper host behind it.
            worker_w = user32.FindWindowExW(0, hwnd, "WorkerW", None)
            if worker_w:
                return False
        return True

    user32.EnumWindows(enum_callback, 0)
    return worker_w


def embed_in_desktop(hwnd):
    """Make the tkinter window a child of the desktop WorkerW layer."""
    worker_w = find_desktop_worker_w()
    if not worker_w or not _set_parent_verified(hwnd, worker_w):
        return False
    # When Progman owns SHELLDLL_DefView, a newly parented child otherwise may
    # sit above desktop icons. Keep the overlay at the bottom of the host's
    # child z-order; a dedicated wallpaper WorkerW is safe with the same rule.
    if not user32.SetWindowPos(
        hwnd,
        HWND_BOTTOM,
        0,
        0,
        0,
        0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
    ):
        # A failed z-order change does not undo SetParent. Keep tracking a
        # retained host so Peek/Topmost still detach the window before moving it.
        log.warning("Failed to lower overlay within its desktop host")
        return bool(user32.IsWindow(worker_w) and user32.GetParent(hwnd) == worker_w)
    return True


def _set_parent_verified(hwnd, expected_parent):
    """Set a native parent and verify the postcondition; SetParent's NULL is ambiguous."""
    if not hwnd or not user32.IsWindow(hwnd):
        return False
    if expected_parent and not user32.IsWindow(expected_parent):
        return False
    original_style = user32.GetWindowLongW(hwnd, GWL_STYLE) & 0xFFFFFFFF
    if expected_parent:
        child_style = (original_style | WS_CHILD) & ~WS_POPUP
        user32.SetWindowLongW(hwnd, GWL_STYLE, ctypes.c_long(child_style).value)
    user32.SetParent(hwnd, expected_parent)
    actual_parent = int(user32.GetParent(hwnd) or 0)
    expected_parent = int(expected_parent or 0)
    if actual_parent != expected_parent:
        if expected_parent:
            user32.SetWindowLongW(hwnd, GWL_STYLE, ctypes.c_long(original_style).value)
        log.warning(
            "SetParent postcondition failed for hwnd=%s: expected=%s actual=%s",
            hwnd,
            expected_parent,
            actual_parent,
        )
        return False
    if not expected_parent:
        popup_style = (original_style | WS_POPUP) & ~WS_CHILD
        user32.SetWindowLongW(hwnd, GWL_STYLE, ctypes.c_long(popup_style).value)
    user32.SetWindowPos(
        hwnd,
        0,
        0,
        0,
        0,
        0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
    )
    return True


def set_tool_window(hwnd):
    """Remove from taskbar and alt-tab, make non-activating."""
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    style |= WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
    # Windows' desktop preview must not fade this desktop widget away.
    enabled = ctypes.wintypes.BOOL(True)
    result = dwmapi.DwmSetWindowAttribute(
        hwnd, DWMWA_EXCLUDED_FROM_PEEK, ctypes.byref(enabled), ctypes.sizeof(enabled),
    )
    if result < 0:
        log.debug("Desktop Peek exclusion unavailable: HRESULT=%s", result)


def _window_has_class(hwnd, classes):
    """Recognize shell children too, without relying on translated titles."""
    name = ctypes.create_unicode_buffer(256)
    for _ in range(20):
        if not hwnd:
            break
        name.value = ""
        user32.GetClassName(hwnd, name, len(name))
        if name.value in classes:
            return True
        parent = user32.GetAncestor(hwnd, GA_PARENT)
        if parent == hwnd:
            break
        hwnd = parent
    return False


def _find_desktop_surface():
    """Find the shell icon host; it is a z-order reference, never our parent."""
    progman = user32.FindWindowW("Progman", None)
    if progman and user32.FindWindowExW(progman, 0, "SHELLDLL_DefView", None):
        return progman
    found = None

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def callback(hwnd, _lparam):
        nonlocal found
        if user32.FindWindowExW(hwnd, 0, "SHELLDLL_DefView", None):
            found = hwnd
            return False
        return True

    user32.EnumWindows(callback, 0)
    return found


def _position_above_desktop(hwnd):
    """Keep the independent widget above wallpaper but below application windows."""
    desktop = _find_desktop_surface()
    if not desktop:
        return False
    preceding = user32.GetWindow(desktop, GW_HWNDPREV)
    if preceding == hwnd:
        return True
    # Inserting after a topmost HWND would also promote this window to topmost.
    # HWND_TOP preserves its normal band when the desktop has no normal predecessor.
    if preceding and user32.GetWindowLongW(preceding, GWL_EXSTYLE) & WS_EX_TOPMOST:
        preceding = HWND_TOP
    return bool(user32.SetWindowPos(
        hwnd, preceding or HWND_TOP, 0, 0, 0, 0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
    ))


def _show_error_message(title, message):
    try:
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)
    except Exception:
        log.warning("Failed to show error message: %s - %s", title, message, exc_info=True)


def _show_info_message(title, message):
    try:
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x40)
    except Exception:
        log.warning("Failed to show information message: %s - %s", title, message, exc_info=True)


# --- Autostart management (least-privilege task; launcher requests UAC) ---
_CREATE_NO_WINDOW = 0x08000000
AUTOSTART_TASK = "HWMonitorOverlay"
AUTOSTART_SCHEMA_VERSION = "2"
AUTOSTART_SOURCE = "HeatMap"
AUTOSTART_DELAY = "PT30S"
AUTOSTART_EXECUTION_TIME_LIMIT = "PT0S"
RUN_AS_ADMIN_PATH = os.path.join(APP_DIR, "run_as_admin.bat")
# Legacy registry key — cleaned up when switching to Task Scheduler
_LEGACY_REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_TASK_XML_DECL_RE = re.compile(r"^\s*<\?xml[^>]*\?>", re.IGNORECASE)
_POWERSHELL_XML_ESCAPE_RE = re.compile(r"_x([0-9a-fA-F]{4})_")
_TASK_QUERY_MAX_ATTEMPTS = 3
_TASK_QUERY_RETRY_SECONDS = 0.25
_TRANSIENT_TASK_HRESULTS = ("0x800706ba", "0x800706be")

AUTOSTART_ABSENT = "absent"
AUTOSTART_SAFE_CURRENT = "safe_current"
AUTOSTART_LEGACY_UNSAFE = "legacy_unsafe"
AUTOSTART_STALE_HEATMAP = "stale_heatmap"
AUTOSTART_COLLISION = "collision"


@dataclass(frozen=True)
class AutostartTaskDefinition:
    source: str = ""
    version: str = ""
    uri: str = ""
    enabled: str = ""
    principal_count: int = 0
    principal_id: str = ""
    principal_user_id: str = ""
    logon_type: str = ""
    run_level: str = ""
    total_trigger_count: int = 0
    logon_trigger_count: int = 0
    trigger_user_id: str = ""
    trigger_delay: str = ""
    trigger_enabled: str = ""
    multiple_instances_policy: str = ""
    disallow_start_on_batteries: str = ""
    stop_on_batteries: str = ""
    start_when_available: str = ""
    run_only_if_network_available: str = ""
    allow_start_on_demand: str = ""
    allow_hard_terminate: str = ""
    hidden: str = ""
    wake_to_run: str = ""
    idle_stop_on_end: str = ""
    idle_restart: str = ""
    execution_time_limit: str = ""
    total_action_count: int = 0
    exec_action_count: int = 0
    actions_context: str = ""
    command: str = ""
    arguments: str = ""
    working_directory: str = ""


def _strip_outer_quotes(value):
    value = (value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def _normalize_path_for_compare(path):
    return os.path.normcase(os.path.abspath(_strip_outer_quotes(path)))


def _expected_autostart_action(app_dir=None, system_root=None):
    app_dir = os.path.abspath(app_dir or APP_DIR)
    system_root = system_root or _windows_directory()
    command = os.path.join(system_root, "System32", "cmd.exe")
    launcher = os.path.join(app_dir, "run_as_admin.bat")
    arguments = f'/d /s /c ""{launcher}""'
    return command, arguments, app_dir


def _windows_directory():
    buffer = ctypes.create_unicode_buffer(32768)
    length = kernel32.GetWindowsDirectoryW(buffer, len(buffer))
    if not length or length >= len(buffer):
        raise OSError("GetWindowsDirectoryW failed")
    return buffer.value


def _run_task_powershell(script, input_text=""):
    try:
        windows_dir = _windows_directory()
        powershell_path = os.path.join(
            windows_dir, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"
        )
        module_dir = os.path.join(
            windows_dir, "System32", "WindowsPowerShell", "v1.0", "Modules"
        )
        env = dict(os.environ)
        env["SystemRoot"] = windows_dir
        env["PSModulePath"] = module_dir
        encoded_script = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        return subprocess.run(
            [powershell_path, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded_script],
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=20,
            creationflags=_CREATE_NO_WINDOW,
            env=env,
        ), None
    except Exception as e:
        return None, str(e)


_TASK_MODULE_IMPORT = (
    "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; "
    "$module=Join-Path $env:SystemRoot "
    "'System32\\WindowsPowerShell\\v1.0\\Modules\\ScheduledTasks\\ScheduledTasks.psd1'; "
    "Import-Module -Name $module -Force; "
)


def _task_query_script():
    return (
        _TASK_MODULE_IMPORT
        + "$tasks=@(Get-ScheduledTask -ErrorAction Stop | Where-Object { "
        + f"$_.TaskName -eq '{AUTOSTART_TASK}' -and $_.TaskPath -eq '\\'"
        + " }); if($tasks.Count -eq 0){exit 3}; "
        + "if($tasks.Count -ne 1){throw 'duplicate root task definitions'}; "
        + "$OutputEncoding=[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
        + f"Export-ScheduledTask -TaskName '{AUTOSTART_TASK}' -TaskPath '\\' -ErrorAction Stop"
    )


def _decode_output(data):
    if not data:
        return ""
    if isinstance(data, str):
        return data
    for encoding in (locale.getpreferredencoding(False), "utf-8", "utf-16"):
        try:
            return data.decode(encoding)
        except UnicodeError:
            continue
    return data.decode(errors="replace")


def _decode_powershell_clixml(data):
    text = _decode_output(data).strip()
    lines = text.splitlines()
    if not lines or lines[0].strip() != "#< CLIXML":
        return text
    try:
        root = ET.fromstring("\n".join(lines[1:]))
    except ET.ParseError:
        return text

    errors = []
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "S" or element.get("S") != "Error":
            continue
        value = _POWERSHELL_XML_ESCAPE_RE.sub(
            lambda match: chr(int(match.group(1), 16)),
            element.text or "",
        )
        errors.append(value)
    return "".join(errors).strip() or text


def _completed_process_message(result):
    stdout = _decode_powershell_clixml(getattr(result, "stdout", b""))
    stderr = _decode_powershell_clixml(getattr(result, "stderr", b""))
    detail = stderr or stdout or "no output"
    return f"exit code {result.returncode}: {detail}"


def _is_transient_task_query_failure(result):
    message = _completed_process_message(result).casefold()
    return any(hresult in message for hresult in _TRANSIENT_TASK_HRESULTS)


def _decode_task_xml(xml_data):
    if isinstance(xml_data, str):
        text = xml_data
    else:
        last_error = None
        if b"\x00" in xml_data[:120]:
            encodings = ("utf-16", "utf-8-sig", locale.getpreferredencoding(False))
        else:
            encodings = ("utf-8-sig", locale.getpreferredencoding(False), "utf-16")
        for encoding in encodings:
            try:
                text = xml_data.decode(encoding)
                break
            except UnicodeError as e:
                last_error = e
        else:
            raise ValueError(f"could not decode task XML: {last_error}")
    return _TASK_XML_DECL_RE.sub("", text, count=1).lstrip("\ufeff")


def _local_xml_name(tag):
    return tag.rsplit("}", 1)[-1]


def _direct_child(parent, name):
    if parent is None:
        return None
    for child in parent:
        if _local_xml_name(child.tag) == name:
            return child
    return None


def _first_element(root, name):
    for element in root.iter():
        if _local_xml_name(element.tag) == name:
            return element
    return None


def _element_text(parent, name, default=""):
    element = _direct_child(parent, name)
    if element is None or element.text is None:
        return default
    return element.text.strip()


def _parse_autostart_task_xml(xml_data):
    try:
        root = ET.fromstring(_decode_task_xml(xml_data))
    except Exception as e:
        raise ValueError(f"could not parse task XML: {e}") from e

    registration = _first_element(root, "RegistrationInfo")
    principals_element = _first_element(root, "Principals")
    principal_elements = list(principals_element) if principals_element is not None else []
    principals = [
        element for element in principal_elements
        if _local_xml_name(element.tag) == "Principal"
    ]
    principal = principals[0] if len(principals) == 1 else None
    settings = _first_element(root, "Settings")
    triggers_element = _first_element(root, "Triggers")
    trigger_elements = list(triggers_element) if triggers_element is not None else []
    logon_triggers = [
        element for element in trigger_elements
        if _local_xml_name(element.tag) == "LogonTrigger"
    ]
    actions_element = _first_element(root, "Actions")
    action_elements = list(actions_element) if actions_element is not None else []
    exec_elements = [
        element for element in action_elements
        if _local_xml_name(element.tag) == "Exec"
    ]
    exec_element = exec_elements[0] if exec_elements else None
    if exec_element is None or _direct_child(exec_element, "Command") is None:
        raise ValueError("task XML does not contain Exec/Command")
    trigger = logon_triggers[0] if len(logon_triggers) == 1 else None
    idle_settings = _direct_child(settings, "IdleSettings")
    return AutostartTaskDefinition(
        source=_element_text(registration, "Source"),
        version=_element_text(registration, "Version"),
        uri=_element_text(registration, "URI"),
        enabled=_element_text(settings, "Enabled"),
        principal_count=len(principals),
        principal_id=(principal.get("id", "").strip() if principal is not None else ""),
        principal_user_id=_element_text(principal, "UserId"),
        logon_type=_element_text(principal, "LogonType"),
        run_level=_element_text(principal, "RunLevel"),
        total_trigger_count=len(trigger_elements),
        logon_trigger_count=len(logon_triggers),
        trigger_user_id=_element_text(trigger, "UserId"),
        trigger_delay=_element_text(trigger, "Delay"),
        trigger_enabled=_element_text(trigger, "Enabled"),
        multiple_instances_policy=_element_text(settings, "MultipleInstancesPolicy"),
        disallow_start_on_batteries=_element_text(settings, "DisallowStartIfOnBatteries"),
        stop_on_batteries=_element_text(settings, "StopIfGoingOnBatteries"),
        start_when_available=_element_text(settings, "StartWhenAvailable"),
        run_only_if_network_available=_element_text(settings, "RunOnlyIfNetworkAvailable"),
        allow_start_on_demand=_element_text(settings, "AllowStartOnDemand"),
        allow_hard_terminate=_element_text(settings, "AllowHardTerminate"),
        hidden=_element_text(settings, "Hidden"),
        wake_to_run=_element_text(settings, "WakeToRun"),
        idle_stop_on_end=_element_text(idle_settings, "StopOnIdleEnd"),
        idle_restart=_element_text(idle_settings, "RestartOnIdle"),
        execution_time_limit=_element_text(settings, "ExecutionTimeLimit"),
        total_action_count=len(action_elements),
        exec_action_count=len(exec_elements),
        actions_context=(actions_element.get("Context", "").strip() if actions_element is not None else ""),
        command=_element_text(exec_element, "Command"),
        arguments=_element_text(exec_element, "Arguments"),
        working_directory=_element_text(exec_element, "WorkingDirectory"),
    )


def _xml_tag(name):
    return f"{{http://schemas.microsoft.com/windows/2004/02/mit/task}}{name}"


def _add_xml_text(parent, name, value):
    element = ET.SubElement(parent, _xml_tag(name))
    element.text = str(value)
    return element


def _build_autostart_task_xml(user_id, app_dir=None, system_root=None):
    command, arguments, working_directory = _expected_autostart_action(app_dir, system_root)
    ET.register_namespace("", "http://schemas.microsoft.com/windows/2004/02/mit/task")
    task = ET.Element(_xml_tag("Task"), {"version": "1.2"})
    registration = ET.SubElement(task, _xml_tag("RegistrationInfo"))
    _add_xml_text(registration, "Source", AUTOSTART_SOURCE)
    _add_xml_text(registration, "Version", AUTOSTART_SCHEMA_VERSION)
    _add_xml_text(registration, "Description", "HeatMap launcher; requests UAC for sensor access.")
    _add_xml_text(registration, "URI", f"\\{AUTOSTART_TASK}")

    triggers = ET.SubElement(task, _xml_tag("Triggers"))
    trigger = ET.SubElement(triggers, _xml_tag("LogonTrigger"))
    _add_xml_text(trigger, "Enabled", "true")
    _add_xml_text(trigger, "UserId", user_id)
    _add_xml_text(trigger, "Delay", AUTOSTART_DELAY)

    principals = ET.SubElement(task, _xml_tag("Principals"))
    principal = ET.SubElement(principals, _xml_tag("Principal"), {"id": "Author"})
    _add_xml_text(principal, "UserId", user_id)
    _add_xml_text(principal, "LogonType", "InteractiveToken")
    _add_xml_text(principal, "RunLevel", "LeastPrivilege")

    settings = ET.SubElement(task, _xml_tag("Settings"))
    for name, value in (
        ("MultipleInstancesPolicy", "IgnoreNew"),
        ("DisallowStartIfOnBatteries", "false"),
        ("StopIfGoingOnBatteries", "false"),
        ("StartWhenAvailable", "true"),
        ("RunOnlyIfNetworkAvailable", "false"),
        ("AllowStartOnDemand", "true"),
        ("AllowHardTerminate", "true"),
        ("Enabled", "true"),
        ("Hidden", "false"),
        ("WakeToRun", "false"),
        ("ExecutionTimeLimit", AUTOSTART_EXECUTION_TIME_LIMIT),
    ):
        _add_xml_text(settings, name, value)
    idle = ET.SubElement(settings, _xml_tag("IdleSettings"))
    _add_xml_text(idle, "StopOnIdleEnd", "false")
    _add_xml_text(idle, "RestartOnIdle", "false")

    actions = ET.SubElement(task, _xml_tag("Actions"), {"Context": "Author"})
    exec_element = ET.SubElement(actions, _xml_tag("Exec"))
    _add_xml_text(exec_element, "Command", command)
    _add_xml_text(exec_element, "Arguments", arguments)
    _add_xml_text(exec_element, "WorkingDirectory", working_directory)
    return ET.tostring(task, encoding="utf-16", xml_declaration=True)


def _normalized_identity(value):
    return (value or "").strip().casefold()


def _is_legacy_elevated_autostart(definition):
    if definition.run_level.casefold() != "highestavailable":
        return False
    command_name = os.path.basename(_strip_outer_quotes(definition.command)).casefold()
    script_name = os.path.basename(_strip_outer_quotes(definition.arguments)).casefold()
    return command_name in ("python.exe", "pythonw.exe") and script_name == "overlay.py"


def _classify_autostart_task(
    definition,
    user_id,
    app_dir=None,
    system_root=None,
    accepted_trigger_user_ids=None,
    task_name=AUTOSTART_TASK,
):
    if definition is None:
        return AUTOSTART_ABSENT
    if _is_legacy_elevated_autostart(definition):
        return AUTOSTART_LEGACY_UNSAFE
    expected_command, expected_arguments, expected_working_directory = _expected_autostart_action(
        app_dir, system_root
    )
    accepted_trigger_user_ids = accepted_trigger_user_ids or (user_id,)
    safe = (
        definition.source == AUTOSTART_SOURCE
        and definition.version == AUTOSTART_SCHEMA_VERSION
        and definition.uri == f"\\{task_name}"
        and definition.enabled.casefold() in ("", "true")
        and definition.principal_count == 1
        and definition.principal_id == "Author"
        and _normalized_identity(definition.principal_user_id) == _normalized_identity(user_id)
        and definition.logon_type == "InteractiveToken"
        and definition.run_level in ("", "LeastPrivilege")
        and definition.total_trigger_count == 1
        and definition.logon_trigger_count == 1
        and any(
            _normalized_identity(definition.trigger_user_id) == _normalized_identity(identity)
            for identity in accepted_trigger_user_ids
        )
        and definition.trigger_delay == AUTOSTART_DELAY
        and definition.trigger_enabled.casefold() in ("", "true")
        and definition.multiple_instances_policy == "IgnoreNew"
        and definition.disallow_start_on_batteries.casefold() == "false"
        and definition.stop_on_batteries.casefold() == "false"
        and definition.start_when_available.casefold() == "true"
        and definition.run_only_if_network_available.casefold() in ("", "false")
        and definition.allow_start_on_demand.casefold() in ("", "true")
        and definition.allow_hard_terminate.casefold() in ("", "true")
        and definition.hidden.casefold() in ("", "false")
        and definition.wake_to_run.casefold() in ("", "false")
        and definition.idle_stop_on_end.casefold() in ("", "false")
        and definition.idle_restart.casefold() in ("", "false")
        and definition.execution_time_limit == AUTOSTART_EXECUTION_TIME_LIMIT
        and definition.total_action_count == 1
        and definition.exec_action_count == 1
        and definition.actions_context == "Author"
        and _normalize_path_for_compare(definition.command) == _normalize_path_for_compare(expected_command)
        and definition.arguments == expected_arguments
        and _normalize_path_for_compare(definition.working_directory)
        == _normalize_path_for_compare(expected_working_directory)
    )
    if safe:
        return AUTOSTART_SAFE_CURRENT
    if definition.source == AUTOSTART_SOURCE:
        return AUTOSTART_STALE_HEATMAP
    return AUTOSTART_COLLISION


def _query_autostart_task_definition():
    for attempt in range(_TASK_QUERY_MAX_ATTEMPTS):
        result, error = _run_task_powershell(_task_query_script())
        if error:
            return None, f"failed to query task: {error}"
        if result.returncode == 3:
            return None, None
        if result.returncode != 0:
            if (
                attempt + 1 < _TASK_QUERY_MAX_ATTEMPTS
                and _is_transient_task_query_failure(result)
            ):
                time.sleep(_TASK_QUERY_RETRY_SECONDS * (attempt + 1))
                continue
            return None, _completed_process_message(result)
        try:
            definition = _parse_autostart_task_xml(result.stdout)
        except ValueError as e:
            return None, str(e)
        return definition, None
    return None, "task query retry limit reached"


def _current_user_identity():
    try:
        result = subprocess.run(
            [os.path.join(_windows_directory(), "System32", "whoami.exe"), "/user", "/fo", "csv", "/nh"],
            capture_output=True,
            timeout=5,
            creationflags=_CREATE_NO_WINDOW,
        )
        if result.returncode == 0:
            rows = list(csv.reader(_decode_output(result.stdout).splitlines()))
            if rows and len(rows[0]) >= 2 and rows[0][1].strip():
                return rows[0][0].strip(), rows[0][1].strip()
    except Exception:
        log.debug("Failed to resolve current Windows identity with whoami", exc_info=True)
    domain = os.environ.get("USERDOMAIN", "").strip()
    username = os.environ.get("USERNAME", "").strip()
    if username:
        account = f"{domain}\\{username}" if domain else username
        return account, None
    return None, None


def _current_user_id():
    return _current_user_identity()[1]


def _current_user_account():
    return _current_user_identity()[0]


def _wts_session_text(session_id, info_class):
    buffer = ctypes.c_void_p()
    byte_count = ctypes.wintypes.DWORD()
    if not wtsapi32.WTSQuerySessionInformationW(
        0, session_id, info_class, ctypes.byref(buffer), ctypes.byref(byte_count)
    ):
        return ""
    try:
        return ctypes.wstring_at(buffer.value).strip() if buffer.value else ""
    finally:
        if buffer.value:
            wtsapi32.WTSFreeMemory(buffer)


def _interactive_user_account():
    session_id = ctypes.wintypes.DWORD()
    if not kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session_id)):
        return None
    username = _wts_session_text(session_id.value, 5)  # WTSUserName
    domain = _wts_session_text(session_id.value, 7)  # WTSDomainName
    if not username:
        return None
    return f"{domain}\\{username}" if domain else username


def _resolve_autostart_identity():
    user_id = _current_user_id()
    token_account = _current_user_account()
    interactive_account = _interactive_user_account()
    if not user_id or not token_account or not interactive_account:
        return None, (), "could not determine the elevated and interactive Windows identities"
    if _normalized_identity(token_account) != _normalized_identity(interactive_account):
        return (
            None,
            (),
            "HeatMap was elevated with different administrator credentials; "
            "autostart must be configured by the same interactive Windows user",
        )
    return user_id, (user_id, token_account, interactive_account), None


def is_autostart_enabled():
    """Return true only for the exact least-privilege HeatMap task contract."""
    user_id, accepted_identities, identity_error = _resolve_autostart_identity()
    if identity_error:
        log.debug("Autostart identity check failed: %s", identity_error)
        return False
    definition, error = _query_autostart_task_definition()
    if error:
        log.debug("Autostart task is not enabled for current app: %s", error)
        return False
    return _classify_autostart_task(
        definition,
        user_id,
        accepted_trigger_user_ids=accepted_identities,
    ) == AUTOSTART_SAFE_CURRENT


def _delete_legacy_autostart_value():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _LEGACY_REG_KEY, 0, winreg.KEY_SET_VALUE) as key:
            try:
                winreg.DeleteValue(key, AUTOSTART_TASK)
            except FileNotFoundError:
                return True, "legacy registry entry not present"
    except FileNotFoundError:
        return True, "legacy registry key not present"
    except OSError as e:
        return False, f"failed to remove legacy registry entry: {e}"
    return True, "legacy registry entry removed"


def _delete_autostart_task():
    script = (
        _TASK_MODULE_IMPORT
        + f"Unregister-ScheduledTask -TaskName '{AUTOSTART_TASK}' -TaskPath '\\' "
        + "-Confirm:$false -ErrorAction Stop"
    )
    result, error = _run_task_powershell(script)
    if error:
        return False, error
    if result.returncode != 0:
        return False, _completed_process_message(result)
    return True, "task deleted"


def _disable_autostart_task():
    script = (
        _TASK_MODULE_IMPORT
        + f"Disable-ScheduledTask -TaskName '{AUTOSTART_TASK}' -TaskPath '\\' "
        + "-ErrorAction Stop | Out-Null"
    )
    result, error = _run_task_powershell(script)
    if error:
        return False, error
    if result.returncode != 0:
        return False, _completed_process_message(result)
    return True, "task disabled"


def _register_autostart_xml(xml_bytes):
    script = (
        _TASK_MODULE_IMPORT
        + "$payload=[Console]::In.ReadToEnd(); "
        + "$xml=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($payload)); "
        + f"Register-ScheduledTask -TaskName '{AUTOSTART_TASK}' -TaskPath '\\' "
        + "-Xml $xml -Force -ErrorAction Stop | Out-Null"
    )
    xml_text = _decode_task_xml(xml_bytes)
    payload = base64.b64encode(xml_text.encode("utf-8")).decode("ascii")
    result, error = _run_task_powershell(script, input_text=payload)
    if error:
        return False, error
    if result.returncode != 0:
        return False, _completed_process_message(result)
    return True, "task registered"


def _remove_autostart_task_fail_closed():
    existing, error = _query_autostart_task_definition()
    if error:
        return False, f"could not inspect task before removal: {error}"
    if existing is None:
        return True, "task already absent"
    disabled, message = _disable_autostart_task()
    if not disabled:
        return False, f"failed to disable task before removal: {message}"
    definition, error = _query_autostart_task_definition()
    if error:
        return False, f"could not verify disabled task: {error}"
    if definition is None:
        return True, "task disappeared after disable"
    if definition.enabled.casefold() != "false":
        return False, "task did not report disabled state; refusing to delete it"
    deleted, message = _delete_autostart_task()
    if not deleted:
        return False, f"disabled task could not be deleted: {message}"
    remaining, error = _query_autostart_task_definition()
    if error:
        return False, f"could not verify task deletion: {error}"
    if remaining is not None:
        return False, "task still exists after deletion"
    return True, "task disabled, deleted, and verified absent"


def enable_autostart():
    """Create a least-privilege task; run_as_admin.bat requests UAC interactively."""
    if "%" in APP_DIR:
        return False, "autostart is unavailable from a checkout path containing '%'"
    user_id, accepted_identities, identity_error = _resolve_autostart_identity()
    if identity_error:
        return False, identity_error
    existing, error = _query_autostart_task_definition()
    if error:
        return False, error
    classification = _classify_autostart_task(
        existing,
        user_id,
        accepted_trigger_user_ids=accepted_identities,
    )
    if classification == AUTOSTART_COLLISION:
        return False, f"Task {AUTOSTART_TASK} exists but is not owned by HeatMap"
    if classification == AUTOSTART_SAFE_CURRENT:
        return True, "Autostart already enabled"

    xml_bytes = _build_autostart_task_xml(user_id)
    if existing is not None:
        deleted, message = _remove_autostart_task_fail_closed()
        if not deleted:
            return False, f"failed to remove unsafe/stale task: {message}"

    registered, message = _register_autostart_xml(xml_bytes)
    if not registered:
        return False, f"failed to create safe autostart task: {message}"

    created, error = _query_autostart_task_definition()
    created_classification = _classify_autostart_task(
        created,
        user_id,
        accepted_trigger_user_ids=accepted_identities,
    ) if not error else None
    if error or created_classification != AUTOSTART_SAFE_CURRENT:
        _cleanup_ok, cleanup_message = _remove_autostart_task_fail_closed()
        return False, (
            f"created task failed security validation: {error or 'definition mismatch'}; "
            f"cleanup: {cleanup_message}"
        )

    ok, message = _delete_legacy_autostart_value()
    if not ok:
        log.warning(message)
    return True, "Autostart enabled; Windows will request UAC at logon"


def disable_autostart():
    """Remove the Task Scheduler task."""
    user_id, accepted_identities, identity_error = _resolve_autostart_identity()
    if identity_error:
        return False, identity_error
    definition, error = _query_autostart_task_definition()
    if error:
        return False, error
    if definition is not None:
        classification = _classify_autostart_task(
            definition,
            user_id,
            accepted_trigger_user_ids=accepted_identities,
        )
        if classification == AUTOSTART_COLLISION:
            return False, f"Task {AUTOSTART_TASK} is not owned by HeatMap"
        deleted, message = _remove_autostart_task_fail_closed()
        if not deleted:
            return False, message

    ok, message = _delete_legacy_autostart_value()
    if not ok:
        log.warning(message)
        return False, message
    return True, "Autostart disabled"


@dataclass(frozen=True)
class AutostartReconcileResult:
    changed: bool
    ok: bool
    message: str
    enabled: bool | None


def reconcile_autostart_security():
    """Fail-closed migration of legacy HighestAvailable HeatMap tasks."""
    user_id, accepted_identities, identity_error = _resolve_autostart_identity()
    if identity_error:
        return AutostartReconcileResult(False, False, identity_error, None)
    definition, error = _query_autostart_task_definition()
    if error or definition is None:
        return AutostartReconcileResult(False, not error, error or "no task", None if error else False)
    classification = _classify_autostart_task(
        definition,
        user_id,
        accepted_trigger_user_ids=accepted_identities,
    )
    if classification == AUTOSTART_SAFE_CURRENT:
        return AutostartReconcileResult(False, True, "safe task already current", True)
    if classification in (AUTOSTART_LEGACY_UNSAFE, AUTOSTART_STALE_HEATMAP):
        ok, message = enable_autostart()
        return AutostartReconcileResult(True, ok, message, True if ok else None)
    return AutostartReconcileResult(
        False, False, f"Task {AUTOSTART_TASK} name collision requires manual review", None
    )


def _format_autostart_reconcile_error(changed, message):
    if changed:
        return (
            "The old elevated autostart task could not be migrated and may be disabled.\n\n"
            f"{message}\n\nOpen HeatMap and toggle Autostart after resolving the error."
        )
    return (
        "HeatMap could not verify autostart security.\n\n"
        f"{message}\n\nNo autostart task was changed. "
        "Open HeatMap and toggle Autostart later to retry."
    )


# --- Load LibreHardwareMonitor ---
def _check_lhm_cpu_temperature(computer, HardwareType, SensorType):
    """Best-effort warning for blocked LHM driver; never disables an opened computer."""
    try:
        hardware_items = list(computer.Hardware)
    except Exception:
        log.warning("Could not enumerate hardware during LHM sanity check", exc_info=True)
        return

    checked_cpu = False
    has_cpu_temp = False
    for hw in hardware_items:
        try:
            if hw.HardwareType != HardwareType.Cpu:
                continue
            hw.Update()
            checked_cpu = True
            for sensor in _iter_hardware_sensors(hw, include_subhardware=True):
                if sensor.SensorType == SensorType.Temperature and sensor.Value is not None:
                    if float(sensor.Value) > 0:
                        has_cpu_temp = True
                        break
            break
        except Exception:
            log.warning("Skipping hardware block during LHM sanity check: %s", _hardware_label(hw), exc_info=True)

    if checked_cpu and not has_cpu_temp:
        log.warning(
            "LHM kernel driver may be blocked (CPU temp unavailable). "
            "Check Windows Vulnerable Driver Blocklist "
            "(HKLM\\SYSTEM\\CurrentControlSet\\Control\\CI\\Config"
            "\\VulnerableDriverBlocklistEnable)."
        )


def _close_hardware_monitor(computer):
    if computer is not None:
        try:
            computer.Close()
        except Exception:
            log.debug("Failed to close hardware monitor", exc_info=True)


def init_hardware_monitor():
    """Initialize LibreHardwareMonitor via pythonnet."""
    computer = None
    try:
        import clr  # pythonnet
        dll_path = os.path.join(LIB_DIR, "LibreHardwareMonitorLib.dll")
        if not os.path.exists(dll_path):
            return None
        clr.AddReference(dll_path)
        from LibreHardwareMonitor.Hardware import Computer

        computer = Computer()
        computer.IsCpuEnabled = True
        computer.IsGpuEnabled = True
        computer.IsStorageEnabled = True
        computer.IsMemoryEnabled = True
        computer.IsMotherboardEnabled = True
        computer.Open()

        from LibreHardwareMonitor.Hardware import HardwareType, SensorType
        _check_lhm_cpu_temperature(computer, HardwareType, SensorType)

        return computer
    except Exception:
        log.warning("Failed to init LibreHardwareMonitor, falling back to psutil", exc_info=True)
        _close_hardware_monitor(computer)
        return None


def _safe_round(value, minimum=None, maximum=None):
    """Validate raw sensor bounds before rounding so invalid values cannot round into range."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(v):
        return None
    if minimum is not None and v < minimum:
        return None
    if maximum is not None and v > maximum:
        return None
    return round(v)


def _safe_percentage(value):
    return _safe_round(value, minimum=0, maximum=100)


_MAX_VALID_HARDWARE_TEMP_C = 150


def _safe_temperature(value):
    """Reject common unavailable/sentinel temperatures before they reach UI policy."""
    rounded = _safe_round(value)
    if rounded is None or rounded <= 0 or rounded > _MAX_VALID_HARDWARE_TEMP_C:
        return None
    return rounded


def _empty_sensor_data():
    return {
        "cpu_temp": None,
        "cpu_load": None,
        "cpu_clock": None,
        "gpu_temp": None,
        "gpu_temp_label": None,
        "gpu_core_temp": None,
        "gpu_hotspot_temp": None,
        "gpu_memory_temp": None,
        "gpu_load": None,
        "gpu_clock": None,
        "cpu_fan": None,
        "cpu_fan_pct": None,
        "cpu_optional_fan": None,
        "fans": [],
        "gpu_fan": None,
        "gpu_fan_pct": None,
        "gpu_vram_pct": None,
        "gpu_vram_used_gb": None,
        "gpu_vram_total_gb": None,
        "ram_pct": None,
        "ram_used_gb": None,
        "ram_total_gb": None,
        "motherboard_temps": [],
        "disks": [],
    }


def _empty_peak_data():
    return {
        "cpu_temp": None,
        "gpu_temp": None,
        "gpu_temp_label": None,
        "gpu_hotspot_temp": None,
        "gpu_memory_temp": None,
        "ram_pct": None,
        "disk_temp": None,
        "disk_used_pct": None,
    }


def _apply_psutil_fallbacks(data):
    if data["cpu_load"] is None:
        data["cpu_load"] = round(psutil.cpu_percent(interval=0))
    vm = psutil.virtual_memory()
    if data["ram_pct"] is None:
        data["ram_pct"] = round(vm.percent)
    data["ram_used_gb"] = round(vm.used / (1024 ** 3), 1)
    data["ram_total_gb"] = round(vm.total / (1024 ** 3), 1)
    return data


def _hardware_label(hw):
    try:
        name = str(getattr(hw, "Name", "<unknown>"))
    except Exception:
        name = "<unknown>"
    try:
        hw_type = str(getattr(hw, "HardwareType", "<unknown>"))
    except Exception:
        hw_type = "<unknown>"
    return f"{name} ({hw_type})"


def _iter_hardware_sensors(hw, include_subhardware=False):
    for sensor in getattr(hw, "Sensors", ()):
        yield sensor
    if include_subhardware:
        for sub in getattr(hw, "SubHardware", ()):
            for sensor in getattr(sub, "Sensors", ()):
                yield sensor


def _fan_number(name):
    match = re.search(r"#\s*(\d+)|fan\s*#?\s*(\d+)", name)
    if not match:
        return None
    return match.group(1) or match.group(2)


def _is_primary_cpu_fan_name(name):
    return ("cpu" in name or "processor" in name) and "optional" not in name


def _select_cpu_fan(fan_sensors):
    if not fan_sensors:
        return None
    for name, val in fan_sensors:
        if _is_primary_cpu_fan_name(name):
            return name, val
    # Unknown Fan #1/System Fan #1 cannot be assumed to be a CPU cooler.
    return None


def _select_cpu_fan_control(control_sensors, fan_name, has_cpu_fan):
    if not control_sensors or not has_cpu_fan:
        return None
    fan_idx = _fan_number(fan_name or "")
    for name, val in control_sensors:
        if _is_primary_cpu_fan_name(name) and (
            fan_idx is None or _fan_number(name) in (None, fan_idx)
        ):
            return val
    # Numbered system controls must not be presented as CPU duty (particularly
    # when the CPU itself uses automatic mode and has no readable percentage).
    return None


def _normalized_sensor_name(name):
    return re.sub(r"\s+", " ", str(name).replace("_", " ").lower()).strip()


def _is_gpu_load_sensor(name):
    name = _normalized_sensor_name(name)
    if "memory" in name or "bus" in name or "video" in name:
        return False
    return (
        name in ("gpu core", "gpu load", "gpu d3d", "d3d", "d3d 3d")
        or ("d3d" in name and ("gpu" in name or "3d" in name))
        or (name.startswith("gpu ") and ("core" in name or "load" in name or "3d" in name))
    )


def _gpu_load_priority(name):
    name = _normalized_sensor_name(name)
    if not _is_gpu_load_sensor(name):
        return None
    if "d3d" in name:
        return 10
    if name in ("gpu core", "gpu load"):
        return 30
    return 20


def _gpu_temperature_key(name):
    name = _normalized_sensor_name(name)
    # WHY: LHM also exports GPU VR VDDC/MVDD/SoC, Liquid and PLX.
    # A generic "gpu" match silently overwrites the actual die temperature.
    if not name or any(marker in name for marker in (
        "warning", "critical", "threshold", "limit", "vr ", "vrm", "liquid", "plx",
    )):
        return None
    if "memory" in name or "vram" in name:
        return "gpu_memory_temp"
    if "hotspot" in name or "hot spot" in name or "junction" in name:
        return "gpu_hotspot_temp"
    if name in ("core", "gpu core", "gpu", "gpu temperature", "temperature",
                "edge", "gpu edge", "gpu core temperature"):
        return "gpu_core_temp"
    return None


def _select_gpu_display_temperature(data):
    # SYNC: Hotspot and memory have independent rows and alert thresholds.
    data["gpu_temp"] = data.get("gpu_core_temp")
    data["gpu_temp_label"] = "CORE" if data["gpu_temp"] is not None else None


def _is_gpu_memory_used_sensor(name):
    name = _normalized_sensor_name(name)
    return "memory" in name and "used" in name and "shared" not in name


def _is_gpu_memory_total_sensor(name):
    name = _normalized_sensor_name(name)
    return "memory" in name and "total" in name and "shared" not in name


def _is_ram_load_sensor(name):
    name = _normalized_sensor_name(name)
    return name in (
        "memory",
        "memory load",
        "load memory",
        "physical memory",
        "physical memory load",
    )


def _ram_load_priority(hardware_name, sensor_name):
    sensor_name = _normalized_sensor_name(sensor_name)
    if not _is_ram_load_sensor(sensor_name):
        return None
    combined = f"{_normalized_sensor_name(hardware_name)} {sensor_name}"
    if "virtual" in combined:
        return 10
    if "physical" in combined or "total memory" in combined:
        return 30
    return 20


def _apply_ranked_sensor_candidates(data, candidates):
    cpu_temps = candidates["cpu_temp"]
    if cpu_temps:
        data["cpu_temp"] = max(cpu_temps)[-1]
    gpu_samples = candidates["gpus"]
    if gpu_samples:
        _rank, selected = max(gpu_samples, key=lambda item: item[0])
        data.update(selected)
    ram_loads = candidates["ram_pct"]
    if ram_loads:
        data["ram_pct"] = max(ram_loads)[-1]


def _is_storage_temperature_reading(name):
    name = _normalized_sensor_name(name)
    return not any(marker in name for marker in ("warning", "critical", "threshold", "limit"))


def _read_hardware_block(hw, HardwareType, SensorType, data, candidates, update_storage=True):
    hw_type = hw.HardwareType
    if hw_type == HardwareType.GpuIntel and any(
        any(sample.get(key) is not None for key in (
            "gpu_core_temp", "gpu_hotspot_temp", "gpu_memory_temp"
        )) for _rank, sample in candidates["gpus"]
    ):
        return
    is_storage = hw_type == HardwareType.Storage
    if is_storage and not update_storage:
        # Skip Update() but still read cached sensor values
        pass
    else:
        hw.Update()
        for sub in hw.SubHardware:
            sub.Update()

    if hw_type == HardwareType.Cpu:
        core_clocks = []
        for sensor in _iter_hardware_sensors(hw, include_subhardware=True):
            if sensor.SensorType == SensorType.Temperature:
                name = sensor.Name.lower()
                val = _safe_temperature(sensor.Value)
                if val is not None:
                    preferred = any(marker in name for marker in ("tctl", "tdie", "package"))
                    candidates["cpu_temp"].append((preferred, val))
            elif sensor.SensorType == SensorType.Load:
                if "total" in sensor.Name.lower():
                    val = _safe_percentage(sensor.Value)
                    if val is not None:
                        data["cpu_load"] = val
            elif sensor.SensorType == SensorType.Clock:
                if "core" in sensor.Name.lower():
                    val = _safe_round(sensor.Value, minimum=0)
                    if val is not None and val > 0:
                        core_clocks.append(val)
        if core_clocks:
            data["cpu_clock"] = round(max(core_clocks))

    elif hw_type in (HardwareType.GpuAmd, HardwareType.GpuNvidia, HardwareType.GpuIntel):
        gpu_keys = (
            "gpu_temp", "gpu_temp_label", "gpu_core_temp", "gpu_hotspot_temp",
            "gpu_memory_temp", "gpu_load", "gpu_clock", "gpu_fan", "gpu_fan_pct",
            "gpu_vram_pct", "gpu_vram_used_gb", "gpu_vram_total_gb",
        )
        gpu_sample = {key: None for key in gpu_keys}
        gpu_load_candidates = []
        gpu_mem_used = None
        gpu_mem_total = None
        sensors = list(hw.Sensors)
        if not sensors:
            data[SENSOR_STATUS_KEY] = SENSOR_STATUS_PARTIAL
            data[SENSOR_REINIT_KEY] = True
        for sensor in sensors:
            if sensor.SensorType == SensorType.Temperature:
                temp_key = _gpu_temperature_key(sensor.Name)
                if temp_key:
                    val = _safe_temperature(sensor.Value)
                    if val is not None:
                        previous = gpu_sample[temp_key]
                        gpu_sample[temp_key] = max(previous, val) if previous is not None else val
            elif sensor.SensorType == SensorType.Load:
                priority = _gpu_load_priority(sensor.Name)
                if priority is not None:
                    val = _safe_percentage(sensor.Value)
                    if val is not None:
                        gpu_load_candidates.append(
                            (priority, _normalized_sensor_name(sensor.Name), val)
                        )
            elif sensor.SensorType == SensorType.Fan:
                val = _safe_round(sensor.Value, minimum=0)
                if val is not None:
                    gpu_sample["gpu_fan"] = val
            elif sensor.SensorType == SensorType.Control:
                val = _safe_percentage(sensor.Value)
                if val is not None:
                    gpu_sample["gpu_fan_pct"] = val
            elif sensor.SensorType == SensorType.Clock:
                if "core" in sensor.Name.lower():
                    val = _safe_round(sensor.Value, minimum=0)
                    if val is not None:
                        gpu_sample["gpu_clock"] = val
            elif sensor.SensorType == SensorType.SmallData:
                if _is_gpu_memory_used_sensor(sensor.Name):
                    value = sensor.Value
                    if _safe_round(value, minimum=0) is not None:
                        gpu_mem_used = float(value)
                elif _is_gpu_memory_total_sensor(sensor.Name):
                    value = sensor.Value
                    if _safe_round(value, minimum=0) is not None:
                        gpu_mem_total = float(value)
        _select_gpu_display_temperature(gpu_sample)
        selected_load = max(gpu_load_candidates) if gpu_load_candidates else None
        if selected_load:
            gpu_sample["gpu_load"] = selected_load[-1]
        if gpu_mem_used is not None and gpu_mem_total and 0 <= gpu_mem_used <= gpu_mem_total:
            gpu_sample["gpu_vram_pct"] = round(gpu_mem_used / gpu_mem_total * 100)
            gpu_sample["gpu_vram_used_gb"] = round(gpu_mem_used / 1024, 1)
            gpu_sample["gpu_vram_total_gb"] = round(gpu_mem_total / 1024, 1)
        hardware_priority = 10 if hw_type == HardwareType.GpuIntel else 20
        # Keep device ranking independent of the primary display policy.
        temperatures = [gpu_sample[key] for key in (
            "gpu_core_temp", "gpu_hotspot_temp", "gpu_memory_temp"
        ) if gpu_sample[key] is not None]
        display_temp = max(temperatures) if temperatures else None
        load_priority = selected_load[0] if selected_load else -1
        load_value = gpu_sample["gpu_load"]
        rank = (
            display_temp is not None,
            hardware_priority,
            display_temp if display_temp is not None else -1,
            load_priority,
            load_value if load_value is not None else -1,
            _normalized_sensor_name(hw.Name),
        )
        candidates["gpus"].append((rank, gpu_sample))

    elif hw_type == HardwareType.Storage:
        disk_temp = None
        primary_temp = None
        temperature_readings = []
        disk_used = None
        disk_life = None
        for sensor in hw.Sensors:
            if sensor.SensorType == SensorType.Temperature:
                if not _is_storage_temperature_reading(sensor.Name):
                    continue
                val = _safe_temperature(sensor.Value)
                if val is not None:
                    temperature_readings.append({"name": str(sensor.Name), "temp": val})
                    if _normalized_sensor_name(sensor.Name) in (
                        "temperature", "composite", "composite temperature", "drive temperature"
                    ):
                        primary_temp = max(primary_temp, val) if primary_temp is not None else val
                if val is not None and (disk_temp is None or val > disk_temp):
                    disk_temp = val
            elif sensor.SensorType == SensorType.Load:
                if "used space" in sensor.Name.lower():
                    val = _safe_percentage(sensor.Value)
                    if val is not None:
                        disk_used = val
            elif sensor.SensorType == SensorType.Level:
                name = sensor.Name.lower()
                if name == "life" or "remaining life" in name:
                    val = _safe_percentage(sensor.Value)
                    if val is not None:
                        disk_life = val
                elif "percentage used" in name and disk_life is None:
                    # NVMe wear may exceed 100%; exhausted endurance means no
                    # estimated life remains, not an invalid percentage sample.
                    val = _safe_round(sensor.Value, minimum=0)
                    if val is not None:
                        disk_life = max(0, min(100, 100 - val))
        # Always show storage devices — even without sensors
        name = re.sub(
            r"^(Samsung|WDC|Western Digital|Kingston|Crucial|Seagate|Toshiba|SK Hynix|Intel|Micron|SanDisk|ADATA|Corsair)\s*(SSD\s*)?",
            "", str(hw.Name), flags=re.IGNORECASE,
        ).strip() or str(hw.Name)
        disk_data = {
            "name": name,
            "temp": primary_temp if primary_temp is not None else disk_temp,
            "used_pct": disk_used,
        }
        if len(temperature_readings) > 1:
            disk_data["temperatures"] = temperature_readings
            disk_data["aux_temp"] = max(item["temp"] for item in temperature_readings)
        if disk_life is not None:
            disk_data["life_pct"] = disk_life
        data["disks"].append(disk_data)

    elif hw_type == HardwareType.Motherboard:
        fan_sensors = []
        control_sensors = []
        for sensor in _iter_hardware_sensors(hw, include_subhardware=True):
            name = sensor.Name.lower()
            if sensor.SensorType == SensorType.Fan:
                val = _safe_round(sensor.Value, minimum=0)
                if val is not None:
                    fan_sensors.append((name, val))
                    data["fans"].append({
                        "name": str(sensor.Name), "rpm": val,
                        "id": str(getattr(sensor, "Identifier", str(hw.Name) + "/" + name)),
                    })
                    if "cpu" in name and "optional" in name:
                        data["cpu_optional_fan"] = val
            elif sensor.SensorType == SensorType.Control:
                val = _safe_percentage(sensor.Value)
                if val is not None:
                    control_sensors.append((name, val))
            elif sensor.SensorType == SensorType.Temperature:
                val = _safe_temperature(sensor.Value)
                if val is not None:
                    data["motherboard_temps"].append({
                        "name": str(sensor.Name),
                        "temp": val,
                    })
        if data["cpu_fan"] is None:
            selected_fan = _select_cpu_fan(fan_sensors)
            if selected_fan is not None:
                _name, data["cpu_fan"] = selected_fan
        else:
            selected_fan = None
        if data["cpu_fan_pct"] is None:
            fan_name = selected_fan[0] if selected_fan else None
            data["cpu_fan_pct"] = _select_cpu_fan_control(
                control_sensors,
                fan_name,
                data["cpu_fan"] is not None,
            )

    elif hw_type == HardwareType.Memory:
        for sensor in hw.Sensors:
            if sensor.SensorType == SensorType.Load:
                priority = _ram_load_priority(hw.Name, sensor.Name)
                if priority is not None:
                    val = _safe_percentage(sensor.Value)
                    if val is not None:
                        candidates["ram_pct"].append((
                            priority,
                            _normalized_sensor_name(hw.Name),
                            _normalized_sensor_name(sensor.Name),
                            val,
                        ))


def read_sensors(computer, update_storage=True):
    """Read all temperature and load sensors from hardware.

    Args:
        computer: LibreHardwareMonitor Computer instance (or None for psutil fallback).
        update_storage: If False, skip hw.Update() for storage devices to reduce I/O.
                        Disk data will retain previous values from the last full update.
    """
    data = _empty_sensor_data()
    candidates = {"cpu_temp": [], "gpus": [], "ram_pct": []}

    if computer is None:
        data[SENSOR_STATUS_KEY] = SENSOR_STATUS_PSUTIL_FALLBACK
        return _apply_psutil_fallbacks(data)

    try:
        from LibreHardwareMonitor.Hardware import HardwareType, SensorType
    except Exception:
        log.warning("Failed to import LibreHardwareMonitor sensor enums, falling back to psutil", exc_info=True)
        data[SENSOR_STATUS_KEY] = SENSOR_STATUS_PSUTIL_FALLBACK
        data[SENSOR_REINIT_KEY] = True
        return _apply_psutil_fallbacks(data)

    try:
        hardware_items = list(computer.Hardware)
    except Exception:
        log.warning("Failed to enumerate LibreHardwareMonitor hardware, falling back to psutil", exc_info=True)
        data[SENSOR_STATUS_KEY] = SENSOR_STATUS_PSUTIL_FALLBACK
        data[SENSOR_REINIT_KEY] = True
        return _apply_psutil_fallbacks(data)

    if not hardware_items:
        data[SENSOR_STATUS_KEY] = SENSOR_STATUS_PSUTIL_FALLBACK
        data[SENSOR_REINIT_KEY] = True
        return _apply_psutil_fallbacks(data)

    hardware_items.sort(
        key=lambda hw: (
            0 if hw.HardwareType in (HardwareType.GpuAmd, HardwareType.GpuNvidia) else
            1 if hw.HardwareType == HardwareType.GpuIntel else 2
        )
    )
    for hw in hardware_items:
        try:
            _read_hardware_block(
                hw,
                HardwareType,
                SensorType,
                data,
                candidates,
                update_storage=update_storage,
            )
        except Exception:
            data[SENSOR_STATUS_KEY] = SENSOR_STATUS_PARTIAL
            data[SENSOR_REINIT_KEY] = True
            if hw.HardwareType == HardwareType.Storage:
                data[SENSOR_STORAGE_FAILED_KEY] = True
            log.warning("Skipping hardware block after sensor read failure: %s", _hardware_label(hw), exc_info=True)

    _apply_ranked_sensor_candidates(data, candidates)
    _apply_psutil_fallbacks(data)

    return data


# --- Color coding ---
# Application warning/action thresholds, not universal hardware damage limits.
# SYNC: rendering and sound alerts both consume this table.
_METRIC_THRESHOLDS = {
    "cpu_temp": (70, 85),
    "gpu_temp": (80, 90),
    "gpu_hotspot_temp": (90, 105),
    "gpu_memory_temp": (85, 100),
    "disk_temp": (45, 55),
    "disk_used": (80, 90),
    "ram_pct": (80, 95),
}


def _metric_color(value, thresholds):
    if value is None:
        return "#888888"
    warning, critical = thresholds
    if value < warning:
        return "#4ade80"
    if value < critical:
        return "#facc15"
    return "#f87171"


def temp_color(temp, metric="cpu_temp"):
    return _metric_color(temp, _METRIC_THRESHOLDS[metric])


def _disk_temperature_thresholds(name):
    # Samsung specifies 0-70 C operating temperature for these SSD families.
    # Unknown disks retain the conservative policy; HDD limits can be lower.
    if re.search(r"\b(?:980\s+PRO|860\s+EVO)\b", str(name), re.IGNORECASE):
        return (55, 70)
    return _METRIC_THRESHOLDS["disk_temp"]


def disk_temp_color(temp, name=""):
    return _metric_color(temp, _disk_temperature_thresholds(name))


def load_color(load):
    if load is None:
        return "#888888"
    if load < 80:
        return "#4ade80"
    # High utilization/fan speed is activity, not evidence of overheating.
    return "#facc15"


def disk_usage_color(pct):
    return _metric_color(pct, _METRIC_THRESHOLDS["disk_used"])


def _format_rpm(value):
    if value is None:
        return "--"
    if value == 0:
        return "0 RPM"
    return f"{value} RPM"


def _format_fan_reading(rpm, control_pct=None):
    # PWM/DC duty and tachometer RPM are different quantities; never derive one from the other.
    reading = _format_rpm(rpm)
    if control_pct is not None:
        reading += f" | {control_pct}% ctl"
    return reading


def _format_vram_gb(used_gb, total_gb):
    if used_gb is None or total_gb is None:
        return "--"
    return f"{used_gb:.1f}/{total_gb:.1f}G"


def _short_board_temp_name(name):
    lowered = str(name).lower()
    if "vrm" in lowered:
        return "VRM"
    if "chipset" in lowered:
        return "CHIP"
    if "system" in lowered:
        return "SYS"
    if "cpu" in lowered:
        return "CPU"
    cleaned = re.sub(r"[^a-z0-9]+", "", lowered)
    return (cleaned[:4] or "TEMP").upper()


def _format_board_temps(temps):
    parts = []
    def priority(index_item):
        index, item = index_item
        lowered = str(item.get("name", "")).lower()
        if "vrm" in lowered:
            rank = 0
        elif "chipset" in lowered:
            rank = 1
        elif "system" in lowered:
            rank = 2
        elif "cpu" in lowered:
            rank = 3
        elif "pcie" in lowered:
            rank = 4
        else:
            rank = 5
        return rank, index

    for _index, item in sorted(enumerate(temps), key=priority)[:3]:
        temp = item.get("temp")
        if temp is None:
            continue
        parts.append(f"{_short_board_temp_name(item.get('name', ''))} {temp}°C")
    return "  ".join(parts) if parts else "--"


def _format_disk_life(disks):
    parts = []
    for disk in disks:
        life = disk.get("life_pct")
        if life is None:
            continue
        parts.append(f"{disk.get('name', 'Disk')} {life}%")
    return "  ".join(parts) if parts else "--"


def _format_gpu_temps(data):
    parts = []
    for label, key in (
        ("CORE", "gpu_core_temp"),
        ("HOT", "gpu_hotspot_temp"),
        ("MEM", "gpu_memory_temp"),
    ):
        temp = data.get(key)
        if temp is not None:
            parts.append(f"{label} {temp}°C")
    return "  ".join(parts) if parts else "--"


def _update_peak_values(peaks, data):
    for key in ("cpu_temp", "ram_pct", "gpu_hotspot_temp", "gpu_memory_temp"):
        value = data.get(key)
        if value is not None and (peaks.get(key) is None or value > peaks[key]):
            peaks[key] = value

    gpu_temp = data.get("gpu_temp")
    if gpu_temp is not None and (peaks.get("gpu_temp") is None or gpu_temp > peaks["gpu_temp"]):
        peaks["gpu_temp"] = gpu_temp
        peaks["gpu_temp_label"] = data.get("gpu_temp_label")

    for disk in data.get("disks", []):
        temp = disk.get("temp")
        if temp is not None and (peaks.get("disk_temp") is None or temp > peaks["disk_temp"]):
            peaks["disk_temp"] = temp
        used = disk.get("used_pct")
        if used is not None and (peaks.get("disk_used_pct") is None or used > peaks["disk_used_pct"]):
            peaks["disk_used_pct"] = used
    return peaks


def _format_peak_temps(peaks):
    parts = []
    if peaks.get("cpu_temp") is not None:
        parts.append(f"CPU {peaks['cpu_temp']}°C")
    if peaks.get("gpu_temp") is not None:
        gpu_label = peaks.get("gpu_temp_label")
        label_part = f" {gpu_label}" if gpu_label else ""
        parts.append(f"GPU{label_part} {peaks['gpu_temp']}°C")
    for key, label in (("gpu_hotspot_temp", "HOT"), ("gpu_memory_temp", "MEM")):
        if peaks.get(key) is not None:
            parts.append(f"{label} {peaks[key]}°C")
    if peaks.get("disk_temp") is not None:
        parts.append(f"DISK {peaks['disk_temp']}°C")
    return "  ".join(parts) if parts else "--"


def _format_peak_usage(peaks):
    parts = []
    if peaks.get("ram_pct") is not None:
        parts.append(f"RAM {peaks['ram_pct']}%")
    if peaks.get("disk_used_pct") is not None:
        parts.append(f"DISK {peaks['disk_used_pct']}%")
    return "  ".join(parts) if parts else "--"


def _detail_row_values(data, peaks=None):
    peaks = _empty_peak_data() if peaks is None else peaks
    return {
        "detail_cpu_fan_rpm": _format_rpm(data.get("cpu_fan")),
        "detail_gpu_fan_rpm": _format_rpm(data.get("gpu_fan")),
        "detail_vram_gb": _format_vram_gb(
            data.get("gpu_vram_used_gb"),
            data.get("gpu_vram_total_gb"),
        ),
        "detail_gpu_temps": _format_gpu_temps(data),
        "detail_board_temps": _format_board_temps(data.get("motherboard_temps", [])),
        "detail_disk_life": _format_disk_life(data.get("disks", [])),
        "detail_disk_sensors": "  ".join(
            f"{disk['name']}: " + ", ".join(f"{s['name']} {s['temp']}°C" for s in disk["temperatures"])
            for disk in data.get("disks", []) if disk.get("temperatures")
        ) or "--",
        "detail_peak_temps": _format_peak_temps(peaks),
        "detail_peak_usage": _format_peak_usage(peaks),
    }


def _format_diag_value(value):
    if value is None:
        return "None"
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.2f}"
    return str(value)


def build_sensor_diagnostics(computer, sensor_data=None, is_admin=None, pawnio_installed=None):
    is_admin = _is_admin() if is_admin is None else is_admin
    pawnio_installed = is_pawnio_driver_installed() if pawnio_installed is None else pawnio_installed
    lines = [
        "HeatMap diagnostics",
        f"Version: {VERSION}",
        f"Admin: {'yes' if is_admin else 'no'}",
        f"PawnIO: {'installed' if pawnio_installed else 'missing'}",
        f"LHM computer: {'yes' if computer is not None else 'no'}",
    ]

    if sensor_data:
        lines.append("Sensor data:")
        for key in (
            "cpu_temp", "cpu_load", "cpu_clock", "cpu_fan", "cpu_fan_pct", "cpu_optional_fan", "fans",
            "gpu_temp", "gpu_temp_label", "gpu_core_temp", "gpu_hotspot_temp", "gpu_memory_temp",
            "gpu_load", "gpu_clock", "gpu_fan", "gpu_fan_pct",
            "gpu_vram_pct", "gpu_vram_used_gb", "gpu_vram_total_gb",
            "ram_pct", SENSOR_STATUS_KEY, SENSOR_REINIT_KEY,
        ):
            if key in sensor_data:
                lines.append(f"  {key}={_format_diag_value(sensor_data.get(key))}")

    if computer is None:
        return "\n".join(lines)

    try:
        hardware_items = list(computer.Hardware)
    except Exception as e:
        lines.append(f"Hardware enumeration failed: {e}")
        return "\n".join(lines)

    lines.append("Hardware inventory:")
    for hw in hardware_items:
        lines.append(f"Hardware: {_hardware_label(hw)}")
        sensors = list(_iter_hardware_sensors(hw, include_subhardware=True))
        if not sensors:
            lines.append("  no sensors")
            continue
        for sensor in sensors:
            try:
                value = sensor.Value
            except Exception as e:
                value = f"ERROR {e}"
            lines.append(
                f"  {sensor.SensorType} {sensor.Name} = {_format_diag_value(value)}"
            )
    return "\n".join(lines)


# --- Config ---
def _default_config():
    return {"x": 50, "y": 50, "peek_enabled": True, "alerts_enabled": True,
            "details_enabled": False,
            "gpu_fan_max_rpm": 2200, "cpu_fan_max_rpm": 1800}


def _normalize_config(cfg, defaults):
    invalid_keys = []
    provided_keys = set(cfg)
    normalized = dict(defaults)
    normalized.update(cfg)
    for key in ("x", "y"):
        if key not in provided_keys:
            continue
        value = normalized.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or (isinstance(value, float) and not math.isfinite(value))
        ):
            normalized[key] = defaults[key]
            invalid_keys.append(key)
        else:
            normalized[key] = int(value)
    for key in ("peek_enabled", "alerts_enabled", "details_enabled", "case_fans_enabled"):
        if key not in provided_keys:
            continue
        if not isinstance(normalized.get(key), bool):
            normalized[key] = defaults.get(key, False)
            invalid_keys.append(key)
    for key in ("gpu_fan_max_rpm", "cpu_fan_max_rpm"):
        if key not in provided_keys:
            continue
        value = normalized.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or (isinstance(value, float) and not math.isfinite(value))
            or value < 1
            or value > 100000
        ):
            normalized[key] = defaults[key]
            invalid_keys.append(key)
        else:
            normalized[key] = int(value)
    return normalized, invalid_keys


def _clamp_overlay_position(x, y, window_width, window_height, virt_x, virt_y, virt_w, virt_h):
    x = int(x)
    y = int(y)
    virt_x = int(virt_x)
    virt_y = int(virt_y)
    virt_w = int(virt_w)
    virt_h = int(virt_h)
    if virt_w <= 0 or virt_h <= 0:
        return x, y

    window_width = max(1, int(window_width))
    window_height = max(1, int(window_height))
    visible_width = min(window_width, virt_w)
    visible_height = min(window_height, virt_h)
    max_x = virt_x + virt_w - visible_width
    max_y = virt_y + virt_h - visible_height
    return min(max(x, virt_x), max_x), min(max(y, virt_y), max_y)


def load_config_result():
    defaults = _default_config()
    if not os.path.exists(CONFIG_PATH):
        return defaults, None
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        message = f"Failed to load config: {e}"
        log.warning("%s (%s)", message, CONFIG_PATH, exc_info=True)
        return defaults, message
    if not isinstance(cfg, dict):
        message = "Invalid config format"
        log.warning("%s in %s", message, CONFIG_PATH)
        return defaults, message
    cfg, invalid_keys = _normalize_config(cfg, defaults)
    if invalid_keys:
        message = f"Adjusted invalid config fields: {', '.join(invalid_keys)}"
        log.warning("%s in %s", message, CONFIG_PATH)
        return cfg, message
    return cfg, None


def load_config():
    cfg, _message = load_config_result()
    return cfg


def save_config(cfg):
    tmp_path = f"{CONFIG_PATH}.tmp"
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, CONFIG_PATH)
        return True, "Config saved"
    except Exception as e:
        message = f"Failed to save config: {e}"
        log.warning("%s (%s)", message, CONFIG_PATH, exc_info=True)
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            log.debug("Failed to remove temporary config file", exc_info=True)
        return False, message


def _runtime_dll_errors(lib_dir=LIB_DIR, manifest_path=LIB_MANIFEST_PATH):
    try:
        from setup import verify_lib_manifest
    except Exception as e:
        return [f"failed to load DLL verifier: {e}"]

    ok, messages = verify_lib_manifest(
        lib_dir=lib_dir,
        manifest_path=manifest_path,
        allow_extra_dlls=True,
    )
    return [] if ok else messages


def is_pawnio_driver_installed():
    try:
        from setup import is_pawnio_driver_installed as check_pawnio
        return check_pawnio()
    except Exception:
        log.warning("Failed to check PawnIO driver status", exc_info=True)
        return False


def prepare_verified_pawnio_installer():
    try:
        from setup import download_pawnio
        return True, download_pawnio()
    except Exception as e:
        return False, str(e)


# --- Main overlay class ---
class OverlayApp:
    def __init__(self, autostart_result=None):
        # Hardware discovery runs on the sensor thread so it cannot delay Tk.
        self.computer = None
        self.config, config_warning = load_config_result()
        self._config_status = STATUS_CONFIG_ADJUSTED if config_warning else None
        self._driver_status = (
            SENSOR_STATUS_DRIVER_MISSING
            if not is_pawnio_driver_installed()
            else None
        )
        self._sensor_start_time = time.monotonic()
        self._sensor_status = SENSOR_STATUS_WARMING_UP
        self.running = True
        self._stop_event = threading.Event()
        self.sensor_data = {}
        self._sensor_sample_time = None
        self.lock = threading.Lock()
        self.embedded = False
        self._embed_after_id = None
        self._window_transition_generation = 0

        # --- Alert system ---
        self.alerts_enabled = self.config.get("alerts_enabled", True)
        self.details_enabled = self.config.get("details_enabled", False)
        self._last_alert_time = 0
        self._ALERT_COOLDOWN = 60  # seconds between repeated alerts
        self.peaks = _empty_peak_data()
        self.advisor = ThermalAdvisor()
        self.thermal_findings = []
        self.fan_worker = FanWorkerClient(APP_DIR)
        self._case_fan_status = {"state": "off"}
        self._seen_case_fans = set()

        # --- tkinter setup ---
        self.root = tk.Tk()
        self.root.title("Temp Overlay")
        self.root.overrideredirect(True)
        self.root.wm_attributes("-alpha", 0.88)
        self.root.configure(bg="#1a1a2e")

        # Hide until embedded in desktop to prevent blink
        # Use alpha=0 instead of withdraw() — withdraw/deiconify cycle resets
        # z-order on Windows, causing the window to appear above app windows.
        self.root.wm_attributes("-alpha", 0)

        # Position from saved config
        self.root.geometry(f"+{self.config.get('x', 50)}+{self.config.get('y', 50)}")

        # --- Drag support ---
        self._drag_x = 0
        self._drag_y = 0
        self._dragged = False

        # --- Header ---
        header = tk.Frame(self.root, bg="#16213e", cursor="fleur")
        header.pack(fill="x", padx=2, pady=(2, 0))

        title_label = tk.Label(
            header, text="  HW Monitor", font=("Segoe UI", 9, "bold"),
            fg="#7c83ff", bg="#16213e", anchor="w"
        )
        title_label.pack(side="left", fill="x", expand=True)

        close_btn = tk.Label(
            header, text=" X ", font=("Segoe UI", 9, "bold"),
            fg="#f87171", bg="#16213e", cursor="hand2"
        )
        close_btn.pack(side="right")
        close_btn.bind("<Button-1>", lambda e: self.quit())

        header.bind("<Button-1>", self.start_drag)
        header.bind("<B1-Motion>", self.on_drag)
        header.bind("<ButtonRelease-1>", self.end_drag)
        title_label.bind("<Button-1>", self.start_drag)
        title_label.bind("<B1-Motion>", self.on_drag)
        title_label.bind("<ButtonRelease-1>", self.end_drag)

        # --- Content frame ---
        self.content = tk.Frame(self.root, bg="#1a1a2e")
        self.content.pack(fill="both", padx=6, pady=4)

        # Group colors
        CPU_CLR = "#6ea8fe"
        GPU_CLR = "#c084fc"
        RAM_CLR = "#67e8f9"
        self.DISK_CLR = "#fdba74"

        # Create label rows in color-coded groups
        self.rows = {}

        # CPU group — temp + clock + load as three separate colored values
        cpu_row = tk.Frame(self.content, bg="#1a1a2e")
        cpu_row.pack(fill="x", pady=1)
        tk.Label(cpu_row, text=" CPU", font=("Segoe UI", 10, "bold"),
                 fg=CPU_CLR, bg="#1a1a2e", width=6, anchor="w").pack(side="left")
        self.rows["cpu_load"] = tk.Label(cpu_row, text="", font=("Segoe UI", 10),
                                         fg="#888888", bg="#1a1a2e", anchor="e")
        self.rows["cpu_load"].pack(side="right")
        self.rows["cpu_clock"] = tk.Label(cpu_row, text="", font=("Segoe UI", 10),
                                          fg="#888888", bg="#1a1a2e", anchor="e")
        self.rows["cpu_clock"].pack(side="right", padx=(0, 4))
        self.rows["cpu_temp"] = tk.Label(cpu_row, text="--", font=("Segoe UI", 10),
                                         fg="#888888", bg="#1a1a2e", anchor="e")
        self.rows["cpu_temp"].pack(side="right", padx=(0, 4))

        self._make_row("cpu_fan", "C.FAN", label_fg=CPU_CLR)
        self._make_row("cpu_optional_fan", "C.OPT", label_fg=CPU_CLR)
        tk.Frame(self.content, bg="#2a2a4e", height=1).pack(fill="x", pady=2)

        # GPU group — temp + clock + load as three separate colored values
        gpu_row = tk.Frame(self.content, bg="#1a1a2e")
        gpu_row.pack(fill="x", pady=1)
        tk.Label(gpu_row, text=" G.CORE", font=("Segoe UI", 10, "bold"),
                 fg=GPU_CLR, bg="#1a1a2e", width=6, anchor="w").pack(side="left")
        self.rows["gpu_load"] = tk.Label(gpu_row, text="", font=("Segoe UI", 10),
                                         fg="#888888", bg="#1a1a2e", anchor="e")
        self.rows["gpu_load"].pack(side="right")
        self.rows["gpu_clock"] = tk.Label(gpu_row, text="", font=("Segoe UI", 10),
                                          fg="#888888", bg="#1a1a2e", anchor="e")
        self.rows["gpu_clock"].pack(side="right", padx=(0, 4))
        self.rows["gpu_temp"] = tk.Label(gpu_row, text="--", font=("Segoe UI", 10),
                                         fg="#888888", bg="#1a1a2e", anchor="e")
        self.rows["gpu_temp"].pack(side="right", padx=(0, 4))

        self._make_row("gpu_hotspot_temp", "HOTSPOT", label_fg=GPU_CLR)
        self._make_row("gpu_delta", "HOT−CORE", label_fg=GPU_CLR)
        self._make_row("gpu_memory_temp", "V.TEMP", label_fg=GPU_CLR)
        self._make_row("vram", "VRAM", label_fg=GPU_CLR)
        self._make_row("gpu_fan", "G.FAN", label_fg=GPU_CLR)
        for number in (1, 2, 3, 4, 5, 6):
            self._make_row(f"case_fan_{number}", f"SYS {number}" + ("/P" if number >= 5 else ""))
            self.rows[f"case_fan_{number}"].master.pack_forget()
        self._make_row("case_fan_control", "AIRFLOW")
        tk.Frame(self.content, bg="#2a2a4e", height=1).pack(fill="x", pady=2)

        # RAM — used/total + load% (like CPU row)
        ram_row = tk.Frame(self.content, bg="#1a1a2e")
        ram_row.pack(fill="x", pady=1)
        tk.Label(ram_row, text=" RAM", font=("Segoe UI", 10, "bold"),
                 fg=RAM_CLR, bg="#1a1a2e", width=6, anchor="w").pack(side="left")
        self.rows["ram_pct"] = tk.Label(ram_row, text="", font=("Segoe UI", 10),
                                        fg="#888888", bg="#1a1a2e", anchor="e")
        self.rows["ram_pct"].pack(side="right")
        self.rows["ram_gb"] = tk.Label(ram_row, text="--", font=("Segoe UI", 10),
                                       fg="#888888", bg="#1a1a2e", anchor="e")
        self.rows["ram_gb"].pack(side="right", padx=(0, 4))
        tk.Frame(self.content, bg="#2a2a4e", height=1).pack(fill="x", pady=2)

        # Optional expanded detail rows.
        self.details_frame = tk.Frame(self.content, bg="#1a1a2e")
        self.details_frame.pack(fill="x")
        self._make_row("detail_cpu_fan_rpm", "C.RPM", parent=self.details_frame, label_fg=CPU_CLR)
        self._make_row("detail_gpu_fan_rpm", "G.RPM", parent=self.details_frame, label_fg=GPU_CLR)
        self._make_row("detail_vram_gb", "V.GB", parent=self.details_frame, label_fg=GPU_CLR)
        self._make_row("detail_gpu_temps", "G.TEMP", parent=self.details_frame, label_fg=GPU_CLR)
        self._make_row("detail_board_temps", "BOARD", parent=self.details_frame, label_fg="#a7f3d0")
        self._make_row("detail_disk_life", "D.LIFE", parent=self.details_frame, label_fg=self.DISK_CLR)
        self._make_row("detail_disk_sensors", "D.SENSE", parent=self.details_frame, label_fg=self.DISK_CLR)
        self._make_row("detail_peak_temps", "PEAK.T", parent=self.details_frame, label_fg="#facc15")
        self._make_row("detail_peak_usage", "PEAK.%", parent=self.details_frame, label_fg="#facc15")

        # Disk rows created dynamically
        self.disk_frame = tk.Frame(self.content, bg="#1a1a2e")
        self.disk_frame.pack(fill="x")
        self.disk_labels = []
        self._last_disk_names = []
        self._apply_details_visibility()

        # Bottom padding
        tk.Frame(self.content, bg="#1a1a2e", height=2).pack()
        self.status_label = tk.Label(
            self.content, text="", font=("Segoe UI", 8),
            fg="#facc15", bg="#1a1a2e", anchor="w"
        )
        if self._driver_status == SENSOR_STATUS_DRIVER_MISSING:
            self.status_label.configure(cursor="hand2")
            self.status_label.bind("<Button-1>", lambda _event: self.prepare_pawnio_repair())
        self._status_label_visible = False
        self._refresh_runtime_status()
        self._clamp_saved_position_to_visible_screen(persist=True)
        self.health_label = tk.Label(
            self.content, text="Sensors warming up", font=("Segoe UI", 9, "bold"),
            bg="#1a1a2e", fg="#facc15", anchor="w", justify="left", wraplength=310,
        )
        self.health_label.pack(fill="x", pady=(3, 2))

        # --- Right-click menu ---
        self.topmost = False
        self.menu = tk.Menu(self.root, tearoff=0, bg="#1a1a2e", fg="#a0a0c0",
                           activebackground="#2a2a4e", activeforeground="white",
                           font=("Segoe UI", 9))
        self._menu_idx = {}  # label_key -> menu index
        self._add_menu_item("topmost", "Always on top: OFF", self.toggle_topmost)
        autostart_enabled = (
            is_autostart_enabled() if autostart_result is None else autostart_result.enabled
        )
        self._add_menu_item("autostart",
            "Autostart: ERROR" if autostart_enabled is None else (
                "Autostart: ON (UAC)" if autostart_enabled else "Autostart: OFF"
            ),
            self.toggle_autostart)
        self._add_menu_item("alerts",
            "Alerts: ON" if self.alerts_enabled else "Alerts: OFF",
            self.toggle_alerts)
        self.peek_enabled = self.config.get("peek_enabled", True)
        self._add_menu_item("peek",
            "Peek from edge: ON" if self.peek_enabled else "Peek from edge: OFF",
            self.toggle_peek)
        self._add_menu_item("details",
            "Details: ON" if self.details_enabled else "Details: OFF",
            self.toggle_details)
        if self._driver_status == SENSOR_STATUS_DRIVER_MISSING:
            self._add_menu_item(
                "pawnio",
                "Prepare verified PawnIO repair...",
                self.prepare_pawnio_repair,
            )
        self.menu.add_separator()
        self.menu.add_command(label="Open log file", command=self.open_log_file)
        self.menu.add_command(label="Copy log path", command=self.copy_log_path)
        self._add_menu_item("diagnostics", "Copy diagnostics", self.copy_diagnostics)
        self._add_menu_item("case_fans", "Automatic case fans: " +
                            ("ON" if self.config.get("case_fans_enabled", False) else "OFF"),
                            self.toggle_case_fans)
        self.menu.add_command(label="Reset peaks", command=self.reset_peaks)
        self.menu.add_separator()
        self.menu.add_command(label="Close", command=self.quit)
        self.root.bind("<Button-3>", self.show_menu)

        # --- Peek from edge ---
        self.peek_visible = False
        self._peek_animating = False
        self._saved_pos = None  # saved desktop position before peek
        self._peek_monitor_area = None
        self._cursor_was_at_peek_edge = False
        self._peek_poll_after_id = None
        self._monitor_areas = _get_monitor_areas()
        self._poll_screen_change()
        self._poll_peek_edge()
        self.root.after(250, self._poll_desktop_visibility)

        # --- Embed into desktop after window is drawn ---
        self._schedule_embed(100)

        # --- Start sensor thread ---
        self.sensor_thread = threading.Thread(target=self.sensor_loop, daemon=True)
        self.sensor_thread.start()

        # --- Start UI update loop ---
        if self.config.get("case_fans_enabled", False):
            self.fan_worker.start()
        self.update_ui()

    def _add_menu_item(self, key, label, command):
        """Add a menu command and track its index by key."""
        self.menu.add_command(label=label, command=command)
        self._menu_idx[key] = self.menu.index("end")

    def _set_menu_label(self, key, label):
        """Update a menu item's label by its key."""
        self.menu.entryconfig(self._menu_idx[key], label=label)

    def _clamp_saved_position_to_visible_screen(self, persist=False):
        if not hasattr(self, "root"):
            return False
        if getattr(self, "peek_visible", False) or getattr(self, "_peek_animating", False):
            return False
        self.root.update_idletasks()
        x, y = _clamp_overlay_to_monitor_areas(
            self.config.get("x", 50),
            self.config.get("y", 50),
            self.root.winfo_width(),
            self.root.winfo_height(),
            _get_monitor_areas(),
        )
        if (x, y) == (self.config.get("x", 50), self.config.get("y", 50)):
            return False
        self.config["x"] = x
        self.config["y"] = y
        self.root.geometry(f"+{x}+{y}")
        if persist:
            self._save_config(update_status=False)
        return True

    def _current_runtime_status(self):
        config_status = getattr(self, "_config_status", None)
        if config_status == STATUS_CONFIG_SAVE_ERROR:
            return config_status
        if getattr(self, "_driver_status", None):
            return self._driver_status
        if config_status:
            return config_status
        return getattr(self, "_sensor_status", None)

    def _refresh_runtime_status(self):
        if not hasattr(self, "status_label"):
            return
        status = self._current_runtime_status()
        if not status:
            if getattr(self, "_status_label_visible", False):
                self.status_label.pack_forget()
                self._status_label_visible = False
            return
        self.status_label.config(
            text=STATUS_TEXT.get(status, ""),
            fg=STATUS_COLOR.get(status, "#facc15"),
        )
        if not getattr(self, "_status_label_visible", False):
            self.status_label.pack(fill="x", pady=(2, 0))
            self._status_label_visible = True

    def _set_sensor_status(self, status):
        if status not in (
            SENSOR_STATUS_PSUTIL_FALLBACK,
            SENSOR_STATUS_PARTIAL,
            SENSOR_STATUS_WARMING_UP,
            SENSOR_STATUS_CPU_UNAVAILABLE,
            SENSOR_STATUS_STALE,
        ):
            status = None
        self._sensor_status = status
        self._refresh_runtime_status()

    def _set_config_status(self, status):
        if status not in CONFIG_STATUSES:
            status = None
        self._config_status = status
        self._refresh_runtime_status()

    def _save_config(self, update_status=True):
        ok, message = save_config(self.config)
        if update_status and getattr(self, "running", False):
            self._set_config_status(None if ok else STATUS_CONFIG_SAVE_ERROR)
        return ok, message

    def open_log_file(self):
        try:
            log_path = os.path.abspath(LOG_PATH)
            log_dir = os.path.dirname(log_path)
            if os.path.exists(log_path):
                target = log_path
            else:
                os.makedirs(log_dir, exist_ok=True)
                target = log_dir
            os.startfile(target)
        except Exception as e:
            log.warning("Failed to open log file location: %s", e, exc_info=True)
            _show_error_message("HeatMap Log", f"Failed to open log file:\n{e}\n\nLog path:\n{LOG_PATH}")

    def copy_log_path(self):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(os.path.abspath(LOG_PATH))
        except Exception as e:
            log.warning("Failed to copy log path: %s", e, exc_info=True)
            _show_error_message("HeatMap Log", f"Failed to copy log path:\n{e}\n\nLog path:\n{LOG_PATH}")

    def copy_diagnostics(self):
        if not self.running or getattr(self, "_diagnostics_running", False):
            return
        self._diagnostics_running = True
        self._set_menu_label("diagnostics", "Collecting diagnostics...")
        results = queue.Queue(maxsize=1)
        self._diagnostics_results = results

        def worker():
            computer = None
            result = None
            try:
                if self._stop_event.is_set():
                    return
                computer = init_hardware_monitor()
                if self._stop_event.is_set():
                    return
                data = read_sensors(computer)
                if not self._stop_event.is_set():
                    result = (True, build_sensor_diagnostics(computer, data))
                    result = (True, result[1] + "\nCase fan controller:\n" +
                              json.dumps(getattr(self, "_case_fan_status", {"state": "off"}), ensure_ascii=False))
            except Exception as e:
                log.warning("Failed to collect diagnostics: %s", e, exc_info=True)
                result = (False, str(e))
            finally:
                _close_hardware_monitor(computer)
            if result is not None and not self._stop_event.is_set():
                results.put_nowait(result)

        self._diagnostics_thread = threading.Thread(target=worker, daemon=True)
        try:
            self._diagnostics_thread.start()
        except Exception as e:
            results.put_nowait((False, str(e)))
        self.root.after(100, self._poll_diagnostics)

    def _poll_diagnostics(self):
        if not self.running or not getattr(self, "_diagnostics_running", False):
            return
        try:
            ok, detail = self._diagnostics_results.get_nowait()
        except queue.Empty:
            self.root.after(100, self._poll_diagnostics)
            return
        self._diagnostics_running = False
        self._set_menu_label("diagnostics", "Copy diagnostics")
        if ok:
            try:
                self.root.clipboard_clear()
                self.root.clipboard_append(detail)
                return
            except Exception as e:
                log.warning("Failed to copy diagnostics: %s", e, exc_info=True)
                detail = str(e)
        _show_error_message("HeatMap Diagnostics", f"Failed to copy diagnostics:\n{detail}")

    def prepare_pawnio_repair(self):
        if getattr(self, "_pawnio_repair_running", False):
            return
        self._pawnio_repair_running = True
        self._set_menu_label("pawnio", "Preparing PawnIO repair...")
        self._pawnio_repair_results = queue.Queue(maxsize=1)

        def worker():
            self._pawnio_repair_results.put(prepare_verified_pawnio_installer())

        threading.Thread(target=worker, daemon=True).start()
        self.root.after(100, self._poll_pawnio_repair)

    def _poll_pawnio_repair(self):
        if not self.running:
            return
        try:
            ok, detail = self._pawnio_repair_results.get_nowait()
        except queue.Empty:
            self.root.after(100, self._poll_pawnio_repair)
            return
        self._finish_pawnio_repair(ok, detail)

    def _finish_pawnio_repair(self, ok, detail):
        self._pawnio_repair_running = False
        if not self.running:
            return
        self._set_menu_label("pawnio", "Prepare verified PawnIO repair...")
        if not ok:
            _show_error_message("PawnIO repair", f"Could not prepare PawnIO installer:\n{detail}")
            return
        installer_path = os.path.abspath(detail)
        try:
            os.startfile(os.path.dirname(installer_path))
        except Exception:
            log.warning("Failed to open PawnIO download folder", exc_info=True)
        _show_info_message(
            "PawnIO repair ready",
            "Verified PawnIO installer is ready.\n\n"
            f"{installer_path}\n\n"
            "Close HeatMap, run the installer as administrator, restart Windows, "
            "then run: python setup.py --hardware-smoke",
        )

    def _apply_details_visibility(self):
        if self.details_enabled:
            pack_options = {"fill": "x"}
            if hasattr(self, "disk_frame"):
                pack_options["before"] = self.disk_frame
            self.details_frame.pack(**pack_options)
        else:
            self.details_frame.pack_forget()

    def _get_hwnd(self):
        """Get the native Windows HWND for the tkinter root window."""
        self.root.update_idletasks()
        frame_id = self.root.wm_frame()
        if frame_id:
            try:
                hwnd = int(frame_id, 16) if isinstance(frame_id, str) else int(frame_id)
            except (ValueError, TypeError):
                hwnd = 0
            if hwnd and hwnd != 0:
                return hwnd
        return self.root.winfo_id()

    def _can_embed_now(self):
        return (
            self.running
            and not self.topmost
            and not self.peek_visible
            and not self._peek_animating
        )

    def _cancel_scheduled_embed(self):
        self._window_transition_generation += 1
        after_id = self._embed_after_id
        self._embed_after_id = None
        if after_id is not None:
            try:
                self.root.after_cancel(after_id)
            except Exception:
                log.debug("Failed to cancel scheduled desktop embed", exc_info=True)

    def _embed_into_desktop(self, expected_generation=None):
        """Embed the window into the desktop layer (above wallpaper, below icons and apps)."""
        if (
            expected_generation is not None
            and expected_generation != self._window_transition_generation
        ):
            return
        self._embed_after_id = None
        if not self._can_embed_now():
            return
        hwnd = self._get_hwnd()
        set_tool_window(hwnd)
        if embed_in_desktop(hwnd):
            self.embedded = True
        else:
            self.embedded = False
            # Show Desktop may raise the shell over independent bottom windows.
            if not _position_above_desktop(hwnd):
                user32.SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0,
                                   SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        # Restore opacity (window was hidden with alpha=0, not withdraw)
        self.root.wm_attributes("-alpha", 0.88)

    def _schedule_embed(self, delay=50):
        """Schedule a generation-guarded embed; stale callbacks become no-ops."""
        self._cancel_scheduled_embed()
        generation = self._window_transition_generation
        self._embed_after_id = self.root.after(
            delay,
            lambda: self._embed_into_desktop(generation),
        )

    def _detach_from_desktop(self):
        if not self.embedded:
            return True
        hwnd = self._get_hwnd()
        if not _set_parent_verified(hwnd, 0):
            return False
        self.embedded = False
        return True

    def _has_valid_desktop_parent(self):
        if not self.embedded:
            return False
        hwnd = self._get_hwnd()
        parent = user32.GetParent(hwnd)
        if not parent or not user32.IsWindow(parent):
            return False
        class_name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(parent, class_name, 256)
        return class_name.value == "WorkerW"

    def _make_row(self, key, label_text, parent=None, label_fg="#a0a0c0"):
        parent = parent or self.content
        row = tk.Frame(parent, bg="#1a1a2e")
        row.pack(fill="x", pady=1)
        tk.Label(
            row, text=f" {label_text}", font=("Segoe UI", 10, "bold"),
            fg=label_fg, bg="#1a1a2e", width=max(6, len(label_text) + 1), anchor="w"
        ).pack(side="left")
        val_lbl = tk.Label(
            row, text="--", font=("Segoe UI", 10),
            fg="#888888", bg="#1a1a2e", anchor="e",
            wraplength=260 if key.startswith("detail_") else 0, justify="right",
        )
        val_lbl.pack(side="right")
        self.rows[key] = val_lbl

    def _make_disk_row(self, key, disk_name, parent):
        """Create a disk row: orange bold name left, temp middle-right, usage% far-right."""
        row = tk.Frame(parent, bg="#1a1a2e")
        row.pack(fill="x", pady=1)
        # Left: disk name (orange, bold)
        tk.Label(
            row, text=f" {disk_name}", font=("Segoe UI", 10, "bold"),
            fg=self.DISK_CLR, bg="#1a1a2e", anchor="w"
        ).pack(side="left")
        # Far-right: usage % (colored)
        usage_lbl = tk.Label(
            row, text="", font=("Segoe UI", 10),
            fg="#888888", bg="#1a1a2e", anchor="e"
        )
        usage_lbl.pack(side="right")
        # Middle-right: temperature (colored)
        temp_lbl = tk.Label(
            row, text="--", font=("Segoe UI", 10),
            fg="#888888", bg="#1a1a2e", anchor="e"
        )
        temp_lbl.pack(side="right", padx=(0, 4))
        self.rows[key] = temp_lbl
        self.rows[key + "_usage"] = usage_lbl

    def _poll_screen_change(self):
        """React to monitor topology/work-area changes, including equal bounding boxes."""
        if not self.running:
            return
        monitor_areas = _get_monitor_areas()
        if monitor_areas != self._monitor_areas:
            self._monitor_areas = monitor_areas
            self._cursor_was_at_peek_edge = False
            if self.peek_visible or self._peek_animating:
                self._restore_desktop_mode()
            self._clamp_saved_position_to_visible_screen(persist=True)
        if self._can_embed_now():
            if self.embedded and not self._has_valid_desktop_parent():
                self.embedded = False
            if not self.embedded and self._embed_after_id is None:
                self._schedule_embed(50)
        self.root.after(5000, self._poll_screen_change)

    def _schedule_peek_poll(self):
        after_id = getattr(self, "_peek_poll_after_id", None)
        if after_id is not None:
            self.root.after_cancel(after_id)
        self._peek_poll_after_id = None
        if self.running and self.peek_enabled and not self.topmost:
            self._peek_poll_after_id = self.root.after(100, self._poll_peek_edge)

    def _desktop_foreground(self):
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        if _window_has_class(hwnd, {"Shell_TrayWnd", "Shell_SecondaryTrayWnd"}):
            # Show Desktop can leave focus in the tray. Inspect the saved widget
            # location, so the button does not require an extra desktop click.
            x, y = self._saved_pos or (self.config.get("x", 50), self.config.get("y", 50))
            hwnd = user32.WindowFromPoint(POINT(x, y))
        return _window_has_class(hwnd, {"Progman", "WorkerW"})

    def _poll_desktop_visibility(self):
        """Recover Show Desktop independently of the optional edge polling."""
        if not self.running:
            return
        try:
            if self.topmost:
                return
            desktop = self._desktop_foreground()
            if desktop and (self.peek_visible or self._peek_animating):
                self._restore_desktop_mode()
            if self.embedded or self.peek_visible or self._peek_animating:
                return
            hwnd = self._get_hwnd()
            if user32.IsIconic(hwnd) or not user32.IsWindowVisible(hwnd):
                user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
            _position_above_desktop(hwnd)
        except tk.TclError:
            log.debug("Desktop visibility changed during window transition", exc_info=True)
        finally:
            if self.running:
                self.root.after(250, self._poll_desktop_visibility)

    def _poll_peek_edge(self):
        self._peek_poll_after_id = None
        if not self.running or not self.peek_enabled or self.topmost:
            return
        point = POINT()
        edge_monitor = None
        if user32.GetCursorPos(ctypes.byref(point)):
            edge_monitor = _exposed_right_edge_monitor(
                point.x, point.y, self._monitor_areas
            )
        at_edge = edge_monitor is not None
        if (
            at_edge
            and not self._cursor_was_at_peek_edge
            and self.peek_enabled
            and not self.topmost
            and not self.peek_visible
            and not self._peek_animating
            and not self._is_desktop_at_cursor()
        ):
            self._peek_show(edge_monitor)
        self._cursor_was_at_peek_edge = at_edge
        self._schedule_peek_poll()

    def _is_desktop_at_cursor(self):
        """Check whether the desktop, rather than an application, is under the cursor."""
        pt = POINT()
        if not user32.GetCursorPos(ctypes.byref(pt)):
            return False
        hwnd = user32.WindowFromPoint(pt)
        return self._is_desktop_hwnd(hwnd) or _window_has_class(
            hwnd, {"Shell_TrayWnd", "Shell_SecondaryTrayWnd"},
        )

    def _is_desktop_hwnd(self, hwnd):
        """Check if the given HWND belongs to a desktop-layer window."""
        if not hwnd:
            return True
        # Any window from our own process is the overlay widget; treat it as desktop.
        pid = ctypes.wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value == _MY_PID:
            return True
        return _window_has_class(hwnd, {"Progman", "WorkerW"})

    def _restore_desktop_mode(self, delay=50):
        self._peek_animating = False
        self.peek_visible = False
        self.root.wm_attributes("-alpha", 0)
        self.root.wm_attributes("-topmost", False)
        if self._saved_pos:
            x, y = self._saved_pos
            self.root.geometry(f"+{x}+{y}")
            self.config["x"] = x
            self.config["y"] = y
            self._saved_pos = None
        self._peek_monitor_area = None
        self._schedule_embed(delay)

    def _peek_show(self, monitor_area=None):
        """Slide the overlay in from the right edge."""
        if not self.running or not self.peek_enabled or self.peek_visible or self._peek_animating or self.topmost:
            return
        # If the desktop is under the cursor, the embedded widget is already visible.
        if self._is_desktop_at_cursor():
            return
        if monitor_area is None:
            point = POINT()
            if user32.GetCursorPos(ctypes.byref(point)):
                monitor_area = _exposed_right_edge_monitor(
                    point.x, point.y, self._monitor_areas
                )
        if monitor_area is None:
            selected = _select_monitor_for_window(
                self.config.get("x", 50),
                self.config.get("y", 50),
                max(1, self.root.winfo_width()),
                max(1, self.root.winfo_height()),
                self._monitor_areas,
            )
            monitor_area = selected
        if monitor_area is None:
            return
        self._peek_monitor_area = monitor_area

        # Save current desktop position
        self._saved_pos = (self.config.get("x", 50), self.config.get("y", 50))

        self._cancel_scheduled_embed()
        if not self._detach_from_desktop():
            self._saved_pos = None
            self._peek_monitor_area = None
            log.warning("Cannot enter peek mode because desktop detach failed")
            return

        self._peek_animating = True

        # Make topmost
        self.root.wm_attributes("-alpha", 0.88)
        self.root.wm_attributes("-topmost", True)

        monitor_rect, work_rect = monitor_area
        screen_right = monitor_rect[2]
        try:
            self.root.update_idletasks()
            overlay_w = self.root.winfo_width()
            overlay_h = self.root.winfo_height()
        except tk.TclError:
            self._restore_desktop_mode()
            return

        # Keep the same Y position as on the desktop
        target_x = work_rect[2] - overlay_w
        saved_y = self._saved_pos[1] if self._saved_pos else self.config.get("y", 50)
        target_y = min(max(saved_y, work_rect[1]), max(work_rect[1], work_rect[3] - overlay_h))

        # Start off-screen
        self.root.geometry(f"+{screen_right}+{target_y}")
        self.root.update_idletasks()

        # Animate slide-in
        self._animate_slide(screen_right, target_x, target_y, step=-20, callback=self._peek_shown)

    def _animate_slide(self, current_x, target_x, y, step, callback, expected_generation=None):
        """Animate horizontal slide."""
        if expected_generation is None:
            expected_generation = self._window_transition_generation
        if (not self.running or not self._peek_animating
                or expected_generation != self._window_transition_generation):
            return
        try:
            if step < 0 and current_x <= target_x:
                self.root.geometry(f"+{target_x}+{y}")
                callback()
                return
            if step > 0 and current_x >= target_x:
                self.root.geometry(f"+{target_x}+{y}")
                callback()
                return
            self.root.geometry(f"+{current_x}+{y}")
            self.root.after(10, lambda: self._animate_slide(
                current_x + step, target_x, y, step, callback, expected_generation,
            ))
        except tk.TclError:
            try:
                self._restore_desktop_mode()
            except tk.TclError:
                pass

    def _peek_shown(self):
        """Called when slide-in animation finishes."""
        self._peek_animating = False
        self.peek_visible = True
        self._peek_check_mouse()

    def _peek_check_mouse(self):
        """Poll mouse position — hide when cursor leaves overlay and trigger."""
        if not self.running or not self.peek_visible or self._peek_animating:
            return

        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        mx, my = pt.x, pt.y

        # Check if mouse is over the overlay
        try:
            ox = self.root.winfo_rootx()
            oy = self.root.winfo_rooty()
            ow = self.root.winfo_width()
            oh = self.root.winfo_height()
        except tk.TclError:
            return
        over_overlay = ox <= mx <= ox + ow and oy <= my <= oy + oh

        edge_monitor = _exposed_right_edge_monitor(mx, my, self._monitor_areas, width=10)
        over_trigger = edge_monitor is not None

        if over_overlay or over_trigger:
            self.root.after(200, self._peek_check_mouse)
        else:
            self._peek_hide()

    def _peek_hide(self):
        """Slide the overlay back off-screen and re-embed in desktop."""
        if not self.peek_visible or self._peek_animating:
            return

        self._peek_animating = True
        # peek_visible stays True until animation finishes (in _peek_hidden)

        if self._peek_monitor_area is None:
            self._restore_desktop_mode()
            return
        screen_right = self._peek_monitor_area[0][2]
        current_x = self.root.winfo_rootx()
        current_y = self.root.winfo_rooty()

        self._animate_slide(current_x, screen_right, current_y, step=20, callback=self._peek_hidden)

    def _peek_hidden(self):
        """Called when slide-out animation finishes."""
        self._restore_desktop_mode()

    def toggle_peek(self):
        self.peek_enabled = not self.peek_enabled
        self.config["peek_enabled"] = self.peek_enabled
        self._cursor_was_at_peek_edge = False
        if not self.peek_enabled and (self.peek_visible or self._peek_animating):
            self._restore_desktop_mode()
        self._save_config()
        self._set_menu_label("peek",
            "Peek from edge: ON" if self.peek_enabled else "Peek from edge: OFF"
        )
        self._schedule_peek_poll()

    def toggle_topmost(self):
        target_topmost = not self.topmost
        if target_topmost:
            self._cancel_scheduled_embed()
            if self.peek_visible or self._peek_animating:
                self._restore_desktop_mode()
                self._cancel_scheduled_embed()
            if not self._detach_from_desktop():
                log.warning("Cannot enable always-on-top because desktop detach failed")
                _show_error_message(
                    "HeatMap window",
                    "Could not detach the overlay from the desktop. Always on top remains off.",
                )
                return
            self.topmost = True
            # Restore saved position if we were peeking
            if self._saved_pos:
                x, y = self._saved_pos
                self.root.geometry(f"+{x}+{y}")
                self._saved_pos = None
            self.root.deiconify()
            self.root.wm_attributes("-alpha", 0.88)
            self.root.wm_attributes("-topmost", True)
        else:
            self.topmost = False
            self.root.wm_attributes("-topmost", False)
            self._schedule_embed(100)
        self._set_menu_label("topmost",
            "Always on top: ON" if self.topmost else "Always on top: OFF"
        )
        self._cursor_was_at_peek_edge = False
        self._schedule_peek_poll()

    def toggle_autostart(self):
        if is_autostart_enabled():
            ok, message = disable_autostart()
        else:
            ok, message = enable_autostart()
        if not ok:
            log.warning("Autostart toggle failed: %s", message)
            self._set_menu_label("autostart", "Autostart: ERROR")
            _show_error_message(
                "Autostart",
                f"{message}\n\nSee log for details:\n{LOG_PATH}",
            )
            return
        self._set_menu_label("autostart",
            "Autostart: ON (UAC)" if is_autostart_enabled() else "Autostart: OFF"
        )

    def toggle_alerts(self):
        self.alerts_enabled = not self.alerts_enabled
        self.config["alerts_enabled"] = self.alerts_enabled
        self._save_config()
        self._set_menu_label("alerts",
            "Alerts: ON" if self.alerts_enabled else "Alerts: OFF"
        )

    def toggle_details(self):
        self.details_enabled = not self.details_enabled
        self.config["details_enabled"] = self.details_enabled
        self._save_config()
        self._apply_details_visibility()
        self._clamp_saved_position_to_visible_screen(persist=True)
        self._set_menu_label("details",
            "Details: ON" if self.details_enabled else "Details: OFF"
        )

    def toggle_case_fans(self):
        enabled = not self.config.get("case_fans_enabled", False)
        if enabled:
            process = self.fan_worker.process
            if process is not None and process.poll() is None and process.stdin.closed:
                self.health_label.config(text="Case fans: waiting for firmware restore", fg="#facc15")
                return
            self.fan_worker.start()
        else:
            self.fan_worker.stop()
        self.config["case_fans_enabled"] = enabled
        self._save_config()
        self._set_menu_label("case_fans", "Automatic case fans: " + ("ON" if enabled else "OFF"))

    def _update_thermal_advice(self, data):
        if not hasattr(self, "advisor"):
            self.advisor = ThermalAdvisor()
        self.thermal_findings = self.advisor.evaluate(
            data, time.monotonic(), _METRIC_THRESHOLDS, _disk_temperature_thresholds
        )
        status = getattr(self, "_case_fan_status", {"state": "off"})
        messages = [item.text for item in self.thermal_findings]
        severity = max((item.severity for item in self.thermal_findings), default=0)
        missing = [label for key, label in (("cpu_temp", "CPU"), ("gpu_core_temp", "GPU Core"))
                   if data.get(key) is None]
        if missing:
            messages.append("Unavailable: " + ", ".join(missing))
            severity = max(severity, 1)
        if status.get("state") == "error":
            messages.insert(0, "Case fans: " + str(status.get("reason", "controller error")))
            severity = 2
        if hasattr(self, "health_label"):
            suffix = "\nSound: OFF" if not self.alerts_enabled and severity else ""
            self.health_label.config(
                text=("\n".join(messages[:3]) if messages else "No thermal warnings") + suffix,
                fg=("#4ade80", "#facc15", "#f87171")[severity],
            )

    def reset_peaks(self):
        self.peaks = _empty_peak_data()
        for key in ("detail_peak_temps", "detail_peak_usage"):
            if key in self.rows:
                self.rows[key].config(text="--", fg="#888888")

    def _check_alerts(self, data):
        """Play a warning beep if any value exceeds critical thresholds."""
        if not self.alerts_enabled:
            return
        now = time.time()
        if now - self._last_alert_time < self._ALERT_COOLDOWN:
            return

        alerts = []
        for key, label in (
            ("cpu_temp", "CPU"), ("gpu_temp", "GPU Core"),
            ("gpu_hotspot_temp", "GPU Hotspot"), ("gpu_memory_temp", "GPU Memory"),
            ("ram_pct", "RAM"),
        ):
            value = data.get(key)
            if value is not None and value >= _METRIC_THRESHOLDS[key][1]:
                unit = "%" if key == "ram_pct" else "°C"
                alerts.append(f"{label} {value}{unit}")

        for disk in data.get("disks", []):
            dtemp = disk.get("temp")
            if dtemp is not None and dtemp >= _disk_temperature_thresholds(disk["name"])[1]:
                alerts.append(f"{disk['name']} {dtemp}°C")
            used = disk.get("used_pct")
            if used is not None and used >= _METRIC_THRESHOLDS["disk_used"][1]:
                alerts.append(f"{disk['name']} {used}%")

        if any(item.severity == 2 for item in getattr(self, "thermal_findings", [])):
            alerts.append("Thermal health warning")
        if getattr(self, "_case_fan_status", {}).get("state") == "error":
            alerts.append("Case fan controller error")

        if alerts:
            self._last_alert_time = now
            # Beep in a thread to avoid blocking UI
            def _alert_beep():
                try:
                    winsound.Beep(1000, 300)
                    time.sleep(0.15)
                    winsound.Beep(1000, 300)
                except Exception:
                    pass  # No audio device or driver issue
            threading.Thread(target=_alert_beep, daemon=True).start()

    def start_drag(self, event):
        self._drag_x = event.x
        self._drag_y = event.y
        self._dragged = False

    def on_drag(self, event):
        self._dragged = True
        # Use winfo_rootx/rooty for screen-absolute coords (correct when embedded in WorkerW)
        x = self.root.winfo_rootx() + event.x - self._drag_x
        y = self.root.winfo_rooty() + event.y - self._drag_y
        self.root.geometry(f"+{x}+{y}")
        if self.peek_visible or self._peek_animating:
            self._saved_pos = (x, y)
        else:
            self.config["x"] = x
            self.config["y"] = y

    def end_drag(self, _event):
        if not self._dragged:
            return
        # If dragged during peek, persist the new position into config
        if self._saved_pos:
            self.config["x"], self.config["y"] = self._saved_pos
        self._save_config()

    def show_menu(self, event):
        self.menu.tk_popup(event.x_root, event.y_root)

    def sensor_loop(self):
        # psutil maintains its CPU baseline per thread.
        psutil.cpu_percent(interval=0)
        computer = self.computer
        consecutive_errors = 0
        consecutive_reinit_hints = 0
        next_init_retry = 0
        next_storage_update = 0
        storage_failed = False
        needs_reinit = computer is None
        first_initialization = computer is None
        try:
            while self.running and not self._stop_event.is_set():
                if (computer is None or needs_reinit) and time.monotonic() >= next_init_retry:
                    if computer is not None:
                        log.warning("Reinitializing hardware monitor after incomplete sensor samples or read errors")
                    elif not first_initialization:
                        log.warning("Retrying unavailable hardware monitor initialization")
                    with self.lock:
                        self.computer = None
                    # Close/Open can take seconds. Only this worker owns the handle;
                    # never hold the UI data lock during native hardware operations.
                    _close_hardware_monitor(computer)
                    computer = None
                    if not self.running or self._stop_event.is_set():
                        break
                    computer = init_hardware_monitor()
                    now = time.monotonic()
                    next_init_retry = now + SENSOR_INIT_RETRY_SECONDS
                    next_storage_update = 0
                    if first_initialization:
                        self._sensor_start_time = now
                        first_initialization = False
                    if not self.running or self._stop_event.is_set():
                        break
                    with self.lock:
                        self.computer = computer
                    needs_reinit = False
                    consecutive_errors = consecutive_reinit_hints = 0
                try:
                    update_storage = time.monotonic() >= next_storage_update
                    data = read_sensors(computer, update_storage=update_storage)
                    if update_storage:
                        next_storage_update = time.monotonic() + 30
                        storage_failed = bool(data.get(SENSOR_STORAGE_FAILED_KEY))
                    if storage_failed:
                        # Cached LHM reads are not evidence that a failed native
                        # storage update recovered. Hide stale disks until refresh.
                        data["disks"] = []
                        data[SENSOR_STATUS_KEY] = SENSOR_STATUS_PARTIAL
                        data[SENSOR_REINIT_KEY] = True
                    warmup = (
                        computer is not None
                        and time.monotonic() - self._sensor_start_time < SENSOR_WARMUP_SECONDS
                    )
                    if warmup and data.get(SENSOR_REINIT_KEY):
                        data[SENSOR_STATUS_KEY] = SENSOR_STATUS_WARMING_UP
                    elif computer is not None and not warmup and data.get("cpu_temp") is None:
                        data[SENSOR_STATUS_KEY] = SENSOR_STATUS_CPU_UNAVAILABLE
                    with self.lock:
                        self.sensor_data = data
                        self._sensor_sample_time = time.monotonic()
                    consecutive_errors = 0
                    if computer is not None and data.get(SENSOR_REINIT_KEY):
                        consecutive_reinit_hints += 1
                        needs_reinit = consecutive_reinit_hints >= (1 if warmup else 3)
                    else:
                        consecutive_reinit_hints = 0
                        needs_reinit = False
                except Exception as e:
                    consecutive_errors += 1
                    consecutive_reinit_hints = 0
                    log.error("Sensor read error: %s", e, exc_info=True)
                    with self.lock:
                        self.sensor_data = {"error": str(e)}
                        self._sensor_sample_time = time.monotonic()
                    needs_reinit = consecutive_errors >= 3
                self._stop_event.wait(2)
        finally:
            # The worker closes its own handle once native calls return.
            _close_hardware_monitor(computer)
            with self.lock:
                self.computer = None

    def update_ui(self):
        if not self.running:
            return

        if hasattr(self, "fan_worker"):
            self._case_fan_status = self.fan_worker.poll()
            if self.config.get("case_fans_enabled", False) and self._case_fan_status.get("state") == "stopped":
                self._case_fan_status = dict(self._case_fan_status, state="error",
                                             reason="Controller stopped; toggle automatic case fans OFF then ON")
            status = self._case_fan_status
            state = status.get("state", "off")
            command = status.get("command_pct")
            self.rows["case_fan_control"].config(
                text=f"AUTO {command}%" if state == "active" else
                     {"off": "Firmware", "stopped": "Firmware", "checking": "Checking..."}.get(state, "ERROR"),
                fg="#f87171" if state == "error" else "#facc15" if state == "checking" else "#4ade80",
            )

        with self.lock:
            data = self.sensor_data
            sample_time = getattr(self, "_sensor_sample_time", None)

        if not data:
            self.root.after(500, self.update_ui)
            return

        if sample_time is not None and time.monotonic() - sample_time > SENSOR_STALE_SECONDS:
            self._set_sensor_status(SENSOR_STATUS_STALE)
            self._show_sensor_error(text="--", color="#888888")
            self.root.after(2000, self.update_ui)
            return

        if "error" in data:
            self._set_sensor_status(None)
            self._show_sensor_error()
            self.root.after(2000, self.update_ui)
            return

        self._set_sensor_status(data.get(SENSOR_STATUS_KEY))
        self._update_thermal_advice(data)

        # CPU: temp + clock + load%
        cpu_temp = data.get("cpu_temp")
        cpu_load = data.get("cpu_load")
        cpu_clock = data.get("cpu_clock")
        self.rows["cpu_temp"].config(
            text=f"{cpu_temp}°C" if cpu_temp is not None else "--",
            fg=temp_color(cpu_temp)
        )
        if cpu_clock is not None:
            ghz = cpu_clock / 1000
            self.rows["cpu_clock"].config(text=f"{ghz:.2f}G", fg="#4ade80")
        else:
            self.rows["cpu_clock"].config(text="", fg="#888888")
        self.rows["cpu_load"].config(
            text=f"{cpu_load}%" if cpu_load is not None else "",
            fg=load_color(cpu_load)
        )

        # GPU: temp + clock + load%
        gpu_temp = data.get("gpu_temp")
        gpu_load = data.get("gpu_load")
        gpu_clock = data.get("gpu_clock")
        self.rows["gpu_temp"].config(
            text=f"{gpu_temp}°C" if gpu_temp is not None else "--",
            fg=temp_color(gpu_temp, "gpu_temp")
        )
        for key in ("gpu_hotspot_temp", "gpu_memory_temp"):
            value = data.get(key)
            self.rows[key].config(
                text=f"{value}°C" if value is not None else "--",
                fg=temp_color(value, key),
            )
        if "gpu_delta" in self.rows:
            delta = gpu_delta(data)
            self.rows["gpu_delta"].config(
                text=f"{delta:+}°C" if delta is not None else "--",
                fg=("#4ade80", "#facc15", "#f87171")[delta_severity(data)] if delta is not None else "#888888",
            )
        if gpu_clock is not None:
            if gpu_clock >= 1000:
                ghz = gpu_clock / 1000
                self.rows["gpu_clock"].config(text=f"{ghz:.2f}G", fg="#4ade80")
            else:
                self.rows["gpu_clock"].config(text=f"{gpu_clock}M", fg="#4ade80")
        else:
            self.rows["gpu_clock"].config(text="", fg="#888888")
        self.rows["gpu_load"].config(
            text=f"{gpu_load}%" if gpu_load is not None else "",
            fg=load_color(gpu_load)
        )

        # VRAM: usage %
        vram_pct = data.get("gpu_vram_pct")
        if vram_pct is not None:
            self.rows["vram"].config(text=f"{vram_pct}%", fg=_metric_color(vram_pct, (90, 98)))
        else:
            self.rows["vram"].config(text="--", fg="#888888")

        for key in ("gpu_fan", "cpu_fan"):
            rpm = data.get(key)
            self.rows[key].config(
                text=_format_fan_reading(rpm, data.get(key + "_pct")),
                fg="#4ade80" if rpm is not None else "#888888",
            )
        if "cpu_optional_fan" in self.rows:
            rpm = data.get("cpu_optional_fan")
            self.rows["cpu_optional_fan"].config(
                text=_format_rpm(rpm), fg="#4ade80" if rpm is not None else "#888888"
            )
        case_sensors = {fan["name"]: fan for fan in data.get("fans", [])}
        for number in range(1, 7):
            key = f"case_fan_{number}"
            if key not in self.rows:
                continue
            fan = case_sensors.get(f"System Fan #{number}") or case_sensors.get(f"System Fan #{number} / Pump")
            rpm = fan.get("rpm") if fan else None
            if rpm and hasattr(self, "_seen_case_fans") and number not in self._seen_case_fans:
                self._seen_case_fans.add(number)
                self.rows[key].master.pack(fill="x", pady=1, before=self.rows["case_fan_control"].master)
            self.rows[key].config(
                text=_format_rpm(rpm), fg="#4ade80" if rpm else "#888888"
            )

        # RAM: used/total GB + %
        ram_pct = data.get("ram_pct")
        ram_used = data.get("ram_used_gb")
        ram_total = data.get("ram_total_gb")
        if ram_used is not None and ram_total is not None:
            self.rows["ram_gb"].config(
                text=f"{ram_used}/{ram_total}G",
                fg=_metric_color(ram_pct, _METRIC_THRESHOLDS["ram_pct"])
            )
        else:
            self.rows["ram_gb"].config(text="--", fg="#888888")
        if ram_pct is not None:
            self.rows["ram_pct"].config(
                text=f"{ram_pct}%", fg=_metric_color(ram_pct, _METRIC_THRESHOLDS["ram_pct"])
            )
        else:
            self.rows["ram_pct"].config(text="", fg="#888888")

        # Disks: orange name left, temp + usage% right
        disks = data.get("disks", [])
        disk_names = [d["name"] for d in disks]

        # Rebuild disk rows if disk list changed
        disk_rows_changed = disk_names != self._last_disk_names
        if disk_rows_changed:
            self._last_disk_names = disk_names
            # Destroy all children of disk_frame at once (avoids double-destroy)
            for child in list(self.disk_frame.winfo_children()):
                child.destroy()
            for key in list(self.disk_labels):
                self.rows.pop(key, None)
                self.rows.pop(key + "_usage", None)
            self.disk_labels.clear()
            # Create new rows
            for idx, disk in enumerate(disks):
                key = f"disk_{idx}"
                self._make_disk_row(key, disk["name"], parent=self.disk_frame)
                self.disk_labels.append(key)
            self._clamp_saved_position_to_visible_screen(persist=True)

        for i, key in enumerate(self.disk_labels):
            if i >= len(disks):
                break
            disk = disks[i]
            dtemp = disk.get("temp")
            if dtemp is not None:
                self.rows[key].config(text=f"{dtemp}°C", fg=disk_temp_color(dtemp, disk["name"]))
            else:
                self.rows[key].config(text="--", fg="#888888")
            used = disk.get("used_pct")
            if used is not None:
                self.rows[key + "_usage"].config(text=f"{used}%", fg=disk_usage_color(used))
            else:
                self.rows[key + "_usage"].config(text="", fg="#888888")

        _update_peak_values(self.peaks, data)
        for key, text in _detail_row_values(data, self.peaks).items():
            if key in self.rows:
                color = "#4ade80" if text != "--" else "#888888"
                if key == "detail_gpu_temps":
                    colors = [temp_color(data.get(sensor), metric) for sensor, metric in (
                        ("gpu_core_temp", "gpu_temp"),
                        ("gpu_hotspot_temp", "gpu_hotspot_temp"),
                        ("gpu_memory_temp", "gpu_memory_temp"),
                    )]
                    color = max(colors, key=("#888888", "#4ade80", "#facc15", "#f87171").index)
                self.rows[key].config(
                    text=text,
                    fg=color,
                )

        # Check critical thresholds and alert
        self._check_alerts(data)

        if hasattr(self, "health_label"):
            self.root.update_idletasks()
            size = (self.root.winfo_reqwidth(), self.root.winfo_reqheight())
            if size != getattr(self, "_thermal_layout_size", None):
                self._thermal_layout_size = size
                self._clamp_saved_position_to_visible_screen(persist=True)

        self.root.after(2000, self.update_ui)

    def _show_sensor_error(self, text="ERR", color="#f87171"):
        if hasattr(self, "advisor"):
            self.advisor.reset()
        self.thermal_findings = []
        if hasattr(self, "health_label"):
            status = getattr(self, "_case_fan_status", {})
            controller_error = status.get("state") == "error"
            self.health_label.config(
                text="Fresh sensor data unavailable" + ("\nCase fans: " + str(status.get("reason")) if controller_error else ""),
                fg="#f87171" if controller_error else "#facc15",
            )
            if controller_error:
                self._check_alerts({})
        for child in list(self.disk_frame.winfo_children()):
            child.destroy()
        for key in list(self.disk_labels):
            self.rows.pop(key, None)
            self.rows.pop(key + "_usage", None)
        self.disk_labels.clear()
        self._last_disk_names = []

        for key, label in self.rows.items():
            if key != "case_fan_control":
                label.config(text=text, fg=color)

    def quit(self):
        self._cancel_scheduled_embed()
        if hasattr(self, "fan_worker"):
            self.fan_worker.stop()
        self.running = False
        self._stop_event.set()
        # Cancel all pending after() callbacks to prevent TclError on destroy
        try:
            for after_id in list(self.root.tk.call('after', 'info') or ()):
                try:
                    self.root.after_cancel(after_id)
                except Exception:
                    log.debug("Failed to cancel after callback", exc_info=True)
        except Exception:
            log.debug("Failed to enumerate after callbacks", exc_info=True)
        # Save desktop position (not peek/animation position)
        if self._saved_pos:
            self.config["x"], self.config["y"] = self._saved_pos
        elif not self.peek_visible and not self._peek_animating and not self.embedded:
            # Only read winfo coordinates when NOT embedded (they are screen-relative)
            # When embedded, config["x"]/["y"] already track position via on_drag
            self.config["x"] = self.root.winfo_rootx()
            self.config["y"] = self.root.winfo_rooty()
        self.config["peek_enabled"] = self.peek_enabled
        self.config["alerts_enabled"] = self.alerts_enabled
        self.config["details_enabled"] = self.details_enabled
        self._save_config(update_status=False)
        # Both workers own their monitors; share one deadline for their cleanup.
        deadline = time.monotonic() + 5
        for name, worker in (
            ("Sensor", getattr(self, "sensor_thread", None)),
            ("Diagnostics", getattr(self, "_diagnostics_thread", None)),
        ):
            if worker is None or not worker.is_alive():
                continue
            try:
                worker.join(timeout=max(0.0, deadline - time.monotonic()))
            except RuntimeError:
                log.debug("Failed to join %s thread", name.lower(), exc_info=True)
            if worker.is_alive():
                log.warning(
                    "%s thread did not stop in time; hardware cleanup remains with the worker",
                    name,
                )
        self.root.destroy()
        release_single_instance()

    def run(self):
        self.root.mainloop()


def _is_admin():
    """Check if the current process has administrator privileges."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def main():
    dll_errors = _runtime_dll_errors()
    if dll_errors:
        details = "\n".join(f"- {message}" for message in dll_errors[:8])
        if len(dll_errors) > 8:
            details += f"\n- ... and {len(dll_errors) - 8} more"
        _show_error_message(
            "HW Monitor",
            "Hardware monitor runtime is missing or corrupted:\n"
            f"{details}\n\nRun: python setup.py --verify\nThen run: python setup.py",
        )
        sys.exit(1)

    if not acquire_single_instance():
        log.warning("Another HeatMap instance is already running")
        return

    try:
        autostart_result = None
        if _is_admin():
            autostart_result = reconcile_autostart_security()
            if not autostart_result.ok:
                operation = (
                    "migrate insecure autostart task"
                    if autostart_result.changed
                    else "verify autostart security"
                )
                log.error("Failed to %s: %s", operation, autostart_result.message)
                _show_error_message(
                    "HeatMap autostart",
                    _format_autostart_reconcile_error(
                        autostart_result.changed, autostart_result.message
                    ),
                )
        else:
            log.warning("Running without admin privileges — hardware sensors may be unavailable")
        app = OverlayApp(autostart_result=autostart_result)
        try:
            app.run()
        except KeyboardInterrupt:
            app.quit()
    finally:
        release_single_instance()


if __name__ == "__main__":
    LOG_PATH = _configure_logging()
    main()
