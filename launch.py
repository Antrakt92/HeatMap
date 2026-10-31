"""Select native Windows monitoring on ARM without requesting elevation."""
from pathlib import Path
import os
import subprocess
import sys

from windows_native import check_windows_dependencies, windows_only_mode
from native_discovery import prepare_disassembler


def main(*, native_only=False):
    if not windows_only_mode():
        if native_only:
            return 20  # The batch launcher continues its verified x64 path.
        app_dir = Path(__file__).resolve().parent
        command = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "cmd.exe")
        return subprocess.run(
            f'"{command}" /d /s /c ""{app_dir / "run_as_admin.bat"}""',
            cwd=app_dir, creationflags=subprocess.CREATE_NO_WINDOW, check=False,
        ).returncode
    ok, messages = check_windows_dependencies()
    if not ok:
        print("\n".join(messages))
        return 1
    try:
        prepare_disassembler()
    except Exception as exc:
        print(f"WARNING: automatic thermal identification is unavailable: {exc}")
    app_dir = Path(__file__).resolve().parent
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    subprocess.Popen(
        [str(pythonw), str(app_dir / "overlay.py")], cwd=app_dir,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
        close_fds=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(native_only="--native-only" in sys.argv[1:]))
