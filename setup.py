"""Restore and verify the complete locked hardware-monitoring runtime."""
import argparse
from contextlib import contextmanager
import ctypes
import ctypes.wintypes
import hashlib
import importlib
import importlib.metadata
import io
import json
import math
import msvcrt
import os
import platform
import posixpath
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import zipfile
import urllib.request
try:
    import winreg
except ImportError:  # pragma: no cover - setup is Windows-only at runtime.
    winreg = None

APP_DIR = os.path.dirname(os.path.abspath(__file__))
LIB_DIR = os.path.join(APP_DIR, "lib")
MANIFEST_PATH = os.path.join(APP_DIR, "lib_manifest.json")
RUNTIME_SOURCES_PATH = os.path.join(APP_DIR, "runtime_sources.json")
RUNTIME_LOCK_PATH = os.path.join(APP_DIR, "runtime-lock.json")
CONSTRAINTS_PATH = os.path.join(APP_DIR, "constraints-known-good.txt")
MANIFEST_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_DLL_RE = re.compile(r"^lib/[^/\\]+\.dll$")
_SOURCE_TYPES = {"runtime-lock"}
_SUPPORTED_MACHINES = {"amd64", "x86_64", "x64"}
_PREFLIGHT_MODULES = (
    ("psutil", "psutil"),
    ("clr", "pythonnet"),
)
# SYNC: overlay.py::_INSTANCE_MUTEX_NAME. Restore must not replace loaded CLR DLLs.
_OVERLAY_INSTANCE_MUTEX = r"Local\HeatMapOverlay"
_ERROR_ALREADY_EXISTS = 183


class SetupError(Exception):
    """Recoverable setup failure that should become a non-zero CLI exit code."""


def _is_overlay_running():
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel32.CreateMutexW.restype = ctypes.wintypes.HANDLE
    kernel32.GetLastError.restype = ctypes.wintypes.DWORD
    kernel32.CloseHandle.argtypes = [ctypes.wintypes.HANDLE]
    kernel32.CloseHandle.restype = ctypes.c_bool
    handle = kernel32.CreateMutexW(None, False, _OVERLAY_INSTANCE_MUTEX)
    if not handle:
        return False
    try:
        return kernel32.GetLastError() == _ERROR_ALREADY_EXISTS
    finally:
        kernel32.CloseHandle(handle)


def _unsupported_runtime_message(sys_platform=None, maxsize=None, machine=None):
    sys_platform = sys.platform if sys_platform is None else sys_platform
    maxsize = sys.maxsize if maxsize is None else maxsize
    machine = platform.machine() if machine is None else machine

    if sys_platform != "win32":
        return "HeatMap setup supports 64-bit Windows only."
    if maxsize <= 2 ** 32:
        return "HeatMap setup requires 64-bit Python."
    normalized_machine = str(machine or "").strip().lower()
    if normalized_machine not in _SUPPORTED_MACHINES:
        return (
            "HeatMap setup currently supports x64 Windows/Python only "
            f"(detected machine: {machine or 'unknown'})."
        )
    return None


def load_lib_manifest(manifest_path=MANIFEST_PATH):
    with open(manifest_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _relative_dll_path(path, lib_dir=LIB_DIR):
    return f"lib/{os.path.basename(path)}"


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_entries_by_file(manifest):
    messages = []
    if not isinstance(manifest, dict):
        return {}, ["manifest must be a JSON object"]
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        messages.append(f"manifest_version must be {MANIFEST_VERSION}")
    files = manifest.get("files")
    if not isinstance(files, list):
        return {}, messages + ["manifest files must be a list"]

    entries = {}
    required_fields = {"file", "sha256", "size", "required", "source", "notes"}
    for index, entry in enumerate(files):
        label = f"manifest files[{index}]"
        if not isinstance(entry, dict):
            messages.append(f"{label} must be an object")
            continue
        missing = sorted(required_fields - set(entry))
        if missing:
            messages.append(f"{label} missing fields: {', '.join(missing)}")
            continue

        file_path = entry["file"]
        if (
            not isinstance(file_path, str)
            or not _MANIFEST_DLL_RE.match(file_path)
            or posixpath.basename(file_path) in ("", ".dll")
        ):
            messages.append(f"{label} has invalid file path: {file_path!r}")
            continue
        key = file_path.lower()
        if key in entries:
            messages.append(f"duplicate manifest file: {file_path}")
            continue

        sha256 = entry["sha256"]
        if not isinstance(sha256, str) or not _SHA256_RE.match(sha256):
            messages.append(f"{file_path} has invalid sha256")
        size = entry["size"]
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            messages.append(f"{file_path} has invalid size")
        if not isinstance(entry["required"], bool):
            messages.append(f"{file_path} required must be bool")
        if not isinstance(entry["notes"], str) or not entry["notes"].strip():
            messages.append(f"{file_path} notes must be non-empty string")

        source = entry["source"]
        if not isinstance(source, dict):
            messages.append(f"{file_path} source must be object")
        else:
            source_type = source.get("type")
            if source_type not in _SOURCE_TYPES:
                messages.append(f"{file_path} has invalid source type: {source_type!r}")
            if source_type == "runtime-lock":
                for field in ("lock_file", "package", "version", "package_path"):
                    if not isinstance(source.get(field), str) or not source[field]:
                        messages.append(f"{file_path} source.{field} must be non-empty string")
        entries[key] = entry
    return entries, messages


def verify_lib_manifest(lib_dir=LIB_DIR, manifest_path=MANIFEST_PATH, allow_extra_dlls=False):
    try:
        manifest = load_lib_manifest(manifest_path)
    except Exception as e:
        return False, [f"failed to load manifest: {e}"]

    entries, messages = _manifest_entries_by_file(manifest)
    if messages:
        return False, messages

    actual = {}
    if os.path.isdir(lib_dir):
        for name in os.listdir(lib_dir):
            path = os.path.join(lib_dir, name)
            if os.path.isfile(path) and name.lower().endswith(".dll"):
                actual[_relative_dll_path(path, lib_dir).lower()] = path

    expected_keys = set(entries)
    actual_keys = set(actual)
    for key in sorted(expected_keys - actual_keys):
        messages.append(f"missing DLL: {entries[key]['file']}")
    if not allow_extra_dlls:
        for key in sorted(actual_keys - expected_keys):
            messages.append(f"extra DLL not in manifest: {_relative_dll_path(actual[key], lib_dir)}")

    for key in sorted(expected_keys & actual_keys):
        entry = entries[key]
        path = actual[key]
        actual_size = os.path.getsize(path)
        if actual_size != entry["size"]:
            messages.append(f"size mismatch for {entry['file']}: expected {entry['size']}, got {actual_size}")
        actual_hash = _sha256_file(path)
        if actual_hash != entry["sha256"]:
            messages.append(f"hash mismatch for {entry['file']}: expected {entry['sha256']}, got {actual_hash}")

    return not messages, messages


def _print_manifest_result(ok, messages):
    if ok:
        print("DLL manifest verification OK")
        return
    print("DLL manifest verification failed:")
    for message in messages:
        print(f"  ERROR: {message}")


def _read_known_good_versions(constraints_path=CONSTRAINTS_PATH):
    versions = {}
    with open(constraints_path, "r", encoding="utf-8") as constraints_file:
        for raw_line in constraints_file:
            line = raw_line.partition("#")[0].strip()
            if not line:
                continue
            if "==" not in line:
                raise ValueError(f"constraint must use an exact version: {line}")
            package_name, version = (part.strip() for part in line.split("==", 1))
            if not package_name or not version:
                raise ValueError(f"invalid exact constraint: {line}")
            versions[package_name] = version
    if not versions:
        raise ValueError("known-good constraints are empty")
    return versions


def _check_preflight_dependencies(
    import_module=importlib.import_module,
    distribution_version=importlib.metadata.version,
    constraints_path=CONSTRAINTS_PATH,
):
    messages = []
    for module_name, package_name in _PREFLIGHT_MODULES:
        try:
            import_module(module_name)
        except Exception as e:
            messages.append(f"missing or broken dependency {package_name} ({module_name}): {e}")

    try:
        known_good_versions = _read_known_good_versions(constraints_path)
    except (OSError, ValueError) as e:
        messages.append(f"known-good dependency constraints are unavailable or invalid ({constraints_path}): {e}")
        return messages

    for package_name, expected_version in known_good_versions.items():
        try:
            actual_version = distribution_version(package_name)
        except Exception as e:
            messages.append(f"missing or broken locked dependency {package_name}: {e}")
            continue
        if actual_version != expected_version:
            messages.append(
                f"unsupported dependency version {package_name}: "
                f"expected {expected_version}, got {actual_version}"
            )
    return messages


def _check_lhm_bridge(import_module=importlib.import_module, lib_dir=LIB_DIR):
    dll_path = os.path.abspath(os.path.join(lib_dir, "LibreHardwareMonitorLib.dll"))
    try:
        clr_module = import_module("clr")
        clr_module.AddReference(dll_path)
        hardware_module = import_module("LibreHardwareMonitor.Hardware")
        for symbol in ("Computer", "HardwareType", "SensorType"):
            if not hasattr(hardware_module, symbol):
                raise AttributeError(f"missing {symbol}")
    except Exception as e:
        return [f"LibreHardwareMonitor CLR bridge failed ({dll_path}): {e}"]
    return []


def _check_staged_lhm_bridge(lib_dir):
    env = dict(os.environ)
    env["HEATMAP_STAGED_LIB"] = os.path.abspath(lib_dir)
    script = (
        "import os, clr; "
        "p=os.path.join(os.environ['HEATMAP_STAGED_LIB'],'LibreHardwareMonitorLib.dll'); "
        "clr.AddReference(p); "
        "from LibreHardwareMonitor.Hardware import Computer, HardwareType, SensorType"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
    except Exception as e:
        return [f"staged CLR bridge smoke could not run: {e}"]
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "no output").strip()
        return [f"staged CLR bridge smoke failed: {detail}"]
    return []


def _reg_key_exists(root, path, registry=None):
    registry = winreg if registry is None else registry
    if registry is None:
        return False
    try:
        with registry.OpenKey(root, path):
            return True
    except OSError:
        return False


def _reg_value(root, path, name, registry=None):
    registry = winreg if registry is None else registry
    if registry is None:
        return None
    try:
        with registry.OpenKey(root, path) as key:
            value, _value_type = registry.QueryValueEx(key, name)
            return str(value)
    except OSError:
        return None


def _service_registry_contains(text, registry=None):
    registry = winreg if registry is None else registry
    if registry is None:
        return False
    needle = text.lower()
    try:
        with registry.OpenKey(registry.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Services") as services:
            index = 0
            while True:
                try:
                    service_name = registry.EnumKey(services, index)
                except OSError:
                    break
                index += 1
                if needle in service_name.lower():
                    return True
                service_path = rf"SYSTEM\CurrentControlSet\Services\{service_name}"
                display = _reg_value(registry.HKEY_LOCAL_MACHINE, service_path, "DisplayName", registry)
                image = _reg_value(registry.HKEY_LOCAL_MACHINE, service_path, "ImagePath", registry)
                if any(value and needle in value.lower() for value in (display, image)):
                    return True
    except OSError:
        return False
    return False


def is_pawnio_driver_installed(registry=None, env=None, path_exists=os.path.exists):
    registry = winreg if registry is None else registry
    env = os.environ if env is None else env
    if registry is not None and _reg_key_exists(
        registry.HKEY_LOCAL_MACHINE,
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO",
        registry,
    ):
        return True
    program_files = env.get("ProgramFiles")
    if program_files and path_exists(os.path.join(program_files, "PawnIO", "PawnIOLib.dll")):
        return True
    return _service_registry_contains("pawnio", registry)


def is_windows_restart_pending(registry=None):
    registry = winreg if registry is None else registry
    if registry is None:
        return False
    try:
        with registry.OpenKey(
            registry.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager",
        ) as key:
            value, _value_type = registry.QueryValueEx(key, "PendingFileRenameOperations")
            return bool(value)
    except OSError:
        return False


def _load_runtime_sources(path=None, runtime_lock_path=RUNTIME_LOCK_PATH):
    path = RUNTIME_SOURCES_PATH if path is None else path
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise SetupError(f"failed to load runtime sources: {e}") from e
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise SetupError("runtime sources schema_version must be 1")
    lhm = data.get("libre_hardware_monitor")
    pawnio = data.get("pawnio")
    if not isinstance(lhm, dict) or not isinstance(pawnio, dict):
        raise SetupError("runtime sources must contain LHM and PawnIO metadata")
    for field in ("version",):
        if not isinstance(lhm.get(field), str) or not lhm[field].strip():
            raise SetupError(f"runtime sources LHM {field} must be a non-empty string")
    for field in ("version", "compatible_lhm", "url", "sha256"):
        if not isinstance(pawnio.get(field), str) or not pawnio[field].strip():
            raise SetupError(f"runtime sources PawnIO {field} must be a non-empty string")
    if not pawnio["url"].startswith("https://"):
        raise SetupError("runtime sources PawnIO URL must use HTTPS")
    if not _SHA256_RE.match(pawnio["sha256"]):
        raise SetupError("runtime sources PawnIO sha256 is invalid")
    if isinstance(pawnio.get("size"), bool) or not isinstance(pawnio.get("size"), int) or pawnio["size"] <= 0:
        raise SetupError("runtime sources PawnIO size must be a positive integer")
    authenticode = pawnio.get("authenticode")
    if not isinstance(authenticode, dict):
        raise SetupError("runtime sources PawnIO authenticode metadata is missing")
    for field in ("status", "subject", "thumbprint"):
        if not isinstance(authenticode.get(field), str) or not authenticode[field].strip():
            raise SetupError(f"runtime sources PawnIO authenticode.{field} is invalid")
    if authenticode["status"].casefold() != "valid":
        raise SetupError("runtime sources PawnIO Authenticode status must be Valid")
    if not re.fullmatch(r"[0-9A-Fa-f]{40}", authenticode["thumbprint"]):
        raise SetupError("runtime sources PawnIO Authenticode thumbprint is invalid")

    _runtime_lock, locked = load_runtime_lock(runtime_lock_path)
    lhm_entries = [
        entry for entry in locked.values()
        if entry["package"].casefold() == "librehardwaremonitorlib"
    ]
    versions = {entry["version"] for entry in lhm_entries}
    if len(lhm_entries) != 1 or versions != {lhm["version"]}:
        raise SetupError("runtime sources LHM version does not match runtime-lock.json")
    if pawnio["compatible_lhm"] != lhm["version"]:
        raise SetupError("PawnIO compatibility does not match the locked LHM version")
    return data


def _pawnio_download_path(metadata, env=None):
    env = os.environ if env is None else env
    base = env.get("LOCALAPPDATA") or tempfile.gettempdir()
    return os.path.join(
        base,
        "HeatMap",
        "downloads",
        f"PawnIO_setup_{metadata['version']}.exe",
    )


def _verify_authenticode(path, expected, run_command=None):
    run_command = subprocess.run if run_command is None else run_command
    env = dict(os.environ)
    env["HEATMAP_VERIFY_PATH"] = os.path.abspath(path)
    system_root = env.get("SystemRoot", r"C:\Windows")
    powershell_path = os.path.join(
        system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe"
    )
    # WHY: Windows PowerShell can fail to load its Security module when PSModulePath
    # contains PowerShell 7 modules first; signature verification must be deterministic.
    env["PSModulePath"] = os.path.join(
        system_root, "System32", "WindowsPowerShell", "v1.0", "Modules"
    )
    script = (
        "$s = Get-AuthenticodeSignature -LiteralPath $env:HEATMAP_VERIFY_PATH; "
        "[pscustomobject]@{Status=$s.Status.ToString(); "
        "Subject=$s.SignerCertificate.Subject; "
        "Thumbprint=$s.SignerCertificate.Thumbprint} | ConvertTo-Json -Compress"
    )
    try:
        result = run_command(
            [powershell_path, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
    except Exception as e:
        return [f"Authenticode verification failed to run: {e}"]
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "no output").strip()
        return [f"Authenticode verification failed: {detail}"]
    try:
        actual = json.loads(result.stdout)
    except Exception as e:
        return [f"Authenticode verification returned invalid JSON: {e}"]
    messages = []
    for field in ("status", "subject", "thumbprint"):
        actual_value = str(actual.get(field.capitalize(), "")).strip()
        expected_value = str(expected.get(field, "")).strip()
        if actual_value.casefold() != expected_value.casefold():
            messages.append(
                f"Authenticode {field} mismatch: expected {expected_value!r}, got {actual_value!r}"
            )
    return messages


def _verify_pawnio_file(path, metadata, authenticode_checker=None):
    authenticode_checker = _verify_authenticode if authenticode_checker is None else authenticode_checker
    messages = []
    try:
        actual_size = os.path.getsize(path)
    except OSError as e:
        return [f"could not read PawnIO installer: {e}"]
    if actual_size != metadata.get("size"):
        messages.append(
            f"PawnIO size mismatch: expected {metadata.get('size')}, got {actual_size}"
        )
    actual_hash = _sha256_file(path)
    if actual_hash != metadata.get("sha256"):
        messages.append(
            f"PawnIO hash mismatch: expected {metadata.get('sha256')}, got {actual_hash}"
        )
    if not messages:
        messages.extend(authenticode_checker(path, metadata.get("authenticode", {})))
    return messages


def download_pawnio(urlopen=urllib.request.urlopen, env=None):
    metadata = _load_runtime_sources()["pawnio"]
    destination = _pawnio_download_path(metadata, env=env)
    if os.path.exists(destination) and not _verify_pawnio_file(destination, metadata):
        return destination

    try:
        os.makedirs(os.path.dirname(destination), exist_ok=True)
    except OSError as e:
        raise SetupError(f"could not create PawnIO download directory: {e}") from e
    staging = f"{destination}.download"
    try:
        request = urllib.request.Request(metadata["url"], headers={"User-Agent": "HeatMap setup"})
        try:
            with urlopen(request, timeout=60, context=ssl.create_default_context()) as response:
                data = response.read()
        except Exception as e:
            raise SetupError(f"error downloading PawnIO {metadata['version']}: {e}") from e
        try:
            with open(staging, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
        except OSError as e:
            raise SetupError(f"could not stage PawnIO installer: {e}") from e
        messages = _verify_pawnio_file(staging, metadata)
        if messages:
            raise SetupError("; ".join(messages))
        try:
            os.replace(staging, destination)
        except OSError as e:
            raise SetupError(f"could not publish verified PawnIO installer: {e}") from e
        return destination
    finally:
        try:
            if os.path.exists(staging):
                os.remove(staging)
        except OSError:
            pass


def _check_pawnio_driver(path_exists=os.path.exists):
    if is_pawnio_driver_installed(path_exists=path_exists):
        return []
    if is_windows_restart_pending():
        return [
            "Windows has pending installer file operations; restart Windows before installing PawnIO. "
            "After restart run 'python setup.py --download-pawnio', launch the verified installer, "
            "and restart Windows again."
        ]
    return [
        "PawnIO driver is not installed; run 'python setup.py --download-pawnio', "
        "then launch the verified installer shown by setup and restart Windows."
    ]


def _print_pawnio_warnings(messages):
    if not messages:
        return
    print("PawnIO warning:")
    for message in messages:
        print(f"  WARNING: {message}")


def run_preflight():
    messages = []

    unsupported = _unsupported_runtime_message()
    if unsupported:
        messages.append(unsupported)

    messages.extend(_check_preflight_dependencies())

    ok, manifest_messages = verify_lib_manifest(allow_extra_dlls=True)
    if not ok:
        messages.extend(f"DLL runtime: {message}" for message in manifest_messages)

    if not messages:
        messages.extend(_check_lhm_bridge())

    return not messages, messages


def run_hardware_smoke(import_module=importlib.import_module, lib_dir=LIB_DIR):
    """Open LHM and require a real positive CPU temperature reading."""
    messages = []
    computer = None
    try:
        clr_module = import_module("clr")
        clr_module.AddReference(os.path.abspath(os.path.join(lib_dir, "LibreHardwareMonitorLib.dll")))
        hardware_module = import_module("LibreHardwareMonitor.Hardware")
        computer = hardware_module.Computer()
        for field in (
            "IsCpuEnabled", "IsGpuEnabled", "IsStorageEnabled",
            "IsMemoryEnabled", "IsMotherboardEnabled",
        ):
            setattr(computer, field, True)
        computer.Open()
        cpu_blocks = 0
        cpu_temperatures = []
        hardware_items = list(computer.Hardware)
        for hardware in hardware_items:
            hardware.Update()
            if hardware.HardwareType != hardware_module.HardwareType.Cpu:
                continue
            cpu_blocks += 1
            sensors = list(hardware.Sensors)
            for sub_hardware in hardware.SubHardware:
                sub_hardware.Update()
                sensors.extend(list(sub_hardware.Sensors))
            for sensor in sensors:
                if sensor.SensorType != hardware_module.SensorType.Temperature:
                    continue
                try:
                    value = float(sensor.Value)
                except (TypeError, ValueError, OverflowError):
                    continue
                if math.isfinite(value) and 0 < value <= 150:
                    cpu_temperatures.append(value)
        if cpu_blocks == 0:
            messages.append("LHM exposed no CPU hardware block")
        elif not cpu_temperatures:
            if is_windows_restart_pending():
                messages.append(
                    "CPU temperature is unavailable and Windows has pending installer operations; "
                    "restart Windows before installing/retrying PawnIO, restart again after install, "
                    "then rerun --hardware-smoke"
                )
            else:
                messages.append(
                    "CPU temperature is unavailable; confirm elevation, install the locked PawnIO driver, "
                    "restart Windows, and rerun --hardware-smoke"
                )
        print(
            f"Hardware smoke: blocks={len(hardware_items)}, cpu_blocks={cpu_blocks}, "
            f"cpu_temperature_readings={len(cpu_temperatures)}"
        )
    except Exception as e:
        messages.append(f"hardware smoke failed: {e}")
    finally:
        if computer is not None:
            try:
                computer.Close()
            except Exception as e:
                messages.append(f"hardware smoke could not close LHM: {e}")
    return not messages, messages


def _print_preflight_result(ok, messages):
    if ok:
        print("Preflight OK")
        return
    print("Preflight failed:")
    for message in messages:
        print(f"  ERROR: {message}")


def _dll_candidates(all_files, dll_path):
    """Return DLL entries with the same filename for diagnostics."""
    target = os.path.basename(dll_path).lower()
    return [
        entry for entry in all_files
        if entry.lower().endswith(".dll") and os.path.basename(entry).lower() == target
    ]


def load_runtime_lock(runtime_lock_path=RUNTIME_LOCK_PATH):
    try:
        with open(runtime_lock_path, "r", encoding="utf-8") as f:
            runtime_lock = json.load(f)
    except Exception as e:
        raise SetupError(f"failed to load runtime lock: {e}") from e

    messages = []
    if not isinstance(runtime_lock, dict):
        raise SetupError("runtime lock must be a JSON object")
    if runtime_lock.get("schema_version") != 1:
        messages.append("schema_version must be 1")
    if not isinstance(runtime_lock.get("target_framework"), str) or not runtime_lock["target_framework"].strip():
        messages.append("target_framework must be a non-empty string")
    source_commit = runtime_lock.get("source_commit")
    if not isinstance(source_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", source_commit):
        messages.append("source_commit must be a lowercase 40-character git hash")
    files = runtime_lock.get("files")
    if not isinstance(files, list) or not files:
        messages.append("files must be a non-empty list")
        files = []

    entries = {}
    packages = {}
    required = {
        "file", "package", "version", "package_path", "nupkg_sha256", "license"
    }
    for index, entry in enumerate(files):
        label = f"runtime lock files[{index}]"
        if not isinstance(entry, dict):
            messages.append(f"{label} must be an object")
            continue
        missing = sorted(required - set(entry))
        if missing:
            messages.append(f"{label} missing fields: {', '.join(missing)}")
            continue
        file_path = entry["file"]
        if not isinstance(file_path, str) or not _MANIFEST_DLL_RE.match(file_path):
            messages.append(f"{label} has invalid file path: {file_path!r}")
            continue
        key = file_path.casefold()
        if key in entries:
            messages.append(f"duplicate runtime lock file: {file_path}")
            continue
        entries[key] = entry
        for field in ("package", "version", "package_path", "license"):
            if not isinstance(entry[field], str) or not entry[field].strip():
                messages.append(f"{file_path} {field} must be a non-empty string")
        nupkg_hash = entry["nupkg_sha256"]
        if not isinstance(nupkg_hash, str) or not _SHA256_RE.match(nupkg_hash):
            messages.append(f"{file_path} has invalid nupkg_sha256")
        package_key = (str(entry["package"]).casefold(), str(entry["version"]).casefold())
        previous_hash = packages.setdefault(package_key, nupkg_hash)
        if previous_hash != nupkg_hash:
            messages.append(
                f"package {entry['package']} {entry['version']} has conflicting hashes"
            )

    if messages:
        raise SetupError("invalid runtime lock: " + "; ".join(messages))
    return runtime_lock, entries


def _runtime_package_url(package, version):
    package_lower = package.casefold()
    version_lower = version.casefold()
    return (
        "https://api.nuget.org/v3-flatcontainer/"
        f"{package_lower}/{version_lower}/{package_lower}.{version_lower}.nupkg"
    )


def _validate_runtime_lock_against_manifest(
    lock_entries,
    manifest_path=MANIFEST_PATH,
    runtime_lock_filename=os.path.basename(RUNTIME_LOCK_PATH),
):
    manifest = load_lib_manifest(manifest_path)
    manifest_entries, messages = _manifest_entries_by_file(manifest)
    if messages:
        raise SetupError("invalid DLL manifest: " + "; ".join(messages))
    if set(lock_entries) != set(manifest_entries):
        missing = sorted(set(manifest_entries) - set(lock_entries))
        extra = sorted(set(lock_entries) - set(manifest_entries))
        raise SetupError(
            f"runtime lock/manifest file set mismatch (missing={missing}, extra={extra})"
        )
    for key, locked in lock_entries.items():
        source = manifest_entries[key]["source"]
        expected = {
            "type": "runtime-lock",
            "lock_file": runtime_lock_filename,
            "package": locked["package"],
            "version": locked["version"],
            "package_path": locked["package_path"],
        }
        if source != expected:
            raise SetupError(
                f"runtime lock metadata does not match manifest for {locked['file']}"
            )
    return manifest_entries


def _download_runtime_package(package, version, expected_hash, urlopen):
    url = _runtime_package_url(package, version)
    request = urllib.request.Request(url, headers={"User-Agent": "HeatMap setup"})
    try:
        with urlopen(request, timeout=60, context=ssl.create_default_context()) as response:
            data = response.read()
    except Exception as e:
        raise SetupError(f"error downloading {package} {version}: {e}") from e
    actual_hash = hashlib.sha256(data).hexdigest()
    if actual_hash != expected_hash:
        raise SetupError(
            f"NuGet hash mismatch for {package} {version}: "
            f"expected {expected_hash}, got {actual_hash}"
        )
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise SetupError(f"downloaded package for {package} {version} is not a valid zip") from None
    return archive


def _remove_restore_tree(path, parent):
    if not path or not os.path.lexists(path):
        return
    resolved_parent = os.path.realpath(parent)
    resolved_path = os.path.realpath(path)
    if resolved_path == resolved_parent or os.path.commonpath([resolved_parent, resolved_path]) != resolved_parent:
        raise SetupError(f"refusing to remove restore path outside {resolved_parent}: {resolved_path}")
    shutil.rmtree(path)


def _write_restore_journal(path, phase):
    staging = f"{path}.tmp"
    try:
        with open(staging, "w", encoding="utf-8") as f:
            json.dump({"schema_version": 1, "phase": phase}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(staging, path)
    except OSError as e:
        raise SetupError(f"could not write runtime restore journal: {e}") from e


def _runtime_is_valid(path, manifest_path):
    if not os.path.isdir(path):
        return False
    ok, _messages = verify_lib_manifest(
        lib_dir=path,
        manifest_path=manifest_path,
        allow_extra_dlls=False,
    )
    return ok


def _recover_runtime_transaction(lib_dir=LIB_DIR, manifest_path=MANIFEST_PATH):
    parent = os.path.dirname(os.path.abspath(lib_dir))
    backup_dir = f"{lib_dir}.runtime-backup"
    journal_path = f"{lib_dir}.runtime-restore.json"
    if not os.path.lexists(backup_dir) and not os.path.exists(journal_path):
        return

    journal = None
    if os.path.exists(journal_path):
        try:
            with open(journal_path, "r", encoding="utf-8") as f:
                journal = json.load(f)
        except Exception as e:
            raise SetupError(f"could not read runtime restore journal: {e}") from e
        if (
            not isinstance(journal, dict)
            or journal.get("schema_version") != 1
            or journal.get("phase") not in ("prepared", "backup-created", "published")
        ):
            raise SetupError(f"invalid runtime restore journal: {journal_path}")

    lib_valid = _runtime_is_valid(lib_dir, manifest_path)
    backup_valid = _runtime_is_valid(backup_dir, manifest_path)
    if lib_valid:
        if os.path.lexists(backup_dir):
            _remove_restore_tree(backup_dir, parent)
        try:
            if os.path.exists(journal_path):
                os.remove(journal_path)
        except OSError as e:
            raise SetupError(f"could not clear stale runtime journal: {e}") from e
        return
    if not os.path.lexists(backup_dir) and journal and journal["phase"] == "prepared":
        try:
            os.remove(journal_path)
        except OSError as e:
            raise SetupError(f"could not clear pre-swap runtime journal: {e}") from e
        return
    if not os.path.lexists(lib_dir) and not os.path.lexists(backup_dir):
        try:
            if os.path.exists(journal_path):
                os.remove(journal_path)
        except OSError as e:
            raise SetupError(f"could not clear interrupted fresh-install journal: {e}") from e
        return
    if backup_valid:
        if os.path.lexists(lib_dir):
            _remove_restore_tree(lib_dir, parent)
        try:
            os.replace(backup_dir, lib_dir)
            if os.path.exists(journal_path):
                os.remove(journal_path)
        except OSError as e:
            raise SetupError(f"could not recover previous runtime from {backup_dir}: {e}") from e
        return
    raise SetupError(
        "incomplete runtime transaction could not be recovered: "
        f"current={lib_dir}, backup={backup_dir}, journal={journal_path}"
    )


@contextmanager
def _runtime_restore_lock(app_dir=APP_DIR):
    lock_id = hashlib.sha256(os.path.abspath(app_dir).encode("utf-8")).hexdigest()[:16]
    lock_path = os.path.join(tempfile.gettempdir(), f"HeatMap-runtime-{lock_id}.lock")
    lock_file = open(lock_path, "a+b")
    try:
        if os.path.getsize(lock_path) == 0:
            lock_file.write(b"\0")
            lock_file.flush()
        lock_file.seek(0)
        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as e:
            raise SetupError("another HeatMap runtime restore is already running") from e
        try:
            yield
        finally:
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
    finally:
        lock_file.close()


def _publish_runtime(staging_dir, lib_dir=LIB_DIR, manifest_path=MANIFEST_PATH):
    parent = os.path.dirname(os.path.abspath(lib_dir))
    backup_dir = f"{lib_dir}.runtime-backup"
    journal_path = f"{lib_dir}.runtime-restore.json"
    if os.path.lexists(backup_dir) or os.path.exists(journal_path):
        raise SetupError("runtime transaction state was not recovered before publish")
    _write_restore_journal(journal_path, "prepared")
    if os.path.lexists(lib_dir):
        try:
            os.replace(lib_dir, backup_dir)
        except OSError as e:
            try:
                if os.path.exists(journal_path):
                    os.remove(journal_path)
            except OSError:
                pass
            raise SetupError(
                f"could not move the current runtime; close HeatMap and retry: {e}"
            ) from e
        try:
            _write_restore_journal(journal_path, "backup-created")
        except SetupError:
            os.replace(backup_dir, lib_dir)
            try:
                if os.path.exists(journal_path):
                    os.remove(journal_path)
            except OSError:
                pass
            raise
    try:
        os.replace(staging_dir, lib_dir)
    except Exception as publish_error:
        if os.path.lexists(backup_dir) and not os.path.lexists(lib_dir):
            try:
                os.replace(backup_dir, lib_dir)
                if os.path.exists(journal_path):
                    os.remove(journal_path)
            except Exception as rollback_error:
                raise SetupError(
                    f"runtime publish failed ({publish_error}); rollback failed ({rollback_error}); "
                    f"backup preserved at {backup_dir}"
                ) from publish_error
            raise SetupError(
                f"runtime publish failed; previous runtime restored: {publish_error}"
            ) from publish_error
        backup_note = f"; backup preserved at {backup_dir}" if os.path.lexists(backup_dir) else ""
        raise SetupError(f"runtime publish failed: {publish_error}{backup_note}") from publish_error
    if os.path.lexists(backup_dir):
        try:
            _remove_restore_tree(backup_dir, parent)
        except OSError as e:
            print(f"  WARNING: restored runtime is active, but old backup cleanup failed: {e}")
    try:
        if os.path.exists(journal_path):
            os.remove(journal_path)
    except OSError as e:
        print(f"  WARNING: restored runtime is active, but journal cleanup failed: {e}")


def restore_runtime(
    runtime_lock_path=RUNTIME_LOCK_PATH,
    manifest_path=MANIFEST_PATH,
    lib_dir=LIB_DIR,
    urlopen=urllib.request.urlopen,
    bridge_checker=None,
):
    bridge_checker = _check_staged_lhm_bridge if bridge_checker is None else bridge_checker
    with _runtime_restore_lock(os.path.dirname(os.path.abspath(lib_dir))):
        if (
            os.path.normcase(os.path.abspath(lib_dir)) == os.path.normcase(os.path.abspath(LIB_DIR))
            and _is_overlay_running()
        ):
            raise SetupError(
                "HeatMap is running and has CLR DLLs loaded; close the overlay before restoring runtime"
            )
        _recover_runtime_transaction(lib_dir=lib_dir, manifest_path=manifest_path)
        _runtime_lock, lock_entries = load_runtime_lock(runtime_lock_path)
        manifest_entries = _validate_runtime_lock_against_manifest(
            lock_entries,
            manifest_path,
            runtime_lock_filename=os.path.basename(runtime_lock_path),
        )
        parent = os.path.dirname(os.path.abspath(lib_dir))
        os.makedirs(parent, exist_ok=True)
        staging_dir = tempfile.mkdtemp(prefix=".lib-restore-", dir=parent)
        packages = {}
        try:
            for locked in lock_entries.values():
                package_key = (locked["package"].casefold(), locked["version"].casefold())
                packages.setdefault(package_key, []).append(locked)

            for package_entries in packages.values():
                first = package_entries[0]
                print(f"Downloading {first['package']} {first['version']}...")
                archive = _download_runtime_package(
                    first["package"], first["version"], first["nupkg_sha256"], urlopen
                )
                with archive:
                    all_files = archive.namelist()
                    for locked in package_entries:
                        package_path = locked["package_path"]
                        if package_path not in all_files:
                            candidates = _dll_candidates(all_files, package_path)
                            raise SetupError(
                                f"could not find exact DLL path for {locked['package']}: "
                                f"{package_path}. Available matching DLLs: {candidates}"
                            )
                        dll_data = archive.read(package_path)
                        manifest_entry = manifest_entries[locked["file"].casefold()]
                        actual_hash = hashlib.sha256(dll_data).hexdigest()
                        if len(dll_data) != manifest_entry["size"] or actual_hash != manifest_entry["sha256"]:
                            raise SetupError(
                                f"extracted DLL does not match manifest: {locked['file']}"
                            )
                        output_path = os.path.join(staging_dir, os.path.basename(locked["file"]))
                        with open(output_path, "wb") as f:
                            f.write(dll_data)
                            f.flush()
                            os.fsync(f.fileno())

            ok, messages = verify_lib_manifest(
                lib_dir=staging_dir,
                manifest_path=manifest_path,
                allow_extra_dlls=False,
            )
            if not ok:
                raise SetupError("staged runtime verification failed: " + "; ".join(messages))
            bridge_messages = bridge_checker(staging_dir)
            if bridge_messages:
                raise SetupError("; ".join(bridge_messages))
            _publish_runtime(staging_dir, lib_dir=lib_dir, manifest_path=manifest_path)
            staging_dir = None
        finally:
            if staging_dir and os.path.lexists(staging_dir):
                _remove_restore_tree(staging_dir, parent)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Download and verify HeatMap hardware monitor DLLs.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--verify", action="store_true", help="Only verify lib_manifest.json against lib/*.dll.")
    group.add_argument("--preflight", action="store_true", help="Check runtime, Python deps, and DLLs without downloading.")
    group.add_argument(
        "--download-pawnio",
        action="store_true",
        help="Download and verify the LHM-compatible PawnIO installer without running it.",
    )
    group.add_argument(
        "--restore-runtime",
        action="store_true",
        help="Restore the complete DLL runtime from runtime-lock.json.",
    )
    group.add_argument(
        "--hardware-smoke",
        action="store_true",
        help="Open LHM on this elevated machine and require a real CPU temperature reading.",
    )
    args = parser.parse_args(argv)

    if args.verify:
        ok, messages = verify_lib_manifest()
        _print_manifest_result(ok, messages)
        return 0 if ok else 1

    if args.preflight:
        ok, messages = run_preflight()
        _print_preflight_result(ok, messages)
        if ok:
            _print_pawnio_warnings(_check_pawnio_driver())
        return 0 if ok else 1

    if args.download_pawnio:
        try:
            path = download_pawnio()
        except (SetupError, OSError) as e:
            print(f"PawnIO download failed: {e}")
            return 1
        print("PawnIO installer verified (hash and Authenticode).")
        print(f"Run this file as administrator, then restart Windows:\n  {path}")
        return 0

    if args.hardware_smoke:
        preflight_ok, preflight_messages = run_preflight()
        if not preflight_ok:
            _print_preflight_result(preflight_ok, preflight_messages)
            return 1
        ok, messages = run_hardware_smoke()
        if not ok:
            print("Hardware smoke failed:")
            for message in messages:
                print(f"  ERROR: {message}")
        return 0 if ok else 1

    unsupported = _unsupported_runtime_message()
    if unsupported:
        print(f"Setup failed: {unsupported}")
        return 1

    try:
        restore_runtime()
    except (SetupError, OSError) as e:
        print(f"Setup failed: {e}")
        return 1
    ok, messages = verify_lib_manifest()
    _print_manifest_result(ok, messages)
    if not ok:
        return 1
    print("\nSetup complete! DLLs are in the 'lib' directory.")
    _print_pawnio_warnings(_check_pawnio_driver())
    print("You can now run the overlay with: run_as_admin.bat")
    return 0


if __name__ == "__main__":
    sys.exit(main())
