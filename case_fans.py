"""Opt-in B550 AORUS PRO AC case fan worker. Never controls CPU/GPU/pump headers."""
import argparse
import ctypes
import json
import os
import subprocess
import sys
import threading
import time
import uuid

import psutil

from thermal_policy import FanRamp, case_fan_demand, finite

PROFILE = "b550-aorus-pro-ac-case-124"
TARGETS = ("System Fan #1", "System Fan #2", "System Fan #4")
INDEPENDENT_TARGETS = TARGETS[:2]
CHANNELS = {
    TARGETS[0]: ("/lpc/it8688e/0", 1),
    TARGETS[1]: ("/lpc/it8688e/0", 2),
    TARGETS[2]: ("/lpc/it8792e/0", 2),
}
CONFLICTS = {"fancontrol.exe", "siv.exe", "gcc.exe", "easytune.exe"}


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
    def __init__(self, app_dir, full_rpm=None):
        self.app_dir = app_dir
        self.process = None
        self.status_path = None
        self.error = None
        self.full_rpm = full_rpm_reference(full_rpm)
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
                 "--full-rpm", json.dumps(self.full_rpm)],
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                text=True, bufsize=1, creationflags=subprocess.CREATE_NO_WINDOW,
                cwd=self.app_dir,
            )
        except OSError as exc:
            self.error = str(exc)

    def poll(self):
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
            if "controlled_channels" in status or "firmware_channels" in status:
                controlled, firmware = status.get("controlled_channels"), status.get("firmware_channels")
                if (not isinstance(controlled, list) or not isinstance(firmware, list)
                        or any(not isinstance(name, str) for name in controlled + firmware)
                        or controlled not in (list(INDEPENDENT_TARGETS), list(TARGETS), [])
                        or firmware != ([name for name in TARGETS if name not in controlled] if controlled else [])):
                    return {"state": "error", "reason": "Invalid case fan controller channel report"}
                if status.get("state") in ("active", "checking") and not controlled:
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
    def __enter__(self):
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
        self.kernel.CreateMutexW.restype = ctypes.c_void_p
        self.kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
        self.handle = self.kernel.CreateMutexW(None, False, "Global\\HeatMapCaseFanControlV1")
        if not self.handle:
            raise OSError(ctypes.get_last_error(), "Cannot acquire case fan ownership")
        if ctypes.get_last_error() == 183:
            self.kernel.CloseHandle(self.handle)
            raise RuntimeError("Another HeatMap case fan controller already owns the hardware")
        return self

    def __exit__(self, *_args):
        self.kernel.CloseHandle(self.handle)


def select_controls(computer):
    boards = [hw for hw in computer.Hardware if str(hw.HardwareType) == "Motherboard"]
    # pythonnet wraps Computer.Hardware as IHardware, which does not expose Model.
    board = getattr(boards[0], "__implementation__", boards[0]) if len(boards) == 1 else None
    if board is None or str(getattr(board, "Model", "")) != "B550_AORUS_PRO_AC":
        raise RuntimeError("Case fan profile supports only Gigabyte B550 AORUS PRO AC")
    sources = [(sub, sensor) for sub in boards[0].SubHardware for sensor in sub.Sensors]
    sensors = [sensor for _sub, sensor in sources]
    try:
        verify_shared_controller(sensors)
        targets = TARGETS
    except RuntimeError:
        # SYS1/2 have a separate chip. Do not acquire SYS4's shared controller
        # when firmware is running a pump curve or its baseline is unavailable.
        targets = INDEPENDENT_TARGETS
    selected = []
    for name in targets:
        control = [s for s in sensors if str(s.SensorType) == "Control" and str(s.Name) == name]
        tach = [s for s in sensors if str(s.SensorType) == "Fan" and str(s.Name) == name]
        if len(control) != 1 or len(tach) != 1 or control[0].Control is None:
            raise RuntimeError(f"Missing or ambiguous case fan channel: {name}")
        chip, index = CHANNELS[name]
        for sensor, kind in ((control[0], "control"), (tach[0], "fan")):
            owners = [sub for sub, candidate in sources if candidate is sensor]
            if (str(getattr(sensor, "Identifier", "")) != f"{chip}/{kind}/{index}"
                    or len(owners) != 1 or str(getattr(owners[0], "Identifier", "")) != chip):
                raise RuntimeError(f"Unexpected controller identity: {name}")
        if finite(float(tach[0].Value) if tach[0].Value is not None else None, 1, 10000) is None:
            raise RuntimeError(f"Cannot take control without a running tachometer: {name}")
        c = control[0].Control
        if float(c.MinSoftwareValue) > 60 or float(c.MaxSoftwareValue) < 100:
            raise RuntimeError(f"Unsupported control range: {name}")
        selected.append((name, c, control[0], tach[0]))
    return selected


def verify_selected_shared_controller(computer, controls):
    if any(item[0] == TARGETS[2] for item in controls):
        verify_shared_controller([s for hw in computer.Hardware if str(hw.HardwareType) == "Motherboard"
                                  for sub in hw.SubHardware for s in sub.Sensors])


def verify_shared_controller(sensors):
    # SYS4 shares Gigabyte EC ownership with the pump-capable headers. Only
    # support the measured fixed-full-speed baseline; never freeze an unknown curve.
    for name in ("System Fan #5 / Pump", "System Fan #6 / Pump"):
        siblings = [s for s in sensors if str(s.SensorType) == "Control" and str(s.Name) == name]
        if len(siblings) != 1 or siblings[0].Value is None or finite(float(siblings[0].Value), 99, 100) is None:
            raise RuntimeError(f"Shared controller requires existing fixed 100% on {name}")


def full_rpm_reference(value):
    if not isinstance(value, dict) or set(value) not in (set(INDEPENDENT_TARGETS), set(TARGETS)):
        return None
    if any(finite(rpm, 200, 10000) is None for rpm in value.values()):
        return None
    return dict(value)


def verify_full_airflow(baseline, readings, full_rpm=None):
    reference = full_rpm_reference(full_rpm)
    if len(baseline) != len(readings) or not readings:
        raise RuntimeError("Incomplete fan response readings")
    for before, after in zip(baseline, readings):
        if before["name"] != after["name"]:
            raise RuntimeError("Fan response channel changed")
        if finite(after["control_pct"], 97, 100) is None:
            raise RuntimeError(f"{after['name']}: full-speed command was not confirmed")
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
    def __init__(self, controls):
        self.controls = controls
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
        self.last_command = percent

    def restore(self):
        errors = []
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
                for name, _control, sensor, tach in self.controls]


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


def worker(status_path, owner_pid, owner_created, full_rpm=None):
    import overlay
    if not overlay._is_admin():
        raise RuntimeError("Administrator sensor access is required; no elevation is launched automatically")
    errors = overlay._runtime_dll_errors()
    if errors:
        raise RuntimeError("Hardware runtime verification failed: " + "; ".join(errors))
    conflicts = [p.info["name"] for p in psutil.process_iter(["name"])
                 if (p.info["name"] or "").lower() in CONFLICTS]
    if conflicts:
        raise RuntimeError("Another fan control application is running: " + ", ".join(conflicts))

    import clr
    clr.AddReference(os.path.join(overlay.LIB_DIR, "LibreHardwareMonitorLib.dll"))
    from LibreHardwareMonitor.Hardware import Computer
    computer = Computer()
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
    try:
        computer.Open()
        data = overlay.read_sensors(computer)
        session = CaseFanSession(select_controls(computer))
        controlled_channels = [item[0] for item in session.controls]
        firmware_channels = [name for name in TARGETS if name not in controlled_channels]
        baseline = session.readings()
        if stop.is_set() or not owner.is_running() or time.monotonic() - heartbeat[0] > 15:
            raise RuntimeError("Overlay owner stopped before case fan activation")
        ramp = FanRamp()
        session.apply(100)
        started = time.monotonic()
        stall_since = {}
        commissioned = False
        verified_full_rpm = None
        while not stop.is_set() and owner.is_running():
            now = time.monotonic()
            if now - heartbeat[0] > 15:
                # A crashed/frozen UI cannot silently retain ownership indefinitely.
                break
            data = overlay.read_sensors(computer)
            now = time.monotonic()
            if stop.is_set() or not owner.is_running() or now - heartbeat[0] > 15:
                break
            verify_selected_shared_controller(computer, session.controls)
            demand, reason = case_fan_demand(data)
            readings = session.readings()
            for fan in readings:
                if fan["rpm"] is None or fan["rpm"] < 200:
                    demand, reason = 100, f"{fan['name']}: tachometer unavailable/stopped"
                    start = stall_since.setdefault(fan["name"], now)
                    if now - start >= 10:
                        raise RuntimeError(reason)
                else:
                    stall_since.pop(fan["name"], None)
            # Full-airflow startup verifies readable command feedback before ramping down.
            if now - started < 15:
                demand, reason = 100, "Checking full airflow"
            elif not commissioned:
                verify_full_airflow(baseline, readings, full_rpm)
                verified_full_rpm = {fan["name"]: fan["rpm"] for fan in readings}
                commissioned = True
            if commissioned and any(fan["control_pct"] is None or abs(fan["control_pct"] - session.last_command) > 3
                     for fan in readings):
                raise RuntimeError("Fan command readback differs; possible firmware/controller conflict")
            command = ramp.update(demand, now)
            if command != session.last_command:
                session.apply(command)
            write_status(status_path, "active" if commissioned else "checking",
                         command_pct=command, demand_pct=demand, reason=reason, fans=readings, baseline=baseline,
                         controlled_channels=controlled_channels, firmware_channels=firmware_channels,
                         verified_full_rpm=verified_full_rpm,
                         temperatures={key: data.get(key) for key in (
                             "cpu_temp", "gpu_core_temp", "gpu_hotspot_temp", "gpu_memory_temp")})
            stop.wait(2)
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
                restore_errors.extend(verify_restore(baseline, session.readings()))
            except Exception as exc:
                restore_errors.append(f"Restore verification: {exc}")
        try:
            computer.Close()
        except Exception as exc:
            restore_errors.append(f"Close: {exc}")
        write_status(status_path, "error" if error or restore_errors else "stopped",
                     reason=error or ("Restore unconfirmed: restart Windows" if restore_errors else "Returned to firmware control"),
                     restore_errors=restore_errors, baseline=baseline,
                     controlled_channels=controlled_channels, firmware_channels=firmware_channels,
                     restore_confirmed=bool(session and baseline and not restore_errors))
    return 1 if error or restore_errors else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", required=True)
    parser.add_argument("--owner-pid", required=True, type=int)
    parser.add_argument("--owner-created", required=True, type=float)
    parser.add_argument("--full-rpm", type=json.loads, default=None)
    args = parser.parse_args()
    try:
        with WorkerMutex():
            return worker(args.status, args.owner_pid, args.owner_created, args.full_rpm)
    except Exception as exc:
        write_status(args.status, "error", reason=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
