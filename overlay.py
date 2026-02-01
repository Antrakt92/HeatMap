"""
Desktop Temperature Overlay
Desktop widget showing hardware temperatures and usage.
Sits on the desktop layer — above wallpaper, below all app windows.
Requires admin privileges to read hardware sensors.
"""
import copy
import ctypes
import ctypes.wintypes
import json
import os
import sys
import threading
import time
import tkinter as tk
import winreg
import winsound

import psutil

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
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_READ)
        winreg.QueryValueEx(key, AUTOSTART_NAME)
        winreg.CloseKey(key)
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


def enable_autostart():
    """Add to Windows startup registry with admin elevation."""
    try:
        pythonw = get_pythonw_path()
        # Must self-elevate because LibreHardwareMonitor needs admin
        cmd = (
            f'powershell -WindowStyle Hidden -Command '
            f'"Start-Process \'{pythonw}\' -ArgumentList \'\\"{SCRIPT_PATH}\\"\' -Verb RunAs"'
        )
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, AUTOSTART_NAME, 0, winreg.REG_SZ, cmd)
        winreg.CloseKey(key)
        return True
    except Exception:
        return False


def disable_autostart():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, AUTOSTART_NAME)
        winreg.CloseKey(key)
        return True
    except Exception:
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
            if disk_temp is not None:
                name = str(hw.Name).replace("Samsung SSD ", "").replace("Samsung ", "")
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
    try:
        with open(CONFIG_PATH, "r") as f:
            return json.load(f)
    except Exception:
        return {"x": 50, "y": 50}


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f)
    except Exception:
        pass


# --- Main overlay class ---
class OverlayApp:
    def __init__(self):
        self.computer = init_hardware_monitor()
        self.config = load_config()
        self.running = True
        self.sensor_data = {}
        self.lock = threading.Lock()
        self.embedded = False

        # --- Alert system ---
        self.alerts_enabled = True
        self._last_alert_time = 0
        self._ALERT_COOLDOWN = 60  # seconds between repeated alerts
        self._CRITICAL = {
            "cpu_temp": 85,
            "gpu_temp": 90,
            "disk_temp": 55,
            "disk_used": 90,
            "ram_pct": 95,
        }

        # --- tkinter setup ---
        self.root = tk.Tk()
        self.root.title("Temp Overlay")
        self.root.overrideredirect(True)
        self.root.wm_attributes("-alpha", 0.88)
        self.root.configure(bg="#1a1a2e")

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

        # Separator after disks
        tk.Frame(self.content, bg="#2a2a4e", height=1).pack(fill="x", pady=2)

        # Status label
        self.status_label = tk.Label(
            self.content, text="Starting...", font=("Segoe UI", 8),
            fg="#555577", bg="#1a1a2e"
        )
        self.status_label.pack()

        # --- Right-click menu ---
        self.topmost = False
        self.menu = tk.Menu(self.root, tearoff=0, bg="#1a1a2e", fg="#a0a0c0",
                           activebackground="#2a2a4e", activeforeground="white",
                           font=("Segoe UI", 9))
        self.menu.add_command(
            label="Always on top: OFF",
            command=self.toggle_topmost
        )
        self.menu.add_command(
            label="Autostart: ON" if is_autostart_enabled() else "Autostart: OFF",
            command=self.toggle_autostart
        )
        self.menu.add_command(
            label="Alerts: ON",
            command=self.toggle_alerts
        )
        self.menu.add_separator()
        self.menu.add_command(label="Close", command=self.quit)
        self.root.bind("<Button-3>", self.show_menu)

        # --- Embed into desktop after window is drawn ---
        self.root.after(100, self._embed_into_desktop)

        # --- Start sensor thread ---
        self.sensor_thread = threading.Thread(target=self.sensor_loop, daemon=True)
        self.sensor_thread.start()

        # --- Start UI update loop ---
        self.update_ui()

    def _get_hwnd(self):
        """Get the native Windows HWND for the tkinter root window."""
        self.root.update_idletasks()
        # wm_frame() returns the frame window id as hex string
        frame_id = self.root.wm_frame()
        if frame_id and frame_id != "0":
            hwnd = int(frame_id, 16)
            if hwnd:  # could be 0 if wm_frame() returned "0x0"
                return hwnd
        # Fallback: use winfo_id (the inner window)
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

    def toggle_topmost(self):
        self.topmost = not self.topmost
        if self.topmost:
            # Unembed from desktop if embedded, make topmost
            if self.embedded:
                hwnd = self._get_hwnd()
                user32.SetParent(hwnd, 0)
                self.embedded = False
            self.root.wm_attributes("-topmost", True)
        else:
            self.root.wm_attributes("-topmost", False)
            self.root.after(100, self._embed_into_desktop)
        self.menu.entryconfig(0,
            label="Always on top: ON" if self.topmost else "Always on top: OFF"
        )

    def toggle_autostart(self):
        if is_autostart_enabled():
            disable_autostart()
        else:
            enable_autostart()
        self.menu.entryconfig(1,
            label="Autostart: ON" if is_autostart_enabled() else "Autostart: OFF"
        )

    def toggle_alerts(self):
        self.alerts_enabled = not self.alerts_enabled
        self.menu.entryconfig(2,
            label="Alerts: ON" if self.alerts_enabled else "Alerts: OFF"
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
            if disk["temp"] >= self._CRITICAL["disk_temp"]:
                alerts.append(f"{disk['name']} {disk['temp']}°C")
            used = disk.get("used_pct")
            if used is not None and used >= self._CRITICAL["disk_used"]:
                alerts.append(f"{disk['name']} {used}%")

        if alerts:
            self._last_alert_time = now
            # Beep in a thread to avoid blocking UI
            threading.Thread(
                target=lambda: winsound.Beep(1000, 300) or time.sleep(0.15) or winsound.Beep(1000, 300),
                daemon=True
            ).start()
            self.status_label.config(
                text=f"⚠ {', '.join(alerts)}",
                fg="#f87171"
            )

    def start_drag(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def on_drag(self, event):
        x = self.root.winfo_x() + event.x - self._drag_x
        y = self.root.winfo_y() + event.y - self._drag_y
        self.root.geometry(f"+{x}+{y}")
        self.config["x"] = x
        self.config["y"] = y

    def end_drag(self, _event):
        save_config(self.config)

    def show_menu(self, event):
        self.menu.post(event.x_root, event.y_root)

    def sensor_loop(self):
        while self.running:
            try:
                data = read_sensors(self.computer)
                with self.lock:
                    self.sensor_data = data
            except Exception as e:
                with self.lock:
                    self.sensor_data = {"error": str(e)}
            time.sleep(2)

    def update_ui(self):
        with self.lock:
            data = copy.deepcopy(self.sensor_data)

        if not data:
            self.root.after(500, self.update_ui)
            return

        if "error" in data:
            self.status_label.config(text=f"Error: {data['error']}", fg="#f87171")
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

        # GPU FAN: prefer %, fallback RPM
        gpu_fan_pct = data.get("gpu_fan_pct")
        gpu_fan = data.get("gpu_fan")
        if gpu_fan_pct is not None:
            if gpu_fan_pct == 0:
                self.rows["gpu_fan"].config(text="OFF", fg="#4ade80")
            else:
                self.rows["gpu_fan"].config(text=f"{gpu_fan_pct}%", fg=load_color(gpu_fan_pct))
        elif gpu_fan is not None:
            if gpu_fan == 0:
                self.rows["gpu_fan"].config(text="OFF", fg="#4ade80")
            else:
                est_pct = min(100, round(gpu_fan / 2200 * 100))
                self.rows["gpu_fan"].config(text=f"~{est_pct}%", fg=load_color(est_pct))
        else:
            self.rows["gpu_fan"].config(text="--", fg="#888888")

        # CPU FAN: show % (same style as GPU fan)
        cpu_fan_pct = data.get("cpu_fan_pct")
        cpu_fan = data.get("cpu_fan")
        if cpu_fan_pct is not None:
            if cpu_fan_pct == 0:
                self.rows["cpu_fan"].config(text="OFF", fg="#4ade80")
            else:
                self.rows["cpu_fan"].config(text=f"{cpu_fan_pct}%", fg=load_color(cpu_fan_pct))
        elif cpu_fan is not None:
            if cpu_fan == 0:
                self.rows["cpu_fan"].config(text="OFF", fg="#4ade80")
            else:
                # Estimate % from RPM (typical CPU fan max ~1500-2000 RPM)
                est_pct = min(100, round(cpu_fan / 1800 * 100))
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
        while len(self.disk_labels) < len(disks):
            idx = len(self.disk_labels)
            key = f"disk_{idx}"
            self._make_disk_row(key, disks[idx]["name"], parent=self.disk_frame)
            self.disk_labels.append(key)
        for i, disk in enumerate(disks):
            key = self.disk_labels[i]
            self.rows[key].config(text=f"{disk['temp']}°C", fg=temp_color(disk["temp"]))
            used = disk.get("used_pct")
            if used is not None:
                self.rows[key + "_usage"].config(text=f"{used}%", fg=disk_usage_color(used))
            else:
                self.rows[key + "_usage"].config(text="", fg="#888888")

        # Check critical thresholds and alert
        self._check_alerts(data)

        # Status (only update if no active alert showing)
        now = time.time()
        if now - self._last_alert_time >= 10:
            self.status_label.config(
                text="Desktop" if self.embedded else "Floating",
                fg="#4ade80" if self.embedded else "#facc15"
            )

        self.root.after(2000, self.update_ui)

    def quit(self):
        self.running = False
        # Save position
        self.config["x"] = self.root.winfo_rootx()
        self.config["y"] = self.root.winfo_rooty()
        save_config(self.config)
        # Close hardware monitor to release sensor handles
        if self.computer is not None:
            try:
                self.computer.Close()
            except Exception:
                pass
        self.root.destroy()

    def run(self):
        self.root.mainloop()


def kill_previous_instances():
    """Kill any other overlay.py / overlay instances."""
    my_pid = os.getpid()
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if proc.info['pid'] == my_pid:
                continue
            cmdline = proc.info.get('cmdline') or []
            cmdline_str = " ".join(cmdline).lower()
            if "overlay.py" in cmdline_str:
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
    app.run()


if __name__ == "__main__":
    main()
