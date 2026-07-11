"""Synchronize manifest provenance fields from the reviewed runtime lock."""
import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "runtime-lock.json"
MANIFEST_PATH = ROOT / "lib_manifest.json"


def synchronized_manifest():
    lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    locked = {entry["file"]: entry for entry in lock["files"]}
    current = {entry["file"]: entry for entry in manifest["files"]}
    if set(locked) != set(current):
        missing = sorted(set(current) - set(locked))
        extra = sorted(set(locked) - set(current))
        raise SystemExit(f"runtime lock mismatch: missing={missing}, extra={extra}")
    for file_path, manifest_entry in current.items():
        lock_entry = locked[file_path]
        manifest_entry["source"] = {
            "type": "runtime-lock",
            "lock_file": "runtime-lock.json",
            "package": lock_entry["package"],
            "version": lock_entry["version"],
            "package_path": lock_entry["package_path"],
        }
        manifest_entry["notes"] = (
            f"Exact NuGet asset locked with package hash and {lock_entry['license']} license metadata."
        )
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    expected = json.dumps(synchronized_manifest(), indent=2) + "\n"
    current = MANIFEST_PATH.read_text(encoding="utf-8")
    if args.check:
        if current != expected:
            raise SystemExit("lib_manifest.json is not synchronized with runtime-lock.json")
        return 0
    MANIFEST_PATH.write_text(expected, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
