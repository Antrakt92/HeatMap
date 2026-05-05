"""
Setup script to download LibreHardwareMonitorLib from NuGet.
Run this once before using the overlay.
"""
import hashlib
import io
import os
import ssl
import sys
import zipfile
import urllib.request

LIB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lib")

PACKAGES = {
    "LibreHardwareMonitorLib": {
        "url": "https://www.nuget.org/api/v2/package/LibreHardwareMonitorLib/0.9.5",
        "dlls": [
            "runtimes/win-x64/lib/net472/LibreHardwareMonitorLib.dll",
        ],
        "sha256": {
            "LibreHardwareMonitorLib.dll": "21673a431323cd350f31f7598d3e1a161bf9d0a4c030b76ef475441fbd30ac33",
        },
    },
    "HidSharp": {
        "url": "https://www.nuget.org/api/v2/package/HidSharp/2.6.4",
        "dlls": [
            "lib/net35/HidSharp.dll",
        ],
        "sha256": {
            "HidSharp.dll": "d86690efde30ea9179f669320f39148853793b743a98b531afeaf30598e22f54",
        },
    },
}


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
            print(f"  ERROR downloading {name}: {e}")
            sys.exit(1)

        print(f"  Extracting DLLs...")
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            print(f"  ERROR: Downloaded package for {name} is not a valid zip file")
            sys.exit(1)
        with zf:
            all_files = zf.namelist()
            for dll_path in info["dlls"]:
                if dll_path not in all_files:
                    print(f"  ERROR: Could not find exact DLL path for {name}: {dll_path}")
                    candidates = _dll_candidates(all_files, dll_path)
                    print(f"  Available matching DLLs: {candidates}")
                    sys.exit(1)

                dll_data = zf.read(dll_path)
                out_name = os.path.basename(dll_path)
                if not _verify_hash(dll_data, out_name, info.get("sha256", {})):
                    sys.exit(1)
                out_path = os.path.join(LIB_DIR, out_name)
                try:
                    with open(out_path, "wb") as f:
                        f.write(dll_data)
                except OSError as e:
                    print(f"  ERROR writing {out_name}: {e}")
                    sys.exit(1)
                print(f"  Extracted: {out_name}")

    print("\nSetup complete! DLLs are in the 'lib' directory.")
    print("You can now run the overlay with: run_as_admin.bat")


if __name__ == "__main__":
    download_and_extract()
