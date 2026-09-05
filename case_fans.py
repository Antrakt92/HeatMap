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
CONFLICTS = {"fancontrol.exe", "siv.exe", "gcc.exe", "easytune.exe"}


class FanWorkerClient:
    """UI-side heartbeat and status; all hardware ownership stays in the child."""
    def __init__(self, app_dir):
        self.app_dir = app_dir
        self.process = None
        self.status_path = None
        self.error = None

    def start(self):
        if self.process is not None and self.process.poll() is None:
            return
        directory = os.path.join(os.environ.get("LOCALAPPDATA", self.app_dir), "HeatMap", "fan-status")
        os.makedirs(directory, exist_ok=True)
        self.status_path = os.path.join(directory, uuid.uuid4().hex + ".json")
        self.error = None
        self.started = time.time()
        try:
            self.process = subprocess.Popen(
                [sys.executable, os.path.join(self.app_dir, "case_fans.py"),
                 "--status", self.status_path, "--owner-pid", str(os.getpid()),
                 "--owner-created", str(psutil.Process().create_time())],
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
            with open(self.status_path, encoding="utf-8") as stream:
                status = json.loads(stream.read(65536))
            stamp = finite(status.get("time"), 0, 1e12)
            if status.get("pid") != self.process.pid or stamp is None or not -2 <= time.time() - stamp <= 10:
                return {"state": "error", "reason": "Case fan controller status is stale"}
            if self.process.poll() is not None and status.get("state") not in ("error", "stopped"):
                return {"state": "error", "reason": "Case fan controller exited unexpectedly; restart Windows if RPM stay abnormal"}
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
    sensors = [sensor for sub in boards[0].SubHardware for sensor in sub.Sensors]
    verify_shared_controller(sensors)
    selected = []
    for name in TARGETS:
        control = [s for s in sensors if str(s.SensorType) == "Control" and str(s.Name) == name]
        tach = [s for s in sensors if str(s.SensorType) == "Fan" and str(s.Name) == name]
        if len(control) != 1 or len(tach) != 1 or control[0].Control is None:
            raise RuntimeError(f"Missing or ambiguous case fan channel: {name}")
        if finite(float(tach[0].Value) if tach[0].Value is not None else None, 1, 10000) is None:
            raise RuntimeError(f"Cannot take control without a running tachometer: {name}")
        c = control[0].Control
        if float(c.MinSoftwareValue) > 60 or float(c.MaxSoftwareValue) < 100:
            raise RuntimeError(f"Unsupported control range: {name}")
        selected.append((name, c, control[0], tach[0]))
    return selected


def verify_shared_controller(sensors):
    # SYS4 shares Gigabyte EC ownership with the pump-capable headers. Only
    # support the measured fixed-full-speed baseline; never freeze an unknown curve.
    for name in ("System Fan #5 / Pump", "System Fan #6 / Pump"):
        siblings = [s for s in sensors if str(s.SensorType) == "Control" and str(s.Name) == name]
        if len(siblings) != 1 or siblings[0].Value is None or finite(float(siblings[0].Value), 99, 100) is None:
            raise RuntimeError(f"Shared controller requires existing fixed 100% on {name}")


def verify_full_airflow(baseline, readings):
    for before, after in zip(baseline, readings):
        if after["control_pct"] is None or after["control_pct"] < 97:
            raise RuntimeError(f"{after['name']}: full-speed command was not confirmed")
        if after["rpm"] is None or after["rpm"] < 200:
            raise RuntimeError(f"{after['name']}: no reliable running tachometer")
        if (before["control_pct"] or 0) < 95 and after["rpm"] < before["rpm"] * 1.08:
            raise RuntimeError(f"{after['name']}: RPM did not confirm a speed increase; wiring/mode needs checking")


def verify_restore(baseline, readings):
    errors = []
    for before, after in zip(baseline, readings):
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
    with open(temporary, "w", encoding="utf-8") as stream:
        json.dump(payload, stream, ensure_ascii=False)
    os.replace(temporary, path)


def worker(status_path, owner_pid, owner_created):
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
    try:
        computer.Open()
        data = overlay.read_sensors(computer)
        session = CaseFanSession(select_controls(computer))
        baseline = session.readings()
        if stop.is_set() or not owner.is_running() or time.monotonic() - heartbeat[0] > 15:
            raise RuntimeError("Overlay owner stopped before case fan activation")
        ramp = FanRamp()
        session.apply(100)
        started = time.monotonic()
        stall_since = {}
        commissioned = False
        while not stop.is_set() and owner.is_running():
            now = time.monotonic()
            if now - heartbeat[0] > 15:
                # A crashed/frozen UI cannot silently retain ownership indefinitely.
                break
            data = overlay.read_sensors(computer)
            now = time.monotonic()
            if stop.is_set() or not owner.is_running() or now - heartbeat[0] > 15:
                break
            verify_shared_controller([s for hw in computer.Hardware if str(hw.HardwareType) == "Motherboard"
                                      for sub in hw.SubHardware for s in sub.Sensors])
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
                verify_full_airflow(baseline, readings)
                commissioned = True
            if commissioned and any(fan["control_pct"] is None or abs(fan["control_pct"] - session.last_command) > 3
                     for fan in readings):
                raise RuntimeError("Fan command readback differs; possible firmware/controller conflict")
            command = ramp.update(demand, now)
            if command != session.last_command:
                session.apply(command)
            write_status(status_path, "active" if commissioned else "checking",
                         command_pct=command, reason=reason, fans=readings, baseline=baseline,
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
                     restore_errors=restore_errors, baseline=baseline)
    return 1 if error or restore_errors else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", required=True)
    parser.add_argument("--owner-pid", required=True, type=int)
    parser.add_argument("--owner-created", required=True, type=float)
    args = parser.parse_args()
    try:
        with WorkerMutex():
            return worker(args.status, args.owner_pid, args.owner_created)
    except Exception as exc:
        write_status(args.status, "error", reason=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
