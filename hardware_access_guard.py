"""Process-name-only exclusion for tools that can compete for sensor hardware."""
import psutil


CONFLICTING_PROCESS_NAMES = frozenset({
    "fancontrol.exe", "siv.exe", "gcc.exe", "easytune.exe",
    "cpuz.exe", "cpuz_x64.exe", "hwinfo32.exe", "hwinfo64.exe",
    "amdryzenmaster.exe", "ryzenmaster.exe", "amd-ryzen-master.exe",
    "amd ryzen master.exe",
})
_UNREADABLE = object()
_INVENTORY_ERROR = (
    "Cannot verify which hardware monitoring tools are running. "
    "Close other hardware monitoring or fan-control tools, then restart HeatMap."
)


class HardwareAccessConflict(RuntimeError):
    """Exclusive sensor access is blocked or cannot be verified."""


def hardware_conflicts():
    """Return canonical executable names; never inspect paths or command lines."""
    conflicts = set()
    try:
        # psutil substitutes ad_value when reading a protected process fails.
        # An unreadable name must not silently turn an incomplete scan into OK.
        processes = iter(psutil.process_iter(["name"], ad_value=_UNREADABLE))
        while True:
            try:
                process = next(processes)
            except StopIteration:
                break
            except psutil.NoSuchProcess:
                continue
            try:
                info = getattr(process, "info", None)
            except psutil.NoSuchProcess:
                continue
            if not isinstance(info, dict):
                raise HardwareAccessConflict(_INVENTORY_ERROR)
            name = info.get("name", _UNREADABLE)
            # psutil reports Windows' Secure System kernel entry as an empty
            # executable basename. Unreadable names use ad_value instead.
            if isinstance(name, str) and name == "":
                continue
            if not isinstance(name, str) or not name.strip():
                raise HardwareAccessConflict(_INVENTORY_ERROR)
            name = name.casefold()
            if name in CONFLICTING_PROCESS_NAMES:
                conflicts.add(name)
    except (psutil.Error, OSError) as exc:
        raise HardwareAccessConflict(_INVENTORY_ERROR) from exc
    return sorted(conflicts)


def require_hardware_access():
    """Reject a conflict before acquiring or polling hardware-monitor sensors."""
    conflicts = hardware_conflicts()
    if conflicts:
        raise HardwareAccessConflict(
            "Other hardware monitoring tools are running: " + ", ".join(conflicts) + ". "
            "Close these tools, then restart HeatMap before reading sensors or controlling fans."
        )
