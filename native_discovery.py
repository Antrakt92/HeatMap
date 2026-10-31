"""Discover thermal associations from this machine's firmware, without executing AML.

ACPICA only disassembles a copied DSDT. No firmware methods/registers are called.
The external disassembler is downloaded from its official release, hash checked,
and cached in the current user's app-data directory, never run elevated here.
"""
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import tempfile
import urllib.request
import winreg


TOOL_URL = "https://github.com/open-acpica/acpica/releases/download/20260408/iasl.exe"
TOOL_SHA256 = "739c597bcee4563f18d13e73b2051bb661707713e6154cf5712a1b7eef4a5df9"
TOOL_SIZE = 1035776
PROFILE_VERSION = 4
log = logging.getLogger(__name__)


def data_directory():
    return Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "HeatMap"


def prepare_disassembler(*, download=True):
    path = data_directory() / "tools" / "acpica-20260408" / "iasl.exe"
    if path.is_file() and path.stat().st_size == TOOL_SIZE:
        if hashlib.sha256(path.read_bytes()).hexdigest() == TOOL_SHA256:
            return path
    if not download:
        raise OSError("Verified ACPI discovery tool is not prepared")
    with urllib.request.urlopen(TOOL_URL, timeout=15) as response:
        data = response.read(TOOL_SIZE + 1)
    if len(data) != TOOL_SIZE or hashlib.sha256(data).hexdigest() != TOOL_SHA256:
        raise OSError("ACPI discovery tool failed size/SHA-256 verification")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(data)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def read_dsdt():
    """Read the OS's firmware copy; retain no SMBIOS/serial-number tables."""
    def walk(path, depth=0):
        if depth > 4:
            return
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            children, values, _ = winreg.QueryInfoKey(key)
            for index in range(values):
                _name, value, kind = winreg.EnumValue(key, index)
                if kind != winreg.REG_BINARY or not 36 <= len(value) <= 4 * 1024 * 1024:
                    continue
                length = int.from_bytes(value[4:8], "little")
                if value[:4] == b"DSDT" and length == len(value) and sum(value) % 256 == 0:
                    yield value
            for index in range(min(children, 64)):
                yield from walk(path + "\\" + winreg.EnumKey(key, index), depth + 1)
    tables = list(walk(r"HARDWARE\ACPI\DSDT"))
    if len(tables) != 1:
        raise OSError("Firmware DSDT is missing or ambiguous")
    return tables[0]


def canonical_path(name, parent=""):
    parent = parent.lstrip("\\")
    if name.startswith("\\"):
        parent, name = "", name[1:]
    while name.startswith("^"):
        parent, name = parent.rpartition(".")[0], name[1:]
    parts = [part.rstrip("_").upper() for part in (parent + "." + name).split(".") if part]
    return "\\" + ".".join(parts)


def _source_views(text):
    """Index braces once, ignoring quoted text and comments without shifting offsets."""
    cleaned, masked, stack, closing = list(text), list(text), [], {}
    for match in re.finditer(r'"(?:\\.|[^"\\])*"|/\*[\s\S]*?\*/|//[^\n]*|[{}]', text):
        token, start, end = match[0], match.start(), match.end()
        if token.startswith('"'):
            masked[start:end] = ' ' * (end - start)
        elif token.startswith('/'):
            cleaned[start:end] = masked[start:end] = ' ' * (end - start)
        elif token == '{':
            stack.append(start)
        elif not stack:
            raise ValueError("Unbalanced ACPI disassembly")
        else:
            closing[stack.pop()] = start
    if stack:
        raise ValueError("Incomplete ACPI disassembly")
    return ''.join(cleaned), ''.join(masked), closing


def _direct_field(block, pattern):
    for match in re.finditer(pattern, block["mask"]):
        prefix = block["mask"][:match.start()]
        if prefix.count('{') == prefix.count('}'):
            return match
    return None


def _hardware_id(block):
    field = _direct_field(block, r'\bName\s*\(\s*(?:_HID|HHID)\s*,')
    if field:
        literal = re.match(r'\s*"([A-Z0-9]+)"', block["body"][field.end():])
        if literal:
            return literal[1]
    return None


def _resolve_reference(name, scope, known_devices):
    if name.startswith(('\\', '^')):
        target = canonical_path(name, scope)
        return target if target in known_devices else None
    while True:
        target = canonical_path(name, scope)
        if target in known_devices:
            return target
        if not scope:
            return None
        scope = scope.rpartition('.')[0] if '.' in scope else ''


def parse_thermal_profile(text, table_id=""):
    """Use explicit associations; never assign the hottest anonymous zone to a die."""
    if len(text) > 12 * 1024 * 1024:
        raise ValueError("Oversized ACPI disassembly")
    text, mask, closing = _source_views(text)
    blocks, ancestry = [], []
    for match in re.finditer(r'\b(Scope|Device|ThermalZone)\s*\(\s*([\\^A-Za-z0-9_.]+)\s*\)\s*{', mask):
        opening, end = match.end() - 1, closing[match.end() - 1]
        while ancestry and ancestry[-1]["end"] < opening:
            ancestry.pop()
        parent = ancestry[-1]["path"] if ancestry else ""
        block = {"kind": match[1], "path": canonical_path(match[2], parent),
                 "start": opening, "end": end, "body": text[opening + 1:end],
                 "mask": mask[opening + 1:end]}
        blocks.append(block)
        ancestry.append(block)
    gpu_devices, cpu_devices = set(), set()
    for block in blocks:
        if block["kind"] != "Device":
            continue
        hid = _hardware_id(block)
        if hid == "ACPI0007":
            cpu_devices.add(block["path"])
        if hid in {"QCOM0C36", "QCOM043A"}:
            gpu_devices.add(block["path"])
    cpu, gpu, evidence = [], [], {}
    known_devices = {block["path"] for block in blocks if block["kind"] == "Device"}
    zone_counts = {}
    for block in blocks:
        if block["kind"] == "ThermalZone":
            zone_counts[block["path"]] = zone_counts.get(block["path"], 0) + 1
    for block in blocks:
        if block["kind"] != "ThermalZone":
            continue
        body, path = block["body"], block["path"]
        if zone_counts[path] != 1:
            continue
        hid = _hardware_id(block)
        association = _direct_field(block, r'\bName\s*\(\s*_TZD\s*,\s*Package\s*\([^)]*\)\s*{')
        targets = set()
        if association:
            opening = association.end() - 1
            absolute = block["start"] + 1 + opening
            content = body[opening + 1:closing[absolute] - block["start"] - 1]
            for reference in content.split(','):
                reference = reference.strip()
                if not reference:
                    continue
                if not re.fullmatch(r'(?:\\|\^*)(?:[A-Za-z_][A-Za-z0-9_]{0,3})(?:\.[A-Za-z_][A-Za-z0-9_]{0,3})*', reference):
                    targets.add(None)
                else:
                    targets.add(_resolve_reference(reference, path, known_devices))
        # On SDM8380, these are the two on-die CPU/SoC TSENS banks, not
        # motherboard ADC thermistors. Report them as SoC, never per-core.
        if table_id.strip() == "SDM8380" and hid in {"QCOM0C58", "QCOM0C59"}:
            cpu.append(path)
            evidence[path] = f"SDM8380 SoC TSENS {hid}"
        elif table_id.strip() != "SDM8380" and targets and targets <= cpu_devices:
            cpu.append(path)
            evidence[path] = "ACPI _TZD processor association"
        if targets and targets <= gpu_devices and hid == "QCOM0C91":
            gpu.append(path)
            evidence[path] = "ACPI _TZD graphics association; QCOM0C91"
    return {"cpu_zones": sorted(set(cpu)), "gpu_zones": sorted(set(gpu)),
            "cpu_label": "SoC" if table_id.strip() == "SDM8380" else "Thermal zone",
            "gpu_label": "Thermal zone", "evidence": evidence}


def _valid_profile(profile, digest):
    if not isinstance(profile, dict) or profile.get("dsdt_sha256") != digest or profile.get("schema_version") != PROFILE_VERSION:
        return False
    for key in ("cpu_zones", "gpu_zones"):
        names = profile.get(key)
        if (not isinstance(names, list) or len(names) > 128
                or any(not isinstance(name, str) or len(name) > 256
                       or not re.fullmatch(r'\\[A-Z0-9_.]+', name) for name in names)):
            return False
    names = set(profile["cpu_zones"] + profile["gpu_zones"])
    evidence = profile.get("evidence")
    return (profile.get("cpu_label") in ("SoC", "Thermal zone")
            and profile.get("gpu_label") == "Thermal zone"
            and isinstance(evidence, dict) and set(evidence) == names
            and all(isinstance(value, str) and 0 < len(value) <= 256 for value in evidence.values()))


def _read_limited_text(path, limit, *, errors="strict"):
    with path.open(encoding="utf-8", errors=errors) as stream:
        text = stream.read(limit + 1)
    if len(text) > limit:
        raise ValueError("Oversized thermal-discovery metadata")
    return text


def _store_profile(cache, profile):
    temporary = None
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=cache.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(profile, stream, indent=2)
        os.replace(temporary, cache)
    except OSError as exc:
        # Advisory persistence must not discard successfully discovered sensors.
        log.warning("Could not cache thermal discovery: %s", exc)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                log.debug("Could not remove temporary thermal metadata", exc_info=True)


def discover_thermal_profile():
    table = read_dsdt()
    digest = hashlib.sha256(table).hexdigest()
    cache = data_directory() / "thermal-profile.json"
    try:
        cached = json.loads(_read_limited_text(cache, 1024 * 1024))
        if _valid_profile(cached, digest):
            return cached
    except (OSError, ValueError, TypeError):
        pass
    tool = prepare_disassembler(download=False)
    with tempfile.TemporaryDirectory(prefix="HeatMap-ACPI-") as directory:
        source = Path(directory) / "dsdt.aml"
        source.write_bytes(table)
        subprocess.run([str(tool), "-d", str(source)], cwd=directory, timeout=12,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW, check=True)
        text = _read_limited_text(source.with_suffix(".dsl"), 12 * 1024 * 1024, errors="replace")
    profile = parse_thermal_profile(text, table[16:24].decode("ascii", errors="replace"))
    profile.update(dsdt_sha256=digest, schema_version=PROFILE_VERSION)
    # This is unprivileged display metadata, not authorization for fan control.
    _store_profile(cache, profile)
    return profile


def component_temperatures(zones, profile):
    result = {}
    for component, field in (("cpu", "cpu_temp"), ("gpu", "gpu_temp")):
        names = set(profile.get(component + "_zones", []))
        matches = [zone for zone in zones if canonical_path(zone["name"]) in names]
        if matches:
            hottest = max(matches, key=lambda zone: zone["temp"])
            result[field] = hottest["temp"]
            result[component + "_temp_sensor"] = hottest["name"]
            result[component + "_temp_label"] = profile.get(component + "_label", "Thermal zone")
            result[component + "_temp_source"] = profile.get("evidence", {}).get(canonical_path(hottest["name"]), "ACPI")
    return result
