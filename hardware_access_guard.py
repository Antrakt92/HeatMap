"""Separate shared monitoring from exclusive control and driver installation."""
from pathlib import Path

import psutil


CONFLICTING_PROCESS_NAMES = frozenset({
    "fancontrol.exe", "siv.exe", "gcc.exe", "easytune.exe",
    "cpuz.exe", "cpuz_x64.exe", "hwinfo32.exe", "hwinfo64.exe",
    "amdryzenmaster.exe", "ryzenmaster.exe", "amd-ryzen-master.exe",
    "amd ryzen master.exe",
    "atisetup.exe",
})
_UNREADABLE = object()
DRIVER_INSTALLER_PROCESS_NAMES = frozenset({'atisetup.exe', 'pnputil.exe'})
RYZEN_MASTER_PROCESS_NAMES = frozenset({
    "amdryzenmaster.exe", "ryzenmaster.exe", "amd-ryzen-master.exe",
    "amd ryzen master.exe",
})
_INVENTORY_ERROR = (
    "Cannot verify which hardware monitoring tools are running. "
    "Close other hardware monitoring or fan-control tools, then restart HeatMap."
)


class HardwareAccessConflict(RuntimeError):
    """Exclusive sensor access is blocked or cannot be verified."""


def _is_gnu_compiler(process):
    """Recognize GCC's compiler/runtime layout without executing another program.

    GCC.exe is also Gigabyte Control Center. A basename or a suggestive directory
    alone is insufficient; unknown/unreadable installations remain blocked.
    Inspect only this ambiguous process, never other command lines or paths.
    """
    try:
        executable = process.exe() if hasattr(process, 'exe') else None
        if not isinstance(executable, str) or not executable:
            return False
        binary = Path(executable)
        if not binary.is_absolute() or binary.name.casefold() != 'gcc.exe':
            return False
        if binary.parent.name.casefold() != 'bin' or not binary.is_file():
            return False
        root = binary.parent.parent
        if any('gigabyte' in part.casefold() for part in root.parts):
            return False
        # Match target/version trees, or the flattened portable compiler layout.
        if (binary.parent / 'cc1.exe').is_file() and (root / 'lib/libgcc.a').is_file():
            return True
        support = root / 'libexec/gcc'
        for compiler in support.glob('*/*/cc1.exe'):
            relative = compiler.parent.relative_to(support)
            if compiler.is_file() and (root / 'lib/gcc' / relative / 'libgcc.a').is_file():
                return True
    except psutil.NoSuchProcess:
        raise
    except (psutil.Error, OSError, ValueError):
        return False
    return False


def hardware_conflicts():
    """Inspect only ambiguous GCC and PnP installer processes beyond their names."""
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
            if name == "pnputil.exe":
                try:
                    args = {arg.casefold() for arg in process.cmdline()}
                except psutil.NoSuchProcess:
                    continue
                except (psutil.AccessDenied, OSError):
                    conflicts.add(name)
                    continue
                # Package staging and device enumeration do not replace a live
                # driver. The observed /add-driver /install operation does.
                if {"/add-driver", "/install"} <= args:
                    conflicts.add(name)
                continue
            if name in CONFLICTING_PROCESS_NAMES:
                if name == 'gcc.exe':
                    try:
                        if _is_gnu_compiler(process):
                            continue
                    except psutil.NoSuchProcess:
                        continue
                conflicts.add(name)
    except (psutil.Error, OSError) as exc:
        raise HardwareAccessConflict(_INVENTORY_ERROR) from exc
    return sorted(conflicts)


def require_hardware_access(scope="control"):
    """Monitoring tools share reads; controllers require exclusive ownership.

    A monitoring process is not evidence of a driver replacement. Apply one
    read policy to the entire tool inventory rather than per-program exceptions.
    Live installers still block native reads, even alongside monitoring tools.
    """
    if scope not in ("control", "monitor"):
        raise ValueError("Unknown hardware access scope")
    conflicts = hardware_conflicts()
    if scope == "monitor" and conflicts:
        installers = sorted(set(conflicts) & DRIVER_INSTALLER_PROCESS_NAMES)
        if not installers:
            return "shared"
        raise HardwareAccessConflict(
            "Driver installation detected: " + ", ".join(installers) + ". "
            "Finish driver installation, then restart HeatMap."
        )
    if conflicts:
        raise HardwareAccessConflict(
            "Hardware monitoring or driver tools are running: " + ", ".join(conflicts) + ". "
            "Close monitoring tools or finish driver installation, then restart HeatMap."
        )
    if scope == "monitor":
        return "full"
