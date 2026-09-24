"""Relay a Codex-launched HeatMap to its independent Windows logon task."""
import psutil


_CODEX_PROCESS_NAMES = frozenset({"codex.exe", "codex-code-mode-host.exe"})


def launched_from_codex(process=None):
    """Check the process ancestry before elevation obscures the launcher."""
    try:
        current = process or psutil.Process()
        for _ in range(32):
            parent = current.parent()
            if parent is None:
                return False
            if parent.name().casefold() in _CODEX_PROCESS_NAMES:
                return True
            current = parent
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return False
    return False


def relay_to_scheduled_task():
    """Return 0 to launch normally, 1 if relayed, or 2 if relay failed."""
    if not launched_from_codex():
        return 0

    import overlay

    if not overlay.is_autostart_enabled():
        print(
            "HeatMap was started by Codex, but its verified independent Windows "
            "task is disabled or unavailable. Launch HeatMap from Explorer and "
            "enable Autostart before launching it from Codex."
        )
        return 2

    script = (
        overlay._TASK_MODULE_IMPORT
        + f"Start-ScheduledTask -TaskName '{overlay.AUTOSTART_TASK}' "
        + "-TaskPath '\\' -ErrorAction Stop"
    )
    result, error = overlay._run_task_powershell(script)
    if error or result is None or result.returncode != 0:
        message = error or overlay._completed_process_message(result)
        print(f"Could not start HeatMap independently through Windows Task Scheduler: {message}")
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(relay_to_scheduled_task())
