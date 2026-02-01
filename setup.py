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
        "url": "https://www.nuget.org/api/v2/package/LibreHardwareMonitorLib/0.9.4",
        "dlls": [
            "lib/net4/LibreHardwareMonitorLib.dll",
        ],
        "sha256": {
            "LibreHardwareMonitorLib.dll": "a0f2728f1734c236a9d02d9e25a88bc4f8cb7bd1faff1770726beb7af06bf8dc",
        },
    },
    "HidSharp": {
        "url": "https://www.nuget.org/api/v2/package/HidSharp/2.1.0",
        "dlls": [
            "lib/net4/HidSharp.dll",
        ],
        "sha256": {
            "HidSharp.dll": "8c58e5fba22acc751032dfe97ce633e4f8a4c96089749bf316d55283b36649c2",
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


def download_and_extract():
    os.makedirs(LIB_DIR, exist_ok=True)

    for name, info in PACKAGES.items():
        print(f"Downloading {name}...")
        try:
            req = urllib.request.Request(info["url"], headers={"User-Agent": "Mozilla/5.0"})
            ssl_ctx = ssl.create_default_context()
            resp = urllib.request.urlopen(req, timeout=60, context=ssl_ctx)
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
            extracted = False
            for dll_path in info["dlls"]:
                # Try exact path first
                if dll_path in all_files:
                    dll_data = zf.read(dll_path)
                    out_name = os.path.basename(dll_path)
                    if not _verify_hash(dll_data, out_name, info.get("sha256", {})):
                        sys.exit(1)
                    out_path = os.path.join(LIB_DIR, out_name)
                    with open(out_path, "wb") as f:
                        f.write(dll_data)
                    print(f"  Extracted: {out_name}")
                    extracted = True
                else:
                    # Search for the DLL by name in any path
                    target = os.path.basename(dll_path).lower()
                    for entry in all_files:
                        if os.path.basename(entry).lower() == target:
                            dll_data = zf.read(entry)
                            out_name = os.path.basename(dll_path)
                            if not _verify_hash(dll_data, out_name, info.get("sha256", {})):
                                sys.exit(1)
                            out_path = os.path.join(LIB_DIR, out_name)
                            with open(out_path, "wb") as f:
                                f.write(dll_data)
                            print(f"  Extracted: {out_name} (from {entry})")
                            extracted = True
                            break

            if not extracted:
                print(f"  WARNING: Could not find DLLs for {name}")
                print(f"  Available files in package: {[f for f in all_files if f.endswith('.dll')]}")

    print("\nSetup complete! DLLs are in the 'lib' directory.")
    print("You can now run the overlay with: run_as_admin.bat")


if __name__ == "__main__":
    download_and_extract()
