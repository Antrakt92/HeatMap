# HeatMap setup

[Overview](../README.md) · [Sensor and cooling reference](REFERENCE.md)

### Windows ARM64 / Snapdragon

Install native ARM64 Python 3.10+ (3.13 is tested), then use the same checkout:

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt -c constraints-known-good.txt
.venv\Scripts\python setup.py --preflight
run_as_admin.bat
```

The launcher detects ARM Windows, including when Python runs under x64
emulation, and starts a Windows monitoring backend without UAC. Prefer native
ARM64 Python: dependency markers exclude pythonnet there. The backend checks
only psutil and Tk; it does not load the x64 DLLs, open hardware drivers or start
fan controllers. CPU load/frequency, GPU engine utilization, ACPI thermal-zone
temperatures, RAM and fixed-volume capacity remain live when Windows exposes
the relevant counters. At startup HeatMap automatically reads the local firmware
copy and discovers thermal associations. On SDM8380 it uses the supported
SoC TSENS banks for the CPU/SoC row, and an explicit ACPI graphics association
for the GPU thermal-zone row. These are labeled SoC/thermal zone rather than
per-core or junction sensors. Names such as TZ0/TZ6 are discovered from firmware,
not assigned by sorting readings or selecting an anonymous hottest sensor.
Other hardware uses supported metadata; unidentified zones remain separate.
The hottest zone/range and individual zones are kept in Display -> Details and
diagnostics. The normal screen shows the identified component temperatures,
with a plain Temperature label for GPU. Inactive/sentinel
and nonfinite values are discarded. GPU load sums processes per engine, then
reports the busiest engine rather than adding parallel engines together.
Load is matched to the selected WDDM adapter LUID and physical adapter; ambiguous
multi-adapter counters are not presented as the selected GPU's load.
PDH uses language-neutral counter paths, runs on the sensor thread, clears
failed readings and closes the query at shutdown. No PowerShell process is
started for periodic monitoring. Missing counters do not block CPU/RAM readings.
Failed PDH queries retry after 30 seconds, and do not suppress the independent
WDDM clock provider.
Current GPU clock is read through Windows WDDM `D3DKMT_NODE_PERFDATA.Frequency`
(engine 0), not the maximum/specification clock. Software adapters are excluded;
each opened adapter handle is closed even after a failed query. On systems with
ambiguous hardware adapters the clock stays unavailable rather than being
associated with the wrong GPU. Driver reset/hotplug is rediscovered per sample.
Fan RPM and drive temperature may be unavailable; cooling
remains managed by the computer's firmware.
The normal display does not show a permanent backend label or unavailable-fan
notice. Backend identity and the RPM limitation remain in copied diagnostics;
current sensor failures, stale data and other warnings remain visible.
Volume reads retain the existing bounded per-volume subprocesses. A proven
single-extent DiskNumber links each volume to its current manufacturer/model;
model discovery does not depend on temperature support. When Windows's generic
temperature property is unavailable on a UFS device, a read-only protocol query
reads bDeviceCaseRoughTemperature (attribute 0x18). Replies must match the expected
protocol/type/attribute, offsets and length. The big-endian attribute uses a
Celsius offset of 80; zero/sentinel readings and thermal limits are never used
as current temperature. No UFS flags or attributes are written, and no CLR/SMART
fallback is loaded on ARM. All these queries remain in the bounded child.
The compact disk row retains model, temperature and fullness only. Temperature
and percent are reserved first; GiB totals are not displayed. The model uses
remaining width and an ellipsis, with full text retained in diagnostics. Numeric
columns determine the minimum width at larger display scales independently of
the length of a model name.
If identity/model queries fail or time out, capacity survives, names are not
guessed, and the existing retry/backoff applies. PawnIO installation and x64 runtime restoration
are rejected on ARM64; do not install those drivers on this machine.

The first ARM launch prepares ACPICA iASL 20260408 from the official
[ACPICA release](https://github.com/open-acpica/acpica/releases/tag/20260408).
The executable's exact size and SHA-256 are verified before use; it is cached
under `%LOCALAPPDATA%/HeatMap/tools`, not bundled or run as administrator.
iASL only disassembles a temporary copy of the DSDT; no ACPI methods are executed
and no firmware settings are written. The generated profile is local and keyed
by DSDT hash, so firmware changes trigger rediscovery. No firmware is uploaded.
Discovery resolves relative namespace references and rejects mixed or ambiguous
associations. Cache writes are optional: a read-only cache does not discard a
successfully discovered profile.
After preparation, discovery works offline. Download/discovery failures leave
unidentified zones visible and are included in diagnostics. They never authorize
fan control. Per-computer paths, zone names and disk models are not hardcoded.

Settings are local to each checkout. Copied x64 fan controls are disabled at
runtime on ARM64; saving display preferences preserves their original persisted
flags. CPU fan calibration cannot be edited from the ARM monitoring backend.
Keep hardware-specific commissioning files on their original machine.
`python launch.py` selects the native ARM route or the existing x64 launcher.
Only the explicit internal handoff code 20 permits the batch launcher to
continue into the x64 route; other native failures stop that launch.
`setup.py --verify` still checks the repository's x64 artifacts without loading
them. Its success does not verify live sensors; use the overlay and diagnostics.

### Windows x64

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
