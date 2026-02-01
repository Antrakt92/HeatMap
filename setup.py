"""
Setup script to download LibreHardwareMonitorLib from NuGet.
Run this once before using the overlay.
"""
import io
import os
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
    },
    "HidSharp": {
        "url": "https://www.nuget.org/api/v2/package/HidSharp/2.1.0",
        "dlls": [
            "lib/net4/HidSharp.dll",
        ],
    },
}


def download_and_extract():
    os.makedirs(LIB_DIR, exist_ok=True)

    for name, info in PACKAGES.items():
        print(f"Downloading {name}...")
        try:
            req = urllib.request.Request(info["url"], headers={"User-Agent": "Mozilla/5.0"})
            resp = urllib.request.urlopen(req)
            data = resp.read()
        except Exception as e:
            print(f"  ERROR downloading {name}: {e}")
            sys.exit(1)

        print(f"  Extracting DLLs...")
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            all_files = zf.namelist()
            extracted = False
            for dll_path in info["dlls"]:
                # Try exact path first
                if dll_path in all_files:
                    dll_data = zf.read(dll_path)
                    out_name = os.path.basename(dll_path)
                    out_path = os.path.join(LIB_DIR, out_name)
                    with open(out_path, "wb") as f:
                        f.write(dll_data)
                    print(f"  Extracted: {out_name}")
                    extracted = True
                else:
                    # Search for the DLL by name in any path
                    target = os.path.basename(dll_path).lower()
                    for entry in all_files:
                        if entry.lower().endswith(target):
                            dll_data = zf.read(entry)
                            out_path = os.path.join(LIB_DIR, target.replace(
                                target[0], os.path.basename(dll_path)[0], 1
                            ))
                            out_path = os.path.join(LIB_DIR, os.path.basename(dll_path))
                            with open(out_path, "wb") as f:
                                f.write(dll_data)
                            print(f"  Extracted: {os.path.basename(dll_path)} (from {entry})")
                            extracted = True
                            break

            if not extracted:
                print(f"  WARNING: Could not find DLLs for {name}")
                print(f"  Available files in package: {[f for f in all_files if f.endswith('.dll')]}")

    print("\nSetup complete! DLLs are in the 'lib' directory.")
    print("You can now run the overlay with: run_as_admin.bat")


if __name__ == "__main__":
    download_and_extract()
