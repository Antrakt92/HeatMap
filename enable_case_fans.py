"""One-time, explicitly launched elevated case fan verification and activation."""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import psutil

import overlay
from case_fans import FanWorkerClient


def close_previous_overlay():
    target = os.path.normcase(os.path.abspath(Path(__file__).with_name("overlay.py")))
    processes = []
    for process in psutil.process_iter(["name", "cmdline"]):
        if process.pid == os.getpid() or not (process.info["name"] or "").lower().startswith("python"):
            continue
        command = process.info["cmdline"] or []
        if any(os.path.normcase(os.path.abspath(arg)) == target for arg in command[1:]):
            processes.append(process)
    if not processes:
        return
    pids = {p.pid for p in processes}
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
    user.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user.PostMessageW.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
    user.EnumChildWindows.argtypes = (wintypes.HWND, callback_type, wintypes.LPARAM)
    user.GetDesktopWindow.restype = wintypes.HWND

    @callback_type
    def close_window(hwnd, _param):
        pid = wintypes.DWORD()
        user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids:
            title = ctypes.create_unicode_buffer(256)
            user.GetWindowTextW(hwnd, title, 256)
            if title.value == "Temp Overlay":
                user.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE runs normal cleanup.
        return True

    user.EnumChildWindows(user.GetDesktopWindow(), close_window, 0)
    _gone, alive = psutil.wait_procs(processes, timeout=12)
    if alive:
        raise RuntimeError("Previous HeatMap did not close normally. Close it with X and run activation again.")


def verify_worker(client, samples, duration=20):
    client.start()
    deadline = time.monotonic() + 60
    active_since = None
    failure = None
    try:
        while time.monotonic() < deadline:
            status = client.poll()
            if status["state"] in ("error", "stopped", "off"):
                raise RuntimeError(status.get("reason", "Controller stopped before verification"))
            if status["state"] == "active":
                samples.append(status)
                active_since = active_since or time.monotonic()
                if time.monotonic() - active_since >= duration:
                    break
            time.sleep(2)
        else:
            raise RuntimeError("Case fan verification timed out")
    except Exception as exc:
        failure = exc
    finally:
        client.stop()
        if client.process is not None:
            try:
                client.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                raise RuntimeError("Native fan restore did not finish. Restart Windows before retrying.")
    restored = client.poll()
    if restored.get("state") != "stopped" or restored.get("restore_errors"):
        raise RuntimeError("Fan restore not confirmed: " + str(restored))
    if failure:
        raise failure
    return restored


def main():
    directory = Path(os.environ["LOCALAPPDATA"]) / "HeatMap"
    directory.mkdir(parents=True, exist_ok=True)
    report = dict(version=overlay.VERSION, time=time.time(), state="checking", samples=[])
    report_path = directory / "activation-result.json"

    def save():
        temporary = report_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(report_path)

    save()
    try:
        if not overlay._is_admin():
            raise RuntimeError("Run enable_case_fans.bat and accept the Windows administrator prompt")
        preflight = subprocess.run([sys.executable, str(Path(__file__).with_name("setup.py")), "--preflight"],
                                   capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=60)
        if preflight.returncode:
            raise RuntimeError("Runtime preflight failed: " + preflight.stdout + preflight.stderr)
        close_previous_overlay()
        if not overlay.acquire_single_instance():
            raise RuntimeError("Another HeatMap instance is still running")
        try:
            client = FanWorkerClient(overlay.APP_DIR)
            report["restore"] = verify_worker(client, report["samples"])
            config, error = overlay.load_config_result()
            if error:
                raise RuntimeError(error)
            if Path(overlay.CONFIG_PATH).exists():
                shutil.copy2(overlay.CONFIG_PATH, directory / f"config-before-fans-{time.time_ns()}.json")
            config.update(case_fans_enabled=True, alerts_enabled=True)
            ok, message = overlay.save_config(config)
            if not ok:
                raise RuntimeError(message)
            report["autostart_ok"], report["autostart_message"] = overlay.enable_autostart()
        finally:
            overlay.release_single_instance()
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        process = subprocess.Popen([str(pythonw), str(Path(__file__).with_name("overlay.py"))],
                                   cwd=overlay.APP_DIR, creationflags=subprocess.CREATE_NO_WINDOW)
        report.update(state="verified_and_launched", overlay_pid=process.pid,
                      note="Check the new overlay AIRFLOW status for continued operation")
    except Exception as exc:
        report.update(state="error", reason=str(exc))
    save()
    if report["state"] == "error":
        overlay._show_error_message("HeatMap activation", report["reason"])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
