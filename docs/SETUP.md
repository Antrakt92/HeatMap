# HeatMap setup

[Overview](../README.md) · [Sensor and cooling reference](REFERENCE.md)

### Requirements

- 64-bit Python 3.10+
- 64-bit Windows
- Administrator privileges (required to read sensors)

Exact production versions are pinned in `constraints-known-good.txt`: Python 3.10 uses pythonnet 3.1.0 and pycparser 3.0, while Python 3.11 and later use pythonnet 3.2.1 and pycparser 3.12. Preflight verifies the installed versions and the bundled CLR bridge; it does not read hardware sensors. A separate CI lane checks newer allowed versions. CI exercises 3.10, 3.13, and 3.14; other 3.10+ versions are less covered.

### Steps

1. **Clone the repository:**
   ```powershell
   git clone https://github.com/Antrakt92/HeatMap.git
   cd HeatMap
   ```

2. **Create a virtual environment and install dependencies:**
   ```powershell
   python -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt -c constraints-known-good.txt
   ```

3. **Prepare the monitoring libraries:**
   ```powershell
   .venv\Scripts\python setup.py
   ```
   `setup.py` restores the complete Windows runtime from the exact NuGet assets
   pinned in `runtime-lock.json`. Before replacing `lib/`, it verifies each
   package's SHA-256, each DLL's exact path/hash/size, and the entire staged
    runtime against `lib_manifest.json`; if any check fails, the previous working
    runtime is preserved. The explicit equivalent is `python setup.py --restore-runtime`.
    An interrupted replacement records the exact previous DLL set in a durable
    restore journal before moving it. On the next restore, setup verifies those
    bytes before recovering the previous set, including when the checked-out
    manifest already describes a newer version. An unverified backup is left in
    place for inspection and is never loaded automatically.
    Close any running HeatMap instance before restoring: Windows cannot atomically
    replace the CLR DLL directory while a process has its assemblies loaded.
    Preflight also rejects unrecognized extra DLLs in `lib/`: they block the
    elevated launch until the runtime is restored to the verified set.

   CPU temperature and motherboard fan sensors also require the PawnIO driver.
   Obtain a compatible installer through the verified process:
   ```powershell
   .venv\Scripts\python setup.py --download-pawnio
   ```
   Setup pins the official version, verifies size, SHA-256, and Authenticode
   publisher/certificate, and only then displays the installer path. Run that
   file as administrator and restart Windows. Setup never starts the driver
   installer automatically. If setup reports pending installer operations,
   restart Windows first, then run the PawnIO installer; another restart is
   required after installation before the hardware smoke check.

   After installation and restart, verify that LHM actually opens and reads a CPU sensor:
   ```powershell
   .venv\Scripts\python setup.py --hardware-smoke
   ```
   This command must run in an elevated terminal and succeeds only with a real,
   positive CPU temperature reading. It checks hardware-access conflicts and
   opens CPU sensors only; GPU, RAM/SPD, motherboard and storage discovery are disabled.

   To check an existing `lib/` without downloading:
   ```powershell
   .venv\Scripts\python setup.py --verify
   ```

4. **Start HeatMap:**
   ```powershell
   run_as_admin.bat
   ```
   The launcher checks candidates in this order: `.venv`, `venv`, then all
   `python.exe`/`pythonw.exe` pairs on `PATH`. It selects the first interpreter
   that fully passes CLR/LHM preflight. Preflight also checks installed
   distributions against the exact production versions in `constraints-known-good.txt`,
   so a newly installed empty Python at the start of `PATH` does not block a
   working environment, and stale packages are not accepted as supported.
   A degraded state with warnings only does not block UAC: the text is saved to
   `%LOCALAPPDATA%\HeatMap\last_preflight_warning.txt`, and driver status remains
   visible in the overlay.

   Or run manually as administrator:
   ```powershell
   .venv\Scripts\python overlay.py
   ```
