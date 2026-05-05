"""
Setup script to download direct DLL dependencies and verify the bundled runtime.
Run this once before using the overlay.
"""
import argparse
import hashlib
import io
import json
import os
import posixpath
import re
import ssl
import sys
import zipfile
import urllib.request

APP_DIR = os.path.dirname(os.path.abspath(__file__))
LIB_DIR = os.path.join(APP_DIR, "lib")
MANIFEST_PATH = os.path.join(APP_DIR, "lib_manifest.json")
MANIFEST_VERSION = 1
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_DLL_RE = re.compile(r"^lib/[^/\\]+\.dll$")
_SOURCE_TYPES = {"nuget", "bundled-unknown"}


class SetupError(Exception):
    """Recoverable setup failure that should become a non-zero CLI exit code."""


PACKAGES = {
    "LibreHardwareMonitorLib": {
        "version": "0.9.5",
        "url": "https://www.nuget.org/api/v2/package/LibreHardwareMonitorLib/0.9.5",
        "dlls": [
            "runtimes/win-x64/lib/net472/LibreHardwareMonitorLib.dll",
        ],
        "sha256": {
            "LibreHardwareMonitorLib.dll": "21673a431323cd350f31f7598d3e1a161bf9d0a4c030b76ef475441fbd30ac33",
        },
    },
    "HidSharp": {
        "version": "2.6.4",
        "url": "https://www.nuget.org/api/v2/package/HidSharp/2.6.4",
        "dlls": [
            "lib/net35/HidSharp.dll",
        ],
        "sha256": {
            "HidSharp.dll": "d86690efde30ea9179f669320f39148853793b743a98b531afeaf30598e22f54",
        },
    },
}


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
            if source_type == "nuget":
                for field in ("package", "version", "url", "package_path"):
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


def _verify_hash(data, filename, expected_hashes):
    """Verify SHA256 hash of downloaded DLL data. Returns True if OK."""
    expected = expected_hashes.get(filename)
    if not expected:
        print(f"  WARNING: No expected hash for {filename}, rejecting")
        return False
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        print(f"  ERROR: {filename} hash mismatch!")
        print(f"    Expected: {expected}")
        print(f"    Got:      {actual}")
        return False
    print(f"  Hash OK: {filename}")
    return True


def _dll_candidates(all_files, dll_path):
    """Return DLL entries with the same filename for diagnostics."""
    target = os.path.basename(dll_path).lower()
    return [
        entry for entry in all_files
        if entry.lower().endswith(".dll") and os.path.basename(entry).lower() == target
    ]


def download_and_extract():
    os.makedirs(LIB_DIR, exist_ok=True)

    for name, info in PACKAGES.items():
        print(f"Downloading {name}...")
        try:
            req = urllib.request.Request(info["url"], headers={"User-Agent": "Mozilla/5.0"})
            ssl_ctx = ssl.create_default_context()
            with urllib.request.urlopen(req, timeout=60, context=ssl_ctx) as resp:
                data = resp.read()
        except Exception as e:
            raise SetupError(f"error downloading {name}: {e}") from e

        print(f"  Extracting DLLs...")
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise SetupError(f"downloaded package for {name} is not a valid zip file") from None
        with zf:
            all_files = zf.namelist()
            for dll_path in info["dlls"]:
                if dll_path not in all_files:
                    candidates = _dll_candidates(all_files, dll_path)
                    raise SetupError(
                        f"could not find exact DLL path for {name}: {dll_path}. "
                        f"Available matching DLLs: {candidates}"
                    )

                dll_data = zf.read(dll_path)
                out_name = os.path.basename(dll_path)
                if not _verify_hash(dll_data, out_name, info.get("sha256", {})):
                    raise SetupError(f"hash verification failed for {out_name}")
                out_path = os.path.join(LIB_DIR, out_name)
                try:
                    with open(out_path, "wb") as f:
                        f.write(dll_data)
                except OSError as e:
                    raise SetupError(f"error writing {out_name}: {e}") from e
                print(f"  Extracted: {out_name}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Download and verify HeatMap hardware monitor DLLs.")
    parser.add_argument("--verify", action="store_true", help="Only verify lib_manifest.json against lib/*.dll.")
    args = parser.parse_args(argv)

    if args.verify:
        ok, messages = verify_lib_manifest()
        _print_manifest_result(ok, messages)
        return 0 if ok else 1

    try:
        download_and_extract()
    except SetupError as e:
        print(f"Setup failed: {e}")
        return 1
    ok, messages = verify_lib_manifest()
    _print_manifest_result(ok, messages)
    if not ok:
        print(
            "\nDirect DLLs were restored, but the full bundled runtime does not match lib_manifest.json."
        )
        print(
            "Restore the tracked lib/ directory from git or reclone the repository until full runtime restore is implemented."
        )
        return 1

    print("\nSetup complete! DLLs are in the 'lib' directory.")
    print("You can now run the overlay with: run_as_admin.bat")
    return 0


if __name__ == "__main__":
    sys.exit(main())
