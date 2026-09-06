"""Opt-in B550 AORUS PRO AC case fan worker. CPU/GPU remain firmware/driver-owned."""
import argparse
import ctypes
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid

import psutil

from thermal_policy import CaseAirflowPolicy, FanRamp, case_fan_demand, finite
from hardware_access_guard import require_hardware_access
from shared_fans import SHARED_NAMES, open_shared_session, make_shared_computer, select_shared_sensors
from startup_readiness import StartupNotReady, StartupCancelled, wait_for_readiness
from thermal_policy import CASE_FAN_LABELS

PROFILE = "b550-aorus-pro-ac-case-124"
TARGETS = ("System Fan #1", "System Fan #2", "System Fan #4")
INDEPENDENT_TARGETS = TARGETS[:2]
ALL_TARGETS = INDEPENDENT_TARGETS + SHARED_NAMES
CHANNELS = {
    TARGETS[0]: ("/lpc/it8688e/0", 1),
    TARGETS[1]: ("/lpc/it8688e/0", 2),
    TARGETS[2]: ("/lpc/it8792e/0", 2),
}
MOTHERBOARD_REDISCOVERY_INTERVAL = 10.0
MOTHERBOARD_REDISCOVERY_LIMIT = 5


def open_status_file(path):
    """Read a snapshot without denying Windows rename/delete access."""
    if os.name != "nt":
        return open(path, encoding="utf-8")
    import msvcrt
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                  ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    # FILE_SHARE_DELETE is required even for rename; ordinary Python open()
    # omits it and can make our own reader intermittently stop the controller.
    delays = (0.005, 0.01, 0.02)
    for attempt in range(len(delays) + 1):
        handle = kernel.CreateFileW(os.fspath(path), 0x80000000, 0x1 | 0x2 | 0x4, None, 3, 0x80, None)
        if handle != ctypes.c_void_p(-1).value:
            break
        error = ctypes.get_last_error()
        # ReplaceFileW can briefly remove the old name before publishing the new
        # one. Retry opening only; never parse or expose a partially written file.
        if error not in (2, 5, 32, 33) or attempt == len(delays):
            raise ctypes.WinError(error)
        time.sleep(delays[attempt])
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except Exception:
        kernel.CloseHandle(handle)
        raise
    try:
        return os.fdopen(fd, "r", encoding="utf-8")
    except Exception:
        os.close(fd)
        raise


def replace_status_file(temporary, path):
    """Replace a Windows snapshot while shared readers retain the old contents."""
    if os.name != "nt":
        os.replace(temporary, path)
        return
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.ReplaceFileW.argtypes = (wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                   wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p)
    kernel.ReplaceFileW.restype = wintypes.BOOL
    # MoveFileExW (os.replace) can deny replacement even with shared-delete
    # readers. ReplaceFileW preserves their snapshot and the destination ACL.
    if kernel.ReplaceFileW(os.fspath(path), os.fspath(temporary), None, 0, None, None):
        return
    error = ctypes.get_last_error()
    if error == 2:  # Initial publication has no existing destination to replace.
        os.replace(temporary, path)
        return
    raise ctypes.WinError(error)


class FanWorkerClient:
    """UI-side heartbeat and status; all hardware ownership stays in the child."""
    def __init__(self, app_dir, full_rpm=None, shared=False, commission=False):
        self.app_dir = app_dir
        self.process = None
        self.status_path = None
        self.error = None
        self.full_rpm = full_rpm_reference(full_rpm)
        self.shared = shared is True
        self.commission = commission is True
        self.worker_pid = None
        self.last_status = None

    def start(self):
        if self.process is not None and self.process.poll() is None:
            return
        directory = os.path.join(os.environ.get("LOCALAPPDATA", self.app_dir), "HeatMap", "fan-status")
        self.error = None
        self.worker_pid = None
        self.last_status = None
        self.started = time.time()
        try:
            os.makedirs(directory, exist_ok=True)
            self.status_path = os.path.join(directory, uuid.uuid4().hex + ".json")
            self.process = subprocess.Popen(
                [sys.executable, os.path.join(self.app_dir, "case_fans.py"),
                 "--status", self.status_path, "--owner-pid", str(os.getpid()),
                 "--owner-created", str(psutil.Process().create_time()),
                 "--full-rpm", json.dumps(self.full_rpm)] + (["--shared"] if self.shared else [])
                + (["--commission"] if self.commission else []),
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                text=True, bufsize=1, creationflags=subprocess.CREATE_NO_WINDOW,
                cwd=self.app_dir,
            )
        except OSError as exc:
            self.error = str(exc)

    def poll(self):
        status = self._poll_status()
        never_acquired = (status.get('control_attempted') is False and status.get('baseline') == []
                          and status.get('controlled_channels') == [] and not status.get('restore_errors'))
        if self.shared and status.get('state') == 'error' and not status.get('controlled_channels') and not never_acquired:
            # A missing/corrupt worker report cannot certify that the secondary
            # controller is firmware-owned after this client requested takeover.
            status = dict(status, controlled_channels=list(ALL_TARGETS), firmware_channels=[])
        return status

    def _poll_status(self):
        if self.error:
            return {"state": "error", "reason": self.error}
        if self.process is None:
            return {"state": "off"}
        if self.process.poll() is None:
            try:
                self.process.stdin.write("alive\n")
                self.process.stdin.flush()
            except (OSError, ValueError):
                pass
        try:
            try:
                with open_status_file(self.status_path) as stream:
                    snapshot = stream.read(65536)
                status = json.loads(snapshot)
            except (OSError, ValueError):
                if self.last_status is None:
                    raise
                # A busy file must not manufacture an error while the last
                # verified report is still fresh. All PID/expiry checks still run.
                status = self.last_status
            stamp = finite(status.get("time"), 0, 1e12)
            if status.get("state") not in ("checking", "active", "error", "stopped"):
                return {"state": "error", "reason": "Invalid case fan controller status"}
            if status.get("state") == "active" and finite(status.get("command_pct"), 60, 100) is None:
                return {"state": "error", "reason": "Invalid case fan controller command report"}
            discovering = (status.get("state") == "checking" and status.get("phase") in ("discovering", "waiting")
                           and status.get("control_attempted") is False
                           and status.get("baseline") == [])
            if "controlled_channels" in status or "firmware_channels" in status:
                controlled, firmware = status.get("controlled_channels"), status.get("firmware_channels")
                if (not isinstance(controlled, list) or not isinstance(firmware, list)
                        or any(not isinstance(name, str) for name in controlled + firmware)
                        or controlled not in (list(INDEPENDENT_TARGETS), list(TARGETS), list(ALL_TARGETS), [])
                        or firmware != ([] if controlled == list(ALL_TARGETS) else
                                        [name for name in TARGETS if name not in controlled] if controlled else [])):
                    return {"state": "error", "reason": "Invalid case fan controller channel report"}
                if status.get("state") in ("active", "checking") and not controlled and not discovering:
                    return {"state": "error", "reason": "Missing case fan controller channels"}
            exited = self.process.poll() is not None
            terminal = status.get("state") in ("error", "stopped")
            pid = status.get("pid")
            valid_pid = (isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 and
                         (pid == self.process.pid or pid == self.worker_pid))
            if not valid_pid and isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
                try:
                    # Windows venv python[w].exe is a redirector whose child writes
                    # the report. The root process remains alive until that child exits.
                    child = psutil.Process(pid)
                    valid_pid = any(parent.pid == self.process.pid for parent in child.parents())
                    if valid_pid:
                        self.worker_pid = pid
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    # A fast terminal result can precede the first poll. This path
                    # is unique to this launch and must have been written after it.
                    valid_pid = exited and terminal and stamp is not None and stamp >= self.started - 2
            if not valid_pid or stamp is None or time.time() - stamp < -2:
                return {"state": "error", "reason": "Case fan controller status is stale"}
            if not (exited and terminal) and time.time() - stamp > 10:
                return {"state": "error", "reason": "Case fan controller status is stale"}
            if exited and not terminal:
                return {"state": "error", "reason": "Case fan controller exited unexpectedly; restart Windows if RPM stay abnormal"}
            self.last_status = status
            return status
        except (OSError, ValueError, TypeError, AttributeError):
            if self.process.poll() is not None or time.time() - self.started > 30:
                return {"state": "error", "reason": "Case fan controller did not report status"}
            return {"state": "checking", "reason": "Opening case fan controller"}

    def stop(self):
        if self.process is not None and self.process.stdin is not None:
            try:
                self.process.stdin.close()
            except OSError:
                pass
        # Do not kill the child: it must complete its native restore in finally.


class WorkerMutex:
    def __init__(self, name="Global\\HeatMapCaseFanControlV1"):
        self.name = name

    def __enter__(self):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
        self.kernel.CreateMutexW.restype = ctypes.c_void_p
        self.kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
        self.handle = self.kernel.CreateMutexW(None, False, self.name)
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "Cannot acquire case fan ownership")
        if ctypes.get_last_error() == 183:
            self.kernel.CloseHandle(self.handle)
            raise RuntimeError("Another HeatMap case fan controller already owns the hardware")
        return self

    def __exit__(self, *_args):
        self.kernel.CloseHandle(self.handle)


class ChannelNotReady(StartupNotReady):
    """A missing startup reading can recover before any control command is sent."""


def select_controls(computer):
    boards = [hw for hw in computer.Hardware if str(hw.HardwareType) == "Motherboard"]
    if not boards:
        raise ChannelNotReady("Waiting for motherboard sensors")
    # pythonnet wraps Computer.Hardware as IHardware, which does not expose Model.
    board = getattr(boards[0], "__implementation__", boards[0]) if len(boards) == 1 else None
    if board is None or str(getattr(board, "Model", "")) != "B550_AORUS_PRO_AC":
        raise RuntimeError("Case fan profile supports only Gigabyte B550 AORUS PRO AC")
    sources = [(sub, sensor) for sub in boards[0].SubHardware for sensor in sub.Sensors]
    sensors = [sensor for _sub, sensor in sources]
    # LHM disables the whole secondary Gigabyte EC when overriding SYS4.
    # Even a current 100% pump reading may be a firmware-curve peak, not a
    # fixed setting. This legacy path only selects the independent controller;
    # the commissioned shared profile uses its own verified adapter.
    selected = []
    for name in INDEPENDENT_TARGETS:
        chip, index = CHANNELS[name]
        control = [s for s in sensors if str(s.SensorType) == "Control"
                   and (str(s.Name) == name or str(getattr(s, "Identifier", "")) == f"{chip}/control/{index}")]
        tach = [s for s in sensors if str(s.SensorType) == "Fan"
                and (str(s.Name) == name or str(getattr(s, "Identifier", "")) == f"{chip}/fan/{index}")]
        counts = f"{name}: controls={len(control)}, tachometers={len(tach)}"
        if len(control) > 1 or len(tach) > 1:
            raise RuntimeError("Ambiguous case fan channel: " + counts)
        if not control or not tach:
            raise ChannelNotReady("Case fan channel not ready: " + counts)
        if control[0].Control is None:
            raise RuntimeError(f"Case fan control object unavailable: {name}")
        for sensor, kind in ((control[0], "control"), (tach[0], "fan")):
            owners = [sub for sub, candidate in sources if candidate is sensor]
            if (str(sensor.Name) != name or str(getattr(sensor, "Identifier", "")) != f"{chip}/{kind}/{index}"
                    or len(owners) != 1 or str(getattr(owners[0], "Identifier", "")) != chip):
                raise RuntimeError(f"Unexpected controller identity: {name}")
        if tach[0].Value is None:
            raise ChannelNotReady(f"Case fan tachometer not ready: {name}")
        if isinstance(tach[0].Value, (bool, str)) or finite(float(tach[0].Value), 1, 10000) is None:
            raise RuntimeError(f"Cannot take control without a running tachometer: {name}")
        c = control[0].Control
        if (finite(float(c.MinSoftwareValue), 0, 60) is None
                or finite(float(c.MaxSoftwareValue), 100, 100) is None):
            raise RuntimeError(f"Unsupported control range: {name}")
        selected.append((name, c, control[0], tach[0]))
    return selected


def wait_for_controls(computer, read_sensors, stop, owner, heartbeat, status_path,
                      shared=False, timeout=60.0, discovery=None):
    """Wait before takeover; rebuild only a positively empty motherboard topology."""
    discovery = {} if discovery is None else discovery
    discovery.update(motherboard_reopens=0, controller_ids=[])
    last_reopen_elapsed = 0.0

    def check():
        if stop.is_set() or not owner.is_running() or time.monotonic() - heartbeat[0] > 15:
            raise StartupCancelled("Overlay owner stopped during case fan readiness")
        require_hardware_access()

    def probe():
        boards = [hw for hw in computer.Hardware if str(hw.HardwareType) == "Motherboard"]
        discovery['controller_ids'] = [str(getattr(sub, 'Identifier', ''))
                                       for board in boards for sub in board.SubHardware]
        data = read_sensors(computer)
        selected = select_controls(computer)
        if shared:
            select_shared_sensors(computer)
        if not isinstance(data, dict):
            raise RuntimeError("Invalid case fan temperature snapshot")
        for key, label in CASE_FAN_LABELS.items():
            value = data.get(key)
            if value is None:
                raise StartupNotReady(f"Waiting for {label} temperature")
            if finite(value, 1, 150) is None:
                raise RuntimeError(f"Invalid {label} temperature before fan activation")
        return selected

    def waiting(reason, elapsed, remaining, attempt):
        nonlocal last_reopen_elapsed
        deadline = time.monotonic() + remaining
        write_status(status_path, "checking", phase="waiting", control_attempted=False,
                     baseline=[], controlled_channels=[], firmware_channels=[],
                     discovery_attempt=attempt, reason=str(reason), remaining_seconds=remaining,
                     discovery=dict(discovery))
        if (elapsed - last_reopen_elapsed < MOTHERBOARD_REDISCOVERY_INTERVAL
                or discovery['motherboard_reopens'] >= MOTHERBOARD_REDISCOVERY_LIMIT):
            return
        boards = [hw for hw in computer.Hardware if str(hw.HardwareType) == "Motherboard"]
        board = getattr(boards[0], '__implementation__', boards[0]) if len(boards) == 1 else None
        if (board is None or str(getattr(board, 'Model', '')) != 'B550_AORUS_PRO_AC'
                or list(boards[0].SubHardware)):
            return
        # LHM 0.9.5 LpcIO gives up after a 100 ms ISA-mutex wait; Update never
        # rebuilds that empty list. Recreate only this empty group, retaining CPU,
        # GPU, the Computer and its preloaded (not software-mode) fan settings.
        # No existing controller is closed and no fan session exists at this point.
        def guard_reopen():
            check()
            if time.monotonic() >= deadline:
                raise StartupNotReady('Startup readiness timed out during motherboard rediscovery')

        guard_reopen()
        discovery['motherboard_reopens'] += 1
        last_reopen_elapsed = elapsed
        computer.IsMotherboardEnabled = False
        guard_reopen()
        computer.IsMotherboardEnabled = True
        guard_reopen()

    return wait_for_readiness(probe, stop, check, waiting, timeout=timeout)


def full_rpm_reference(value):
    if not isinstance(value, dict) or set(value) not in (set(INDEPENDENT_TARGETS), set(TARGETS), set(ALL_TARGETS[:-1])):
        return None
    if any(finite(rpm, 200, 10000) is None for rpm in value.values()):
        return None
    return dict(value)


def commissioned_rpm_reference(value, shared=False):
    """Reuse calibration only when it covers this exact connected-fan profile."""
    reference = full_rpm_reference(value)
    expected = set(ALL_TARGETS[:-1] if shared else INDEPENDENT_TARGETS)
    if reference is None or set(reference) != expected:
        return None
    return reference


def parse_primary_fan_mode(report):
    """Read register 0x13 from the pinned IT8688E public diagnostic format."""
    if not isinstance(report, str):
        raise RuntimeError("Primary fan mode report is unavailable")
    lines = [line.strip() for line in report.splitlines()]
    if (lines.count("LPC IT87XX") != 1
            or [line for line in lines if line.startswith("Chip ID:")] != ["Chip ID: 0x8688"]
            or lines.count("Environment Controller Registers Bank 0") != 1):
        raise RuntimeError("Primary fan mode report has missing or ambiguous identity/bank")
    start = lines.index("Environment Controller Registers Bank 0") + 1
    bank = []
    for line in lines[start:]:
        if line.startswith("Environment Controller Registers Bank ") or line == "GPIO Registers":
            break
        if line:
            bank.append(line.split())
    header = [f"{index:02X}" for index in range(16)]
    if len(bank) != 12 or bank[0] != header:
        raise RuntimeError("Primary fan mode report has incomplete register rows")
    for index, row in enumerate(bank[1:]):
        if (len(row) != 17 or row[0] != f"{index * 16:02X}"
                or any(re.fullmatch(r"[0-9A-Fa-f]{2}", value) is None for value in row[1:])):
            raise RuntimeError("Primary fan mode report has unreadable or ambiguous registers")
    return int(bank[2][4], 16)


def read_primary_fan_mode(computer):
    """Use only the primary controller belonging to this opened Computer."""
    boards = [hw for hw in computer.Hardware if str(hw.HardwareType) == "Motherboard"]
    primary = [sub for hw in boards for sub in hw.SubHardware
               if str(getattr(sub, "Identifier", "")) == "/lpc/it8688e/0"]
    if len(boards) != 1 or len(primary) != 1:
        raise RuntimeError("Primary fan mode report controller is missing or ambiguous")
    try:
        return parse_primary_fan_mode(primary[0].GetReport())
    except Exception as exc:
        raise RuntimeError(f"Cannot verify primary fan output mode: {exc}") from exc


def verify_full_airflow(baseline, readings, full_rpm=None):
    reference = full_rpm_reference(full_rpm)
    if len(baseline) != len(readings) or not readings:
        raise RuntimeError("Incomplete fan response readings")
    for before, after in zip(baseline, readings):
        if before["name"] != after["name"]:
            raise RuntimeError("Fan response channel changed")
        if finite(after["control_pct"], 97, 100) is None:
            raise RuntimeError(f"{after['name']}: full-speed command was not confirmed")
        if before['name'] == SHARED_NAMES[2] and before['rpm'] == 0 and after['rpm'] == 0:
            continue  # Explicitly verified unused SYS6 is held at 100%, not calibrated.
        if finite(after["rpm"], 200, 10000) is None or finite(before["rpm"], 1, 10000) is None:
            raise RuntimeError(f"{after['name']}: no reliable running tachometer")
        reference_rpm = reference.get(after["name"]) if reference else None
        previously_verified_full = reference_rpm is not None and after["rpm"] >= 0.9 * reference_rpm
        if (before["control_pct"] or 0) < 95 and after["rpm"] < before["rpm"] * 1.08 and not previously_verified_full:
            raise RuntimeError(f"{after['name']}: RPM did not confirm a speed increase; wiring/mode needs checking")


def verify_restore(baseline, readings):
    if len(baseline) != len(readings) or not readings:
        return ["Incomplete fan restore readings"]
    errors = []
    for before, after in zip(baseline, readings):
        if before["name"] != after["name"]:
            errors.append("Fan restore channel changed")
            continue
        old, new = before["control_pct"], after["control_pct"]
        if (old is None) != (new is None) or (old is not None and abs(old - new) > 3):
            errors.append(f"{before['name']}: original control readback not restored")
    return errors


class CaseFanSession:
    def __init__(self, controls, shared=None):
        self.shared = shared
        self.controls = tuple(controls)
        if tuple(item[0] for item in self.controls) != INDEPENDENT_TARGETS:
            raise RuntimeError("Only independent SYS1/SYS2 control is supported; SYS4 and pump headers remain with firmware")
        for name, _control, sensor, tach in self.controls:
            chip, index = CHANNELS[name]
            if (str(getattr(sensor, "Identifier", "")) != f"{chip}/control/{index}"
                    or str(getattr(tach, "Identifier", "")) != f"{chip}/fan/{index}"):
                raise RuntimeError(f"Unexpected controller identity: {name}")
        self.touched = []
        self.last_command = None

    def apply(self, percent):
        if finite(percent, 60, 100) is None:
            raise ValueError("Case fan command must be finite and within 60..100")
        for item in self.controls:
            # Include the failing channel: native writes can partly succeed before raising.
            if item not in self.touched:
                self.touched.append(item)
            item[1].SetSoftware(float(percent))
        if self.shared is not None:
            self.shared.apply(percent)
        self.last_command = percent

    def restore(self):
        errors = []
        if self.shared is not None:
            try:
                errors.extend(self.shared.restore())
            except Exception as exc:
                # Cleanup of the independent outputs must survive a bridge fault.
                errors.append(f"Shared controller restore: {exc}")
        remaining = []
        for item in reversed(self.touched):
            try:
                item[1].SetDefault()
            except Exception as exc:
                remaining.append(item)
                errors.append(f"{item[0]}: {exc}")
        self.touched = remaining
        return errors

    def readings(self):
        return [{"name": name, "rpm": finite(float(tach.Value), 0, 10000) if tach.Value is not None else None,
                 "control_pct": finite(float(sensor.Value), 0, 100) if sensor.Value is not None else None}
                for name, _control, sensor, tach in self.controls] + (
                    self.shared.backend.readings() if self.shared is not None else [])


def write_status(path, state, **details):
    payload = {"state": state, "time": time.time(), "pid": os.getpid(), "profile": PROFILE, **details}
    temporary = path + ".tmp"
    # External readers/scanners may still omit FILE_SHARE_DELETE. Retry briefly
    # without truncating the published snapshot or delaying thermal control for
    # an unbounded time. Persistent I/O failures still trigger normal restoration.
    delays = (0.01, 0.02, 0.04, 0.08, 0.16, 0.32)
    for attempt in range(len(delays) + 1):
        try:
            with open(temporary, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False)
            replace_status_file(temporary, path)
            return
        except OSError as exc:
            # ReplaceFileW's ERROR_UNABLE_TO_REMOVE_REPLACED (1175) also
            # preserves both original names, so retrying it is safe.
            if getattr(exc, "winerror", None) not in (5, 32, 33, 1175) or attempt == len(delays):
                raise
            time.sleep(delays[attempt])


def worker(status_path, owner_pid, owner_created, full_rpm=None, shared=False, commission=False):
    import overlay
    if not overlay._is_admin():
        raise RuntimeError("Administrator sensor access is required; no elevation is launched automatically")
    errors = overlay._runtime_dll_errors()
    if errors:
        raise RuntimeError("Hardware runtime verification failed: " + "; ".join(errors))

    import clr
    clr.AddReference(os.path.join(overlay.LIB_DIR, "LibreHardwareMonitorLib.dll"))
    # Both profiles need a preloaded SoftwareValue: LHM's first mode transition
    # otherwise briefly writes its default zero before the requested full duty.
    computer = make_shared_computer()
    computer.IsCpuEnabled = True
    computer.IsGpuEnabled = True
    computer.IsMotherboardEnabled = True
    session = None
    stop = threading.Event()
    heartbeat = [time.monotonic()]

    def listen():
        try:
            for line in sys.stdin:
                if line.strip() == "stop":
                    break
                if line.strip() == "alive":
                    heartbeat[0] = time.monotonic()
        finally:
            stop.set()

    threading.Thread(target=listen, daemon=True).start()
    owner = psutil.Process(owner_pid)
    if abs(owner.create_time() - owner_created) > 0.01:
        raise RuntimeError("Overlay owner process changed")
    error = None
    baseline = []
    controlled_channels = []
    firmware_channels = []
    initial_fan_mode = None
    control_attempted = False
    shared_session = None
    discovery = {}

    def check_before_command():
        if stop.is_set() or not owner.is_running() or time.monotonic() - heartbeat[0] > 15:
            raise StartupCancelled("Overlay owner stopped before case fan command")
        require_hardware_access()
        # Process inspection may block; recheck the owner after the guard too.
        if stop.is_set() or not owner.is_running() or time.monotonic() - heartbeat[0] > 15:
            raise StartupCancelled("Overlay owner stopped before case fan command")

    try:
        require_hardware_access()
        computer.Open()
        selected = wait_for_controls(computer, overlay.read_sensors, stop, owner, heartbeat, status_path,
                                     shared=shared, discovery=discovery)
        mode = read_primary_fan_mode(computer)
        # LHM saves this whole register as a bool but restores individual bits.
        # Require both selected outputs already enabled to preserve their modes.
        if mode & 0x06 != 0x06:
            raise RuntimeError("Primary SYS1/SYS2 output modes are disabled; firmware configuration needs checking")
        initial_fan_mode = mode & 0x06
        if shared:
            shared_session = open_shared_session(computer)
        session = CaseFanSession(selected, shared_session)
        controlled_channels = list(ALL_TARGETS) if shared else [item[0] for item in session.controls]
        firmware_channels = [] if shared else [name for name in TARGETS if name not in controlled_channels]
        baseline = session.readings()
        if stop.is_set() or not owner.is_running() or time.monotonic() - heartbeat[0] > 15:
            raise StartupCancelled("Overlay owner stopped before case fan activation")
        commissioned = not commission and commissioned_rpm_reference(full_rpm, shared) is not None
        ramp = None if commissioned else FanRamp()
        airflow = CaseAirflowPolicy()
        if not commissioned:
            # A new/changed profile still needs the full-speed response test.
            # Repeated launches of a commissioned profile verify normal command
            # feedback below instead of repeating a noisy calibration sweep.
            check_before_command()
            control_attempted = True
            session.apply(100)
        started = time.monotonic()
        stall_since = {}
        verified_full_rpm = None
        while not stop.is_set() and owner.is_running():
            now = time.monotonic()
            if now - heartbeat[0] > 15:
                # A crashed/frozen UI cannot silently retain ownership indefinitely.
                break
            require_hardware_access()
            data = overlay.read_sensors(computer)
            now = time.monotonic()
            if stop.is_set() or not owner.is_running() or now - heartbeat[0] > 15:
                break
            thermal_demand, _ = case_fan_demand(data)
            demand, reason = airflow.update(data, now)
            policy_demand = demand
            readings = session.readings()
            if shared_session is not None:
                shared_session.check()
            for fan in readings:
                if shared and fan['name'] == SHARED_NAMES[2]:
                    if fan['rpm'] != 0:
                        raise RuntimeError('Previously unused SYS6 changed; restoring motherboard control')
                    continue
                if fan["rpm"] is None or fan["rpm"] < 200:
                    demand, reason = 100, f"{fan['name']}: tachometer unavailable/stopped"
                    start = stall_since.setdefault(fan["name"], now)
                    if now - start >= 10:
                        raise RuntimeError(reason)
                else:
                    stall_since.pop(fan["name"], None)
            # Full-airflow startup verifies readable command feedback before ramping down.
            if not commissioned and now - started < 15:
                demand, reason = 100, "Checking full airflow"
            elif not commissioned:
                verify_full_airflow(baseline, readings, full_rpm)
                verified_full_rpm = {fan["name"]: fan["rpm"] for fan in readings if fan['name'] != SHARED_NAMES[2]}
                commissioned = True
            command_feedback_verified = commissioned and session.last_command is not None
            if command_feedback_verified and any(fan["control_pct"] is None or abs(fan["control_pct"] -
                                     (100 if shared and fan['name'] == SHARED_NAMES[2] else session.last_command)) > 3
                     for fan in readings):
                raise RuntimeError("Fan command readback differs; possible firmware/controller conflict")
            if ramp is None:
                # Initialize from the first current sample, without inheriting
                # FanRamp's fail-safe 100% startup hold on a calibrated profile.
                ramp = FanRamp(value=demand)
            command = ramp.update(demand, now)
            if command != session.last_command:
                check_before_command()
                control_attempted = True
                session.apply(command)
            write_status(status_path, "active" if command_feedback_verified else "checking",
                         discovery=dict(discovery),
                         command_pct=command, demand_pct=demand, reason=reason, fans=readings, baseline=baseline,
                         controlled_channels=controlled_channels, firmware_channels=firmware_channels,
                         unused_channels=[SHARED_NAMES[2]] if shared else [],
                         verified_full_rpm=verified_full_rpm,
                         thermal_demand_pct=thermal_demand, policy_demand_pct=policy_demand,
                         observed_case_fans=[dict(name=fan.get("name"), id=fan.get("id"),
                                                  rpm=fan.get("rpm"), control_pct=fan.get("control_pct"))
                                             for fan in data.get("fans", [])
                                             if str(fan.get("name", "")).startswith("System Fan #")],
                         temperatures={key: data.get(key) for key in (
                             "cpu_temp", "gpu_core_temp", "gpu_hotspot_temp", "gpu_memory_temp")})
            stop.wait(2)
    except StartupCancelled:
        pass  # Ordinary owner shutdown is not a controller failure.
    except Exception as exc:
        error = str(exc)
    finally:
        restore_errors = session.restore() if session else []
        if restore_errors:
            time.sleep(0.2)
            restore_errors = session.restore()
        if session and baseline:
            try:
                # Close is also LHM's second native restore attempt (SetDefault
                # alone can silently lose the ISA mutex race). Read before closing
                # the Computer so failed restoration cannot be reported as success.
                for hw in computer.Hardware:
                    if str(hw.HardwareType) == "Motherboard":
                        for sub in hw.SubHardware:
                            getattr(sub, "__implementation__", sub).Close()
                            sub.Update()
                # Firmware curves may legitimately change secondary duty after EC
                # restoration. The shared session verifies actual modes/registers.
                restored_readings = session.readings()
                restore_errors.extend(verify_restore(baseline[:2], restored_readings[:2]))
                if read_primary_fan_mode(computer) & 0x06 != initial_fan_mode:
                    restore_errors.append("Primary SYS1/SYS2 output modes were not restored")
            except Exception as exc:
                restore_errors.append(f"Restore verification: {exc}")
        try:
            computer.Close()
        except Exception as exc:
            restore_errors.append(f"Close: {exc}")
        if shared_session is not None:
            try:
                shared_session.close()
            except Exception as exc:
                restore_errors.append(f'Shared bridge close: {exc}')
        write_status(status_path, "error" if error or restore_errors else "stopped",
                     discovery=dict(discovery),
                     reason=error or ("Restore unconfirmed: restart Windows" if restore_errors else
                                      "Returned to firmware control" if control_attempted else "Stopped before case fan takeover"),
                     restore_errors=restore_errors, baseline=baseline,
                     controlled_channels=controlled_channels, firmware_channels=firmware_channels,
                     control_attempted=control_attempted,
                     restore_confirmed=bool(session and baseline and not restore_errors))
    return 1 if error or restore_errors else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", required=True)
    parser.add_argument("--owner-pid", required=True, type=int)
    parser.add_argument("--owner-created", required=True, type=float)
    parser.add_argument("--full-rpm", type=json.loads, default=None)
    parser.add_argument("--shared", action="store_true", help="Use the commissioned four-fan shared-controller profile")
    parser.add_argument("--commission", action="store_true", help="Explicitly repeat full-airflow response calibration")
    args = parser.parse_args()
    try:
        with WorkerMutex():
            return worker(args.status, args.owner_pid, args.owner_created, args.full_rpm, args.shared, args.commission)
    except Exception as exc:
        write_status(args.status, "error", reason=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
