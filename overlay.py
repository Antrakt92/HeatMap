"""
Desktop Temperature Overlay
Desktop widget showing hardware temperatures and usage.
Sits on the desktop layer — above wallpaper, below all app windows.
Requires admin privileges to read hardware sensors.
"""
import copy
import ctypes
import re
import ctypes.wintypes
import json
import logging
import base64
import os
import sys
import threading
import time
import tkinter as tk
import winreg
import winsound

import psutil

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("HeatMap")

# --- Paths ---
APP_DIR = os.path.dirname(os.path.abspath(__file__))
LIB_DIR = os.path.join(APP_DIR, "lib")
CONFIG_PATH = os.path.join(APP_DIR, "overlay_config.json")
SCRIPT_PATH = os.path.join(APP_DIR, "overlay.py")

# --- Windows API constants ---
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010
HWND_BOTTOM = 1
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
    ctypes.POINTER(ctypes.wintypes.DWORD),
]
user32.SendMessageTimeoutW.restype = ctypes.wintypes.LPARAM
user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM), ctypes.wintypes.LPARAM]
user32.FindWindowExW.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.HWND, ctypes.c_wchar_p, ctypes.c_wchar_p]
user32.FindWindowExW.restype = ctypes.wintypes.HWND
user32.SetParent.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.HWND]
user32.SetParent.restype = ctypes.wintypes.HWND

class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
user32.GetCursorPos.restype = ctypes.c_bool
user32.WindowFromPoint.argtypes = [POINT]
user32.WindowFromPoint.restype = ctypes.wintypes.HWND
user32.GetAncestor.argtypes = [ctypes.wintypes.HWND, ctypes.c_uint]
user32.GetAncestor.restype = ctypes.wintypes.HWND
user32.GetClassName = user32.GetClassNameW
user32.GetClassName.argtypes = [ctypes.wintypes.HWND, ctypes.c_wchar_p, ctypes.c_int]
user32.GetClassName.restype = ctypes.c_int
GA_ROOT = 2

# Virtual screen metrics (all monitors combined)
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.GetSystemMetrics.restype = ctypes.c_int

HWND_TOPMOST = -1
HWND_NOTOPMOST = -2

# --- Desktop widget: embed window into the desktop layer ---
def find_desktop_worker_w():
    """Find the WorkerW window behind desktop icons for widget embedding."""
    progman = user32.FindWindowW("Progman", None)
    if not progman:
        return None

    # Send Progman a 0x052C message to spawn a WorkerW behind the icons
    result = ctypes.wintypes.DWORD(0)
    user32.SendMessageTimeoutW(progman, 0x052C, 0, 0, 0x0000, 1000, ctypes.byref(result))

    worker_w = None

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def enum_callback(hwnd, lparam):
        nonlocal worker_w
        shell_view = user32.FindWindowExW(hwnd, 0, "SHELLDLL_DefView", None)
        if shell_view:
            # The WorkerW we want is the NEXT one after the one containing SHELLDLL_DefView
            worker_w = user32.FindWindowExW(0, hwnd, "WorkerW", None)
        return True

    user32.EnumWindows(enum_callback, 0)
    return worker_w


def embed_in_desktop(hwnd):
    """Make the tkinter window a child of the desktop WorkerW layer."""
    worker_w = find_desktop_worker_w()
    if worker_w:
        user32.SetParent(hwnd, worker_w)
        return True
    return False


def set_tool_window(hwnd):
    """Remove from taskbar and alt-tab, make non-activating."""
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    style |= WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)


# --- Autostart management ---
AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_NAME = "HWMonitorOverlay"


def get_pythonw_path():
    """Get path to pythonw.exe (no console window)."""
    python_dir = os.path.dirname(sys.executable)
    pythonw = os.path.join(python_dir, "pythonw.exe")
    if os.path.exists(pythonw):
        return pythonw
    return sys.executable


def is_autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, AUTOSTART_NAME)
        return True
    except FileNotFoundError:
        return False
    except Exception:
        log.warning("Failed to check autostart status", exc_info=True)
        return False


def enable_autostart():
    """Add to Windows startup registry with admin elevation."""
    try:
        pythonw = get_pythonw_path()
        # Escape single quotes for PowerShell string literals
        safe_pythonw = pythonw.replace("'", "''")
        safe_script = SCRIPT_PATH.replace("'", "''")
        ps_script = (
            f"Start-Process '{safe_pythonw}' "
            f"-ArgumentList '\"{safe_script}\"' "
            f"-Verb RunAs"
        )
        encoded = base64.b64encode(ps_script.encode("utf-16-le")).decode("ascii")
        cmd = f'powershell -WindowStyle Hidden -EncodedCommand {encoded}'
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, cmd)
        return True
    except Exception:
        log.warning("Failed to enable autostart", exc_info=True)
        return False


def disable_autostart():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, AUTOSTART_NAME)
        return True
    except Exception:
        log.warning("Failed to disable autostart", exc_info=True)
        return False


# --- Load LibreHardwareMonitor ---
def init_hardware_monitor():
    """Initialize LibreHardwareMonitor via pythonnet."""
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
        return computer
    except Exception:
        log.warning("Failed to init LibreHardwareMonitor, falling back to psutil", exc_info=True)
        return None


def read_sensors(computer):
    """Read all temperature and load sensors from hardware."""
    data = {
        "cpu_temp": None,
        "cpu_load": None,
        "gpu_temp": None,
        "gpu_load": None,
        "cpu_fan": None,
        "cpu_fan_pct": None,
        "gpu_fan": None,
        "gpu_fan_pct": None,
        "gpu_vram_pct": None,
        "ram_pct": None,
        "disks": [],
    }

    if computer is None:
        data["cpu_load"] = psutil.cpu_percent(interval=0.1)
        data["ram_pct"] = round(psutil.virtual_memory().percent)
        return data

    from LibreHardwareMonitor.Hardware import HardwareType, SensorType

    for hw in computer.Hardware:
        hw.Update()
        for sub in hw.SubHardware:
            sub.Update()

        hw_type = hw.HardwareType

        if hw_type == HardwareType.Cpu:
            for sensor in hw.Sensors:
                if sensor.SensorType == SensorType.Temperature:
                    name = sensor.Name.lower()
                    if sensor.Value is not None:
                        if "tctl" in name or "tdie" in name or "package" in name:
                            data["cpu_temp"] = round(float(sensor.Value))
                        elif data["cpu_temp"] is None:
                            data["cpu_temp"] = round(float(sensor.Value))
                elif sensor.SensorType == SensorType.Load:
                    if "total" in sensor.Name.lower():
                        if sensor.Value is not None:
                            data["cpu_load"] = round(float(sensor.Value))

        elif hw_type in (HardwareType.GpuAmd, HardwareType.GpuNvidia, HardwareType.GpuIntel):
            # Skip integrated Intel GPU if we already have data from a discrete GPU
            if hw_type == HardwareType.GpuIntel and data["gpu_temp"] is not None:
                continue  # discrete GPU data already collected, skip Intel iGPU
            gpu_mem_used = None
            gpu_mem_total = None
            for sensor in hw.Sensors:
                if sensor.SensorType == SensorType.Temperature:
                    if "core" in sensor.Name.lower() or "gpu" in sensor.Name.lower():
                        if sensor.Value is not None:
                            data["gpu_temp"] = round(float(sensor.Value))
                elif sensor.SensorType == SensorType.Load:
                    name = sensor.Name.lower()
                    if name == "gpu core":
                        if sensor.Value is not None:
                            data["gpu_load"] = round(float(sensor.Value))
                elif sensor.SensorType == SensorType.Fan:
                    if sensor.Value is not None:
                        data["gpu_fan"] = round(float(sensor.Value))
                elif sensor.SensorType == SensorType.Control:
                    if sensor.Value is not None:
                        data["gpu_fan_pct"] = round(float(sensor.Value))
                elif sensor.SensorType == SensorType.SmallData:
                    name = sensor.Name.lower()
                    if name == "gpu memory used" and sensor.Value is not None:
                        gpu_mem_used = float(sensor.Value)
                    elif name == "gpu memory total" and sensor.Value is not None:
                        gpu_mem_total = float(sensor.Value)
            if gpu_mem_used is not None and gpu_mem_total and gpu_mem_total > 0:
                data["gpu_vram_pct"] = round(gpu_mem_used / gpu_mem_total * 100)

        elif hw_type == HardwareType.Storage:
            disk_temp = None
            disk_used = None
            for sensor in hw.Sensors:
                if sensor.SensorType == SensorType.Temperature:
                    if sensor.Value is not None and "temperature" in sensor.Name.lower():
                        if disk_temp is None:
                            disk_temp = round(float(sensor.Value))
                elif sensor.SensorType == SensorType.Load:
                    if "used space" in sensor.Name.lower() and sensor.Value is not None:
                        disk_used = round(float(sensor.Value))
            if disk_temp is not None or disk_used is not None:
                name = re.sub(
                    r"^(Samsung|WDC|Western Digital|Kingston|Crucial|Seagate|Toshiba|SK Hynix|Intel|Micron|SanDisk|ADATA|Corsair)\s*(SSD\s*)?",
                    "", str(hw.Name), flags=re.IGNORECASE,
                ).strip() or str(hw.Name)
                data["disks"].append({
                    "name": name,
                    "temp": disk_temp,
                    "used_pct": disk_used,
                })

        elif hw_type == HardwareType.Motherboard:
            for sub in hw.SubHardware:
                control_sensors = []
                for sensor in sub.Sensors:
                    name = sensor.Name.lower()
                    if sensor.SensorType == SensorType.Fan:
                        if sensor.Value is not None:
                            if "cpu" in name and "optional" not in name:
                                data["cpu_fan"] = round(float(sensor.Value))
                    elif sensor.SensorType == SensorType.Control:
                        if sensor.Value is not None:
                            control_sensors.append((name, round(float(sensor.Value))))
                # Match CPU fan percentage: prefer "cpu" in name, then "#1", then first control
                if data["cpu_fan_pct"] is None:
                    for cname, cval in control_sensors:
                        if "cpu" in cname:
                            data["cpu_fan_pct"] = cval
                            break
                    else:
                        for cname, cval in control_sensors:
                            if "#1" in cname:
                                data["cpu_fan_pct"] = cval
                                break
                        else:
                            if control_sensors and data["cpu_fan"] is not None:
                                data["cpu_fan_pct"] = control_sensors[0][1]

        elif hw_type == HardwareType.Memory:
            for sensor in hw.Sensors:
                if sensor.SensorType == SensorType.Load:
                    if sensor.Name.lower() == "memory" and sensor.Value is not None:
                        data["ram_pct"] = round(float(sensor.Value))

    if data["cpu_load"] is None:
        data["cpu_load"] = psutil.cpu_percent(interval=0.1)
    if data["ram_pct"] is None:
        data["ram_pct"] = round(psutil.virtual_memory().percent)

    return data


# --- Color coding ---
def temp_color(temp):
    if temp is None:
        return "#888888"
    if temp < 55:
        return "#4ade80"
    if temp < 75:
        return "#facc15"
    return "#f87171"


def load_color(load):
    if load is None:
        return "#888888"
    if load < 50:
        return "#4ade80"
    if load < 80:
        return "#facc15"
    return "#f87171"


def disk_usage_color(pct):
    if pct is None:
        return "#888888"
    if pct < 70:
        return "#4ade80"
    if pct < 85:
        return "#facc15"
    return "#f87171"


# --- Config ---
def load_config():
    defaults = {"x": 50, "y": 50, "peek_enabled": True, "alerts_enabled": True,
                "gpu_fan_max_rpm": 2200, "cpu_fan_max_rpm": 1800}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            return defaults
        # Validate types, fall back to defaults for bad values
        for key in ("x", "y"):
            if not isinstance(cfg.get(key), (int, float)):
                cfg[key] = defaults[key]
            else:
                cfg[key] = int(cfg[key])
        for key in ("peek_enabled", "alerts_enabled"):
            if not isinstance(cfg.get(key), bool):
                cfg[key] = defaults[key]
        for key in ("gpu_fan_max_rpm", "cpu_fan_max_rpm"):
            if not isinstance(cfg.get(key), (int, float)) or cfg[key] <= 0:
                cfg[key] = defaults[key]
            else:
                cfg[key] = int(cfg[key])
        return cfg
    except Exception:
        return defaults


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f)
    except Exception:
        log.warning("Failed to save config to %s", CONFIG_PATH, exc_info=True)


# --- Main overlay class ---
class OverlayApp:
    def __init__(self):
        self.computer = init_hardware_monitor()
        self.config = load_config()
        # Validate saved position is within visible screen area
        virt_x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        virt_y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        virt_w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        virt_h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        cx, cy = self.config.get("x", 50), self.config.get("y", 50)
        if cx < virt_x or cx >= virt_x + virt_w or cy < virt_y or cy >= virt_y + virt_h:
            self.config["x"], self.config["y"] = 50, 50
        self.running = True
        self._stop_event = threading.Event()
        self.sensor_data = {}
        self.lock = threading.Lock()
        self.embedded = False

        # --- Alert system ---
        self.alerts_enabled = self.config.get("alerts_enabled", True)
        self._last_alert_time = 0
        self._ALERT_COOLDOWN = 60  # seconds between repeated alerts
        self._CRITICAL = {
            "cpu_temp": 85,
            "gpu_temp": 90,
            "disk_temp": 55,
            "disk_used": 90,
            "ram_pct": 95,
        }
        # Max RPM for fan % estimation (auto-calibrated, persisted in config)
        self._GPU_FAN_MAX_RPM = self.config.get("gpu_fan_max_rpm", 2200)
        self._CPU_FAN_MAX_RPM = self.config.get("cpu_fan_max_rpm", 1800)

        # --- tkinter setup ---
        self.root = tk.Tk()
        self.root.title("Temp Overlay")
        self.root.overrideredirect(True)
        self.root.wm_attributes("-alpha", 0.88)
        self.root.configure(bg="#1a1a2e")

        # Hide until embedded in desktop to prevent blink
        self.root.withdraw()

        # Position from saved config
        self.root.geometry(f"+{self.config.get('x', 50)}+{self.config.get('y', 50)}")

        # --- Drag support ---
        self._drag_x = 0
        self._drag_y = 0

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

        # CPU group — temp + load as two separate colored values
        cpu_row = tk.Frame(self.content, bg="#1a1a2e")
        cpu_row.pack(fill="x", pady=1)
        tk.Label(cpu_row, text=" CPU", font=("Segoe UI", 10, "bold"),
                 fg=CPU_CLR, bg="#1a1a2e", width=6, anchor="w").pack(side="left")
        self.rows["cpu_load"] = tk.Label(cpu_row, text="", font=("Segoe UI", 10),
                                         fg="#888888", bg="#1a1a2e", anchor="e")
        self.rows["cpu_load"].pack(side="right")
        self.rows["cpu_temp"] = tk.Label(cpu_row, text="--", font=("Segoe UI", 10),
                                         fg="#888888", bg="#1a1a2e", anchor="e")
        self.rows["cpu_temp"].pack(side="right", padx=(0, 4))

        self._make_row("cpu_fan", "C.FAN", label_fg=CPU_CLR)
        tk.Frame(self.content, bg="#2a2a4e", height=1).pack(fill="x", pady=2)

        # GPU group — temp + load as two separate colored values
        gpu_row = tk.Frame(self.content, bg="#1a1a2e")
        gpu_row.pack(fill="x", pady=1)
        tk.Label(gpu_row, text=" GPU", font=("Segoe UI", 10, "bold"),
                 fg=GPU_CLR, bg="#1a1a2e", width=6, anchor="w").pack(side="left")
        self.rows["gpu_load"] = tk.Label(gpu_row, text="", font=("Segoe UI", 10),
                                         fg="#888888", bg="#1a1a2e", anchor="e")
        self.rows["gpu_load"].pack(side="right")
        self.rows["gpu_temp"] = tk.Label(gpu_row, text="--", font=("Segoe UI", 10),
                                         fg="#888888", bg="#1a1a2e", anchor="e")
        self.rows["gpu_temp"].pack(side="right", padx=(0, 4))

        self._make_row("vram", "VRAM", label_fg=GPU_CLR)
        self._make_row("gpu_fan", "G.FAN", label_fg=GPU_CLR)
        tk.Frame(self.content, bg="#2a2a4e", height=1).pack(fill="x", pady=2)

        # RAM
        self._make_row("ram", "RAM", label_fg=RAM_CLR)
        tk.Frame(self.content, bg="#2a2a4e", height=1).pack(fill="x", pady=2)

        # Disk rows created dynamically
        self.disk_frame = tk.Frame(self.content, bg="#1a1a2e")
        self.disk_frame.pack(fill="x")
        self.disk_labels = []
        self._last_disk_names = []

        # Bottom padding
        tk.Frame(self.content, bg="#1a1a2e", height=2).pack()

        # --- Right-click menu ---
        self.topmost = False
        self.menu = tk.Menu(self.root, tearoff=0, bg="#1a1a2e", fg="#a0a0c0",
                           activebackground="#2a2a4e", activeforeground="white",
                           font=("Segoe UI", 9))
        self._menu_idx = {}  # label_key -> menu index
        self._add_menu_item("topmost", "Always on top: OFF", self.toggle_topmost)
        self._add_menu_item("autostart",
            "Autostart: ON" if is_autostart_enabled() else "Autostart: OFF",
            self.toggle_autostart)
        self._add_menu_item("alerts",
            "Alerts: ON" if self.alerts_enabled else "Alerts: OFF",
            self.toggle_alerts)
        self.peek_enabled = self.config.get("peek_enabled", True)
        self._add_menu_item("peek",
            "Peek from edge: ON" if self.peek_enabled else "Peek from edge: OFF",
            self.toggle_peek)
        self.menu.add_separator()
        self.menu.add_command(label="Close", command=self.quit)
        self.root.bind("<Button-3>", self.show_menu)

        # --- Peek from edge ---
        self.peek_visible = False
        self._peek_animating = False
        self._saved_pos = None  # saved desktop position before peek
        self._create_peek_trigger()

        # --- Embed into desktop after window is drawn ---
        self.root.after(100, self._embed_into_desktop)

        # --- Start sensor thread ---
        self.sensor_thread = threading.Thread(target=self.sensor_loop, daemon=True)
        self.sensor_thread.start()

        # --- Start UI update loop ---
        self.update_ui()

    def _add_menu_item(self, key, label, command):
        """Add a menu command and track its index by key."""
        self.menu.add_command(label=label, command=command)
        self._menu_idx[key] = self.menu.index("end")

    def _set_menu_label(self, key, label):
        """Update a menu item's label by its key."""
        self.menu.entryconfig(self._menu_idx[key], label=label)

    def _get_hwnd(self):
        """Get the native Windows HWND for the tkinter root window."""
        self.root.update_idletasks()
        frame_id = self.root.wm_frame()
        if frame_id:
            try:
                hwnd = int(frame_id, 16)
            except (ValueError, TypeError):
                hwnd = 0
            if hwnd:
                return hwnd
        return self.root.winfo_id()

    def _embed_into_desktop(self):
        """Embed the window into the desktop layer (above wallpaper, below icons and apps)."""
        hwnd = self._get_hwnd()
        self._hwnd = hwnd
        set_tool_window(hwnd)
        if embed_in_desktop(hwnd):
            self.embedded = True
        else:
            # Fallback: just send to bottom, no topmost
            user32.SetWindowPos(hwnd, HWND_BOTTOM, 0, 0, 0, 0,
                               SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        # Show window after embedding to prevent blink
        self.root.deiconify()

    def _make_row(self, key, label_text, parent=None, label_fg="#a0a0c0"):
        parent = parent or self.content
        row = tk.Frame(parent, bg="#1a1a2e")
        row.pack(fill="x", pady=1)
        tk.Label(
            row, text=f" {label_text}", font=("Segoe UI", 10, "bold"),
            fg=label_fg, bg="#1a1a2e", width=6, anchor="w"
        ).pack(side="left")
        val_lbl = tk.Label(
            row, text="--", font=("Segoe UI", 10),
            fg="#888888", bg="#1a1a2e", anchor="e"
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

    # --- Peek from edge methods ---
    def _create_peek_trigger(self):
        """Create an invisible strip on the right edge of the screen."""
        self._trigger = tk.Toplevel(self.root)
        self._trigger.overrideredirect(True)
        self._trigger.wm_attributes("-alpha", 0.01)
        self._trigger.wm_attributes("-topmost", True)
        self._trigger.configure(bg="black")

        self._update_trigger_geometry()

        # Make it a tool window (no taskbar, no alt-tab)
        self._trigger.update_idletasks()
        try:
            trigger_hwnd = int(self._trigger.wm_frame(), 16) or self._trigger.winfo_id()
        except (ValueError, TypeError):
            trigger_hwnd = self._trigger.winfo_id()
        set_tool_window(trigger_hwnd)

        self._trigger.bind("<Enter>", lambda _: self._peek_show())

        if not self.peek_enabled:
            self._trigger.withdraw()

        # Periodically re-check screen geometry for resolution/monitor changes
        self._last_screen_x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        self._last_screen_y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        self._last_screen_w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        self._last_screen_h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        self._poll_screen_change()

    def _update_trigger_geometry(self):
        """Position the trigger strip on the right edge of the virtual screen (all monitors)."""
        virt_x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        virt_y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        virt_w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        virt_h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        trigger_w = 6
        self._trigger.geometry(f"{trigger_w}x{virt_h}+{virt_x + virt_w - trigger_w}+{virt_y}")

    def _poll_screen_change(self):
        """Re-position trigger strip if screen geometry changed (resolution or monitor rearrangement)."""
        screen_x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        screen_y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        screen_w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        screen_h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        if (screen_x != self._last_screen_x or screen_y != self._last_screen_y
                or screen_w != self._last_screen_w or screen_h != self._last_screen_h):
            self._last_screen_x = screen_x
            self._last_screen_y = screen_y
            self._last_screen_w = screen_w
            self._last_screen_h = screen_h
            self._update_trigger_geometry()
        self.root.after(5000, self._poll_screen_change)

    def _is_desktop_visible(self):
        """Check if the desktop is visible (no app windows covering the widget area)."""
        # Check a point near the widget's actual desktop position
        if self._saved_pos:
            x, y = self._saved_pos[0] + 10, self._saved_pos[1] + 10
        else:
            x = self.root.winfo_rootx() + 10
            y = self.root.winfo_rooty() + 10
        pt = POINT(x, y)
        hwnd = user32.WindowFromPoint(pt)
        if not hwnd:
            return True
        # Walk up to root window and check its class
        root_hwnd = user32.GetAncestor(hwnd, GA_ROOT)
        if not root_hwnd:
            root_hwnd = hwnd
        class_name = ctypes.create_unicode_buffer(256)
        user32.GetClassName(root_hwnd, class_name, 256)
        name = class_name.value
        # Desktop-related window classes
        return name in ("Progman", "WorkerW", "")

    def _peek_show(self):
        """Slide the overlay in from the right edge."""
        if self.peek_visible or self._peek_animating or self.topmost:
            return
        if self._is_desktop_visible():
            return

        self._peek_animating = True

        # Save current desktop position
        self._saved_pos = (self.config.get("x", 50), self.config.get("y", 50))

        # Unembed from desktop
        if self.embedded:
            hwnd = self._get_hwnd()
            user32.SetParent(hwnd, 0)
            self.embedded = False

        # Make topmost
        self.root.wm_attributes("-topmost", True)

        virt_x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        virt_w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        screen_right = virt_x + virt_w
        self.root.update_idletasks()
        overlay_w = self.root.winfo_width()

        # Keep the same Y position as on the desktop
        target_x = screen_right - overlay_w
        target_y = self._saved_pos[1] if self._saved_pos else self.config.get("y", 50)

        # Start off-screen
        self.root.geometry(f"+{screen_right}+{target_y}")
        self.root.update_idletasks()

        # Animate slide-in
        self._animate_slide(screen_right, target_x, target_y, step=-20, callback=self._peek_shown)

    def _animate_slide(self, current_x, target_x, y, step, callback):
        """Animate horizontal slide."""
        if not self.running or not self._peek_animating:
            return
        if step < 0 and current_x <= target_x:
            self.root.geometry(f"+{target_x}+{y}")
            callback()
            return
        if step > 0 and current_x >= target_x:
            self.root.geometry(f"+{target_x}+{y}")
            callback()
            return
        self.root.geometry(f"+{current_x}+{y}")
        self.root.after(10, lambda: self._animate_slide(current_x + step, target_x, y, step, callback))

    def _peek_shown(self):
        """Called when slide-in animation finishes."""
        self._peek_animating = False
        self.peek_visible = True
        self._peek_check_mouse()

    def _peek_check_mouse(self):
        """Poll mouse position — hide when cursor leaves overlay and trigger."""
        if not self.peek_visible or self._peek_animating:
            return

        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        mx, my = pt.x, pt.y

        # Check if mouse is over the overlay
        ox = self.root.winfo_rootx()
        oy = self.root.winfo_rooty()
        ow = self.root.winfo_width()
        oh = self.root.winfo_height()
        over_overlay = ox <= mx <= ox + ow and oy <= my <= oy + oh

        # Check if mouse is over the trigger strip (right edge of virtual screen)
        virt_x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        virt_w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        over_trigger = mx >= virt_x + virt_w - 10

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

        screen_right = user32.GetSystemMetrics(SM_XVIRTUALSCREEN) + user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        current_x = self.root.winfo_rootx()
        current_y = self.root.winfo_rooty()

        self._animate_slide(current_x, screen_right, current_y, step=20, callback=self._peek_hidden)

    def _peek_hidden(self):
        """Called when slide-out animation finishes."""
        self._peek_animating = False
        self.peek_visible = False

        # Hide before repositioning to prevent blink
        self.root.withdraw()

        # Restore topmost off
        self.root.wm_attributes("-topmost", False)

        # Restore saved desktop position and update config
        if self._saved_pos:
            x, y = self._saved_pos
            self.root.geometry(f"+{x}+{y}")
            self.config["x"] = x
            self.config["y"] = y
            self._saved_pos = None

        # Re-embed in desktop (deiconify happens inside _embed_into_desktop)
        self.root.after(50, self._embed_into_desktop)

    def toggle_peek(self):
        self.peek_enabled = not self.peek_enabled
        self.config["peek_enabled"] = self.peek_enabled
        save_config(self.config)
        if self.peek_enabled and not self.topmost:
            self._trigger.deiconify()
        else:
            self._trigger.withdraw()
            if self.peek_visible:
                self._peek_hide()
        self._set_menu_label("peek",
            "Peek from edge: ON" if self.peek_enabled else "Peek from edge: OFF"
        )

    def toggle_topmost(self):
        self.topmost = not self.topmost
        if self.topmost:
            # Hide peek trigger — not needed in topmost mode
            self._trigger.withdraw()
            if self.peek_visible or self._peek_animating:
                self.peek_visible = False
                self._peek_animating = False
            # Unembed from desktop if embedded, make topmost
            if self.embedded:
                hwnd = self._get_hwnd()
                user32.SetParent(hwnd, 0)
                self.embedded = False
            # Restore saved position if we were peeking
            if self._saved_pos:
                x, y = self._saved_pos
                self.root.geometry(f"+{x}+{y}")
                self._saved_pos = None
            self.root.wm_attributes("-topmost", True)
        else:
            self.root.wm_attributes("-topmost", False)
            self.root.after(100, self._embed_into_desktop)
            # Restore peek trigger if enabled
            if self.peek_enabled:
                self._trigger.deiconify()
        self._set_menu_label("topmost",
            "Always on top: ON" if self.topmost else "Always on top: OFF"
        )

    def toggle_autostart(self):
        if is_autostart_enabled():
            disable_autostart()
        else:
            enable_autostart()
        self._set_menu_label("autostart",
            "Autostart: ON" if is_autostart_enabled() else "Autostart: OFF"
        )

    def toggle_alerts(self):
        self.alerts_enabled = not self.alerts_enabled
        self.config["alerts_enabled"] = self.alerts_enabled
        save_config(self.config)
        self._set_menu_label("alerts",
            "Alerts: ON" if self.alerts_enabled else "Alerts: OFF"
        )

    def _check_alerts(self, data):
        """Play a warning beep if any value exceeds critical thresholds."""
        if not self.alerts_enabled:
            return
        now = time.time()
        if now - self._last_alert_time < self._ALERT_COOLDOWN:
            return

        alerts = []
        cpu_temp = data.get("cpu_temp")
        if cpu_temp is not None and cpu_temp >= self._CRITICAL["cpu_temp"]:
            alerts.append(f"CPU {cpu_temp}°C")

        gpu_temp = data.get("gpu_temp")
        if gpu_temp is not None and gpu_temp >= self._CRITICAL["gpu_temp"]:
            alerts.append(f"GPU {gpu_temp}°C")

        ram_pct = data.get("ram_pct")
        if ram_pct is not None and ram_pct >= self._CRITICAL["ram_pct"]:
            alerts.append(f"RAM {ram_pct}%")

        for disk in data.get("disks", []):
            dtemp = disk.get("temp")
            if dtemp is not None and dtemp >= self._CRITICAL["disk_temp"]:
                alerts.append(f"{disk['name']} {dtemp}°C")
            used = disk.get("used_pct")
            if used is not None and used >= self._CRITICAL["disk_used"]:
                alerts.append(f"{disk['name']} {used}%")

        if alerts:
            self._last_alert_time = now
            # Beep in a thread to avoid blocking UI
            def _alert_beep():
                winsound.Beep(1000, 300)
                time.sleep(0.15)
                winsound.Beep(1000, 300)
            threading.Thread(target=_alert_beep, daemon=True).start()

    def start_drag(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def on_drag(self, event):
        x = self.root.winfo_x() + event.x - self._drag_x
        y = self.root.winfo_y() + event.y - self._drag_y
        self.root.geometry(f"+{x}+{y}")
        if self.peek_visible or self._peek_animating:
            self._saved_pos = (x, y)
        else:
            self.config["x"] = x
            self.config["y"] = y

    def end_drag(self, _event):
        # If dragged during peek, persist the new position into config
        if self._saved_pos:
            self.config["x"], self.config["y"] = self._saved_pos
        save_config(self.config)

    def show_menu(self, event):
        self.menu.tk_popup(event.x_root, event.y_root)

    def sensor_loop(self):
        consecutive_errors = 0
        while not self._stop_event.is_set():
            try:
                with self.lock:
                    computer = self.computer
                data = read_sensors(computer)
                with self.lock:
                    self.sensor_data = data
                consecutive_errors = 0
            except Exception as e:
                consecutive_errors += 1
                log.error("Sensor read error: %s", e, exc_info=True)
                with self.lock:
                    self.sensor_data = {"error": str(e)}
                # After 3 consecutive failures, try to reinitialize the hardware monitor
                if consecutive_errors >= 3 and computer is not None:
                    log.warning("Reinitializing hardware monitor after %d errors", consecutive_errors)
                    with self.lock:
                        try:
                            self.computer.Close()
                        except Exception:
                            pass
                        self.computer = init_hardware_monitor()
                    consecutive_errors = 0
            self._stop_event.wait(2)

    def update_ui(self):
        if not self.running:
            return

        with self.lock:
            data = copy.deepcopy(self.sensor_data)

        if not data:
            self.root.after(500, self.update_ui)
            return

        if "error" in data:
            self.root.after(2000, self.update_ui)
            return

        # CPU: temp (colored by temp) + load% (colored by load)
        cpu_temp = data.get("cpu_temp")
        cpu_load = data.get("cpu_load")
        self.rows["cpu_temp"].config(
            text=f"{cpu_temp}°C" if cpu_temp is not None else "--",
            fg=temp_color(cpu_temp)
        )
        self.rows["cpu_load"].config(
            text=f"{cpu_load}%" if cpu_load is not None else "",
            fg=load_color(cpu_load)
        )

        # GPU: temp (colored by temp) + load% (colored by load)
        gpu_temp = data.get("gpu_temp")
        gpu_load = data.get("gpu_load")
        self.rows["gpu_temp"].config(
            text=f"{gpu_temp}°C" if gpu_temp is not None else "--",
            fg=temp_color(gpu_temp)
        )
        self.rows["gpu_load"].config(
            text=f"{gpu_load}%" if gpu_load is not None else "",
            fg=load_color(gpu_load)
        )

        # VRAM: usage %
        vram_pct = data.get("gpu_vram_pct")
        if vram_pct is not None:
            self.rows["vram"].config(text=f"{vram_pct}%", fg=load_color(vram_pct))
        else:
            self.rows["vram"].config(text="--", fg="#888888")

        # Auto-calibrate fan RPM max from observed values
        gpu_fan = data.get("gpu_fan")
        cpu_fan = data.get("cpu_fan")
        if gpu_fan is not None and gpu_fan > self._GPU_FAN_MAX_RPM:
            self._GPU_FAN_MAX_RPM = gpu_fan
        if cpu_fan is not None and cpu_fan > self._CPU_FAN_MAX_RPM:
            self._CPU_FAN_MAX_RPM = cpu_fan

        # GPU FAN: prefer %, fallback RPM
        gpu_fan_pct = data.get("gpu_fan_pct")
        if gpu_fan_pct is not None:
            if gpu_fan_pct == 0:
                self.rows["gpu_fan"].config(text="OFF", fg="#4ade80")
            else:
                self.rows["gpu_fan"].config(text=f"{gpu_fan_pct}%", fg=load_color(gpu_fan_pct))
        elif gpu_fan is not None:
            if gpu_fan == 0:
                self.rows["gpu_fan"].config(text="OFF", fg="#4ade80")
            else:
                est_pct = min(100, round(gpu_fan / self._GPU_FAN_MAX_RPM * 100))
                self.rows["gpu_fan"].config(text=f"~{est_pct}%", fg=load_color(est_pct))
        else:
            self.rows["gpu_fan"].config(text="--", fg="#888888")

        # CPU FAN: show % (same style as GPU fan)
        cpu_fan_pct = data.get("cpu_fan_pct")
        if cpu_fan_pct is not None:
            if cpu_fan_pct == 0:
                self.rows["cpu_fan"].config(text="OFF", fg="#4ade80")
            else:
                self.rows["cpu_fan"].config(text=f"{cpu_fan_pct}%", fg=load_color(cpu_fan_pct))
        elif cpu_fan is not None:
            if cpu_fan == 0:
                self.rows["cpu_fan"].config(text="OFF", fg="#4ade80")
            else:
                est_pct = min(100, round(cpu_fan / self._CPU_FAN_MAX_RPM * 100))
                self.rows["cpu_fan"].config(text=f"~{est_pct}%", fg=load_color(est_pct))
        else:
            self.rows["cpu_fan"].config(text="--", fg="#888888")

        # RAM: usage %
        ram_pct = data.get("ram_pct")
        if ram_pct is not None:
            self.rows["ram"].config(text=f"{ram_pct}%", fg=load_color(ram_pct))
        else:
            self.rows["ram"].config(text="--", fg="#888888")

        # Disks: orange name left, temp + usage% right
        disks = data.get("disks", [])
        disk_names = [d["name"] for d in disks]

        # Rebuild disk rows if disk list changed
        if disk_names != self._last_disk_names:
            self._last_disk_names = disk_names
            # Destroy old disk widgets
            for key in list(self.disk_labels):
                try:
                    self.rows[key].master.destroy()
                except Exception:
                    pass
                self.rows.pop(key, None)
                self.rows.pop(key + "_usage", None)
            self.disk_labels.clear()
            # Create new rows
            for idx, disk in enumerate(disks):
                key = f"disk_{idx}"
                self._make_disk_row(key, disk["name"], parent=self.disk_frame)
                self.disk_labels.append(key)

        for i, key in enumerate(self.disk_labels):
            if i >= len(disks):
                break
            disk = disks[i]
            dtemp = disk.get("temp")
            if dtemp is not None:
                self.rows[key].config(text=f"{dtemp}°C", fg=temp_color(dtemp))
            else:
                self.rows[key].config(text="--", fg="#888888")
            used = disk.get("used_pct")
            if used is not None:
                self.rows[key + "_usage"].config(text=f"{used}%", fg=disk_usage_color(used))
            else:
                self.rows[key + "_usage"].config(text="", fg="#888888")

        # Check critical thresholds and alert
        self._check_alerts(data)

        self.root.after(2000, self.update_ui)

    def quit(self):
        self.running = False
        self._stop_event.set()
        # Cancel all pending after() callbacks to prevent TclError on destroy
        try:
            for after_id in list(self.root.tk.call('after', 'info') or ()):
                try:
                    self.root.after_cancel(after_id)
                except Exception:
                    pass
        except Exception:
            pass
        # Save desktop position (not peek/animation position)
        if self._saved_pos:
            self.config["x"], self.config["y"] = self._saved_pos
        elif not self.peek_visible and not self._peek_animating:
            self.config["x"] = self.root.winfo_rootx()
            self.config["y"] = self.root.winfo_rooty()
        self.config["peek_enabled"] = self.peek_enabled
        self.config["alerts_enabled"] = self.alerts_enabled
        self.config["gpu_fan_max_rpm"] = self._GPU_FAN_MAX_RPM
        self.config["cpu_fan_max_rpm"] = self._CPU_FAN_MAX_RPM
        save_config(self.config)
        # Destroy trigger window
        try:
            self._trigger.destroy()
        except Exception:
            pass
        # Close hardware monitor to release sensor handles
        with self.lock:
            if self.computer is not None:
                try:
                    self.computer.Close()
                except Exception:
                    pass
                self.computer = None
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def kill_previous_instances():
    """Kill any other overlay.py instances matching our script path."""
    my_pid = os.getpid()
    script_lower = SCRIPT_PATH.lower()
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if proc.info['pid'] == my_pid:
                continue
            cmdline = proc.info.get('cmdline') or []
            if any(script_lower == arg.strip('"').lower() for arg in cmdline):
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except psutil.TimeoutExpired:
                    proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass


def main():
    kill_previous_instances()

    dll_path = os.path.join(LIB_DIR, "LibreHardwareMonitorLib.dll")
    if not os.path.exists(dll_path):
        ctypes.windll.user32.MessageBoxW(
            0, "LibreHardwareMonitorLib.dll not found!\nRun: python setup.py",
            "HW Monitor", 0x10
        )
        sys.exit(1)

    app = OverlayApp()
    try:
        app.run()
    except KeyboardInterrupt:
        app.quit()


if __name__ == "__main__":
    main()
