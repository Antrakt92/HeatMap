# HeatMap — Desktop Hardware Monitor Overlay

A lightweight Windows widget that displays PC temperatures, load, and component status wherever you choose on the desktop. In its normal mode, application windows cover it.

## What it shows

| Component | Metrics |
|-----------|---------|
| **CPU** | Temperature, frequency, load (%), RPM of both fans, and percentage when available |
| **GPU** | Core, Hotspot, and memory temperatures on separate rows, load (%), VRAM, and fans |
| **RAM** | Used/total GB and usage (%) |
| **Drives** | Physical drive temperatures; separate C:, D:, etc. rows show used/total GiB and volume fullness (%) |
| **Case** | RPM and available percentage for each SYS header, plus automatic control status |
| **Warnings** | Overheating, a large Hotspot–Core difference, a previously spinning fan stopping under load, and memory/disk usage |

CPU/GPU/RAM update every 2 seconds; storage sensors and local volume capacity update approximately every 30 seconds to avoid unnecessary I/O. Each local drive-letter volume has its own capacity row, such as `C: 396.0/400.0 GiB 99.0%`: the first number is used space, the second is total capacity. Fullness comes from Windows volume capacity, not the physical drive's LHM aggregate. Drive temperatures remain beside the device model; the app does not guess which device hosts a volume. GiB means 1,073,741,824 bytes.

Capacity rows, warnings, alerts, and usage peaks all use Windows volume readings. The warning panel also shows free GiB, using the same 80% warning and 90% critical thresholds. Volume read failures remain visible in the panel and Copy diagnostics. Expired readings are not displayed as current, and fresh volume rows remain available if hardware-temperature readings fail.

Temperatures and memory/disk usage are color-coded:

- **Green** — below the warning threshold
- **Yellow** — elevated; keep an eye on it
- **Red** — the action threshold has been reached; a sound also plays if Alerts are enabled

Red does not prove damage or represent a universal hardware safety limit. These are
application thresholds; exact hardware limits depend on the model. High CPU/GPU
load and fan speeds do not, by themselves, mean overheating. Load and frequencies
use neutral colors; fans that are spinning normally are green. Gray means a value
is unavailable. Additional sensors without model-specific thresholds and recorded
peaks use neutral colors: their color does not establish that a component is safe.

### Temperatures and thresholds

`Core` is the normal GPU Core/Edge sensor. `Hotspot` is the hottest point on the die
(junction), and `Memory temp` is the video memory temperature. The percentage next
to `VRAM` shows its usage. All three temperatures are visible without enabling
Details. If Core is unavailable, its row shows `--`; Hotspot or memory readings
are not substituted. Each sensor triggers alerts independently: a cool Core does
not hide a hot Hotspot. Details breaks down the temperatures and records separate
Hotspot and memory peaks until Reset peaks or a restart.

| Metric | Yellow at | Red / alert at |
|------------|-----------|-------------------|
| CPU Package/Tctl/Tdie | 70°C | 85°C |
| GPU Core/Edge | 80°C | 90°C |
| GPU Hotspot/Junction | 90°C | 105°C |
| GPU Memory | 85°C | 100°C |
| Samsung 980 PRO / 860 EVO | 55°C | 70°C |
| Other drives, conservative general threshold | 45°C | 55°C |
| RAM usage | 80% | 95% |
| VRAM usage | 90% | 98% |
| Disk usage | 80% | 90% |

Core/Hotspot/Memory thresholds are separate application warning settings, not
throttling limits read from the BIOS. A Hotspot of 108–110°C should not be treated
as a normal green state just because Core is substantially cooler. If it persists
under load, reduce the load and check the cooling; GPU clocks remain under driver
control. Optional case and GPU fan control is described below.

`Δ` next to Hotspot continuously shows the temperature difference. When Hotspot
is at least 80°C, a difference of 25°C or more is yellow and 35°C or more is red;
a text warning appears after 10 seconds of continuous exceedance. This is a
diagnostic heuristic, not proof of bad thermal paste: first compare the sensors
and check airflow.

`CPU → Fan 1 / Fan 2` and `GPU → Fans` show measured RPM. `75% ctl` means the
controller command read back from hardware, not a percentage of maximum RPM.
If the controller does not report a percentage in automatic mode, HeatMap neither
calculates one from an incidental observed maximum nor borrows it from another
header. `0 RPM` is a measured zero; `--` means no reading. A SYS row appears after
rotation is detected and remains if the fan later stops. `SYS 5/P` denotes a
pump-capable header; the program does not infer its physical location from its number.

If the CPU controller does not report duty, you can set the rated speed through
`Cooling → CPU fan % reference`. `~90%` then means RPM / the specified rating,
not PWM: 1800 out of 2000 RPM is approximately 90%. The estimate can exceed 100%.
The stock fans on the [Noctua NH-U12A](https://www.noctua.at/en/products/nh-u12a/specifications)
are rated at 2000 RPM, or 1700 RPM with the low-noise adapter. This setting changes
only the display. A value of 0 disables the estimate; an incidental observed
maximum is not used. An available actual control percentage takes priority.

CPU and SYS rows display the estimate as `~77%`, without a `ref` suffix.
For SYS, the control sensor on the same hardware channel takes priority; otherwise,
HeatMap uses the saved RPM for that fan, verified at full speed. An unknown
percentage appears as `--%`. `FW` next to SYS4 means motherboard control.
For CPU, GPU, and SYS, only the percentage changes color: orange from 80%, red
from 95%. The color indicates proximity to full speed, not overheating, and does
not independently trigger a sound. The window fits its width to the content with
a smaller minimum size; long warnings wrap within that width.

For SSDs, the main `Temperature`/Composite sensor takes priority over `Temperature 2`.
All additional temperatures are available in Details → Disk sensors. SMART
Warning/Critical values are not current readings and are not displayed as the
drive temperature. If there is no named primary sensor, the hottest available
reading is used.

VRAM shows used/total GB and a percentage in its main row without enabling Details.
RAM separately shows system memory capacity. If VRAM capacity is unavailable,
the available percentage is still shown; unknown values are not inferred from
the GPU model.

The warning panel is hidden and takes up no space when there are no messages.
Muted sound is indicated by `Alerts: OFF` in the menu, without a permanent footer.
Elevated RAM/VRAM usage highlights the row itself; a separate message appears
only from 95% RAM or 98% VRAM usage. Temperature, missing-sensor, and control-error
warnings remain visible regardless of the sound setting. The panel shows three
priority messages and a count of the rest. Clicking it copies diagnostics with
all warnings and the full controller error reason; long paths and tracebacks do
not stretch the window. Messages distinguish confirmed return to motherboard
control from unconfirmed restoration.
Fan-stop detection requires a previously positive RPM followed by 10 seconds of
zero RPM while the relevant component is hot: CPU at 70°C or above; GPU Hotspot
at 85°C, Core at 80°C, or memory at 85°C or above. Any of these heat sources counts
for a case fan. A cool GPU in normal Zero RPM mode is not classified as faulty
because the CPU is under load. An unused header reading zero from its first
sample is not considered a fault. This is tachometer-based detection, not a
guarantee that every physical fan on a splitter is spinning.

If the GPU exposes multiple tachometers, Fans shows them separately; a previously
working channel stopping while the GPU is hot makes Fans red. For a single
channel, `% ctl` is matched to its fan-control sensor; power limit is not used
as fan speed.

Loss of a previously available Hotspot/video memory temperature triggers a warning.
With automatic case fan control enabled, a missing required sensor is reported
immediately, even if it has never produced a reading.

### Automatic GPU fans

A separate **Cooling → Automatic GPU fans** toggle is available for the
**Gigabyte RX 7900 XT Gaming OC**. HeatMap reads Core, Hotspot, and memory
temperatures through the installed AMD ADLX and selects the highest speed
requested by the three curves. On a cool card, `AUTO ready · Driver curve` means
HeatMap is monitoring while the fans remain on the saved driver curve. Control
engages only in response to the GPU's own temperatures: **Core at 70°C, Hotspot
at 85°C, or Memory at 85°C or above**. CPU temperature, case fans, and their errors
do not determine GPU fan speed. Full speed starts at **Core 75°C, Hotspot 90°C,
or Memory 90°C**. While HeatMap intervenes, the minimum is 30%; increases take
effect at the next two-second poll, while decreases require 10 seconds of
cooling and are limited to two percentage points per second.
After 10 seconds of fresh readings with Core ≤60°C, Hotspot ≤75°C, and Memory
≤75°C, HeatMap verifies restoration of the original curve and Zero RPM. These
are application thresholds, not the card's rated limits. The original Zero RPM
setting is preserved: if it is disabled in Adrenalin, returning control to the
driver does not by itself stop the fans.

`GPU → Cooling → AUTO N%+ · Hotspot/Core/Memory` shows the required minimum and
the governing sensor. The driver may request more cooling through its saved
temperature protection; when intervening, HeatMap preserves the original curve's
temperature points and never lowers its speeds. Driver full speed is retained
at or before 90°C at its input, sometimes earlier because of the original points.
Fans still shows RPM and duty read from hardware. There is no 100% test during
normal startup. It runs only through an explicitly started `commission_gpu_fans.py`
procedure and lasts 15 seconds. OFF or a normal HeatMap exit restores the previous
curve and Zero RPM and verifies the result. CPU settings, voltages, clocks, and
power limits are unchanged. Do not adjust fans through Adrenalin or another
application at the same time: a curve mismatch stops the controller.

To test without enabling: `.venv\Scripts\python.exe tools\commission_gpu_fans.py`.
To test, save ON, and restart HeatMap normally, run the same command with `--enable`
from an administrator terminal. The script does not request elevation itself.
No libraries or drivers are installed. If the worker is forcibly terminated,
the driver retains the last HeatMap curve, reaching 100% at 90°C at the driver's
own temperature input. On its next startup, the GPU controller checks the journal
of original settings and restores them if the GPU and current curve match a known
HeatMap state. External changes are preserved and reported as a conflict. Normal
shutdown removes the journal only after verified restoration.
After a conflict, close other fan-control applications and toggle
`Automatic GPU fans` OFF → ON: this explicitly accepts the current curve and
Zero RPM setting. HeatMap verifies the GPU and journal, reads the current settings
twice, and archives the conflicting journal without writing the old settings to
the driver. A damaged journal, a different GPU, or changing settings prevents
re-enabling. If the current settings match a known unfinished HeatMap command,
normal verified restoration runs first. Autostart does not accept conflicts
automatically. Details, hardware results, and limitations:
[GPU fan control](docs/gpu-fans-2026-09-06.md).

Before controlling the GPU, HeatMap waits up to 60 seconds for Core/Hotspot/Memory
and two updated samples, displaying `Waiting GPU`. This check precedes restoration
of an old journal and any new commands. Identity errors, conflicts, and invalid
readings are not bypassed by retries; normal cancellation during the wait stops
the worker without commands. Zero RPM on a cool card is not a reason to increase
speed. If data disappears before intervention, control stays with the driver;
after intervention, emergency cooling and verified restoration on failure remain
in place. Adrenalin curve changes made while waiting become the new baseline at
the next intervention. Repeated old readings neither lower the fan command nor
count toward the cooling hold time. GPU settings and cancellation are rechecked
before writing after a lengthy check; this also applies to old-session recovery.

### Automatic case cooling

This profile is intended only for the **Gigabyte B550 AORUS PRO AC** with working
**System Fan #1 and #2** tachometers on the independent IT8688E. By default,
**SYS4/SYS5/SYS6 remain under motherboard control**: normal SYS4 control in bundled
LHM disables the shared IT8792E automatic mode, affecting SYS5/SYS6. The Pump suffix
in the original sensor name denotes pump capability, not an installed pump.
The motherboard supports ordinary case fans on these headers. An isolated 100%
reading proves neither overheating nor a fixed BIOS setting. Even with air
cooling, the current public API cannot confirm that the shared EC has actually
returned to its original automatic mode; matching PWM does not prove EC restoration.
Exact sensor and controller identifiers are checked.
At startup, missing readings are checked once a second for up to 60 seconds.
If the supported motherboard's hardware-controller list is completely empty,
HeatMap repeats motherboard-only discovery every 10 seconds, at most five times
within the same deadline. This addresses LHM missing discovery because the hardware
bus was busy. CPU/GPU devices and drivers are not reopened. Already-discovered
controllers are not recreated during this wait. The window shows
`Waiting sensors...`; no fan commands are sent until the channels and
CPU/Core/Hotspot/Memory temperatures are ready. Retries do not bypass duplicates,
foreign identifiers, a stopped fan, or a missing control object. An error before
takeover explicitly reports that no commands were sent and retains the FW label;
unconfirmed restoration after takeover remains a separate error.
This case profile does not control CPU/GPU fans. A separate verifiable profile
for SYS4/SYS5/SYS6 is described below; it does not use the old LHM SetSoftware
path for them. With a different motherboard or wiring, HeatMap continues monitoring
but rejects this profile. Header numbers do not mean “front” or “rear” fan.

Mode shows the percentage and controlled channels, for example `AUTO 70% · SYS 1/2`.
SYS4 shows `FW` in this mode. **Cooling → Cooling status and policy** displays
the current reason for cooling, target percentage, and actual command, which
may exceed the target during a gradual decrease. The window updates without
interrupting sensor polling or the controller heartbeat.

#### Four case fans without BIOS changes

For air cooling with fans on **SYS1/SYS2/SYS4/SYS5** and an unused SYS6, you can
explicitly enable the shared profile. From an administrator terminal:

```powershell
.venv\Scripts\python.exe tools\commission_shared_fans.py --enable
```

The check closes HeatMap normally, backs up the configuration, and verifies full
speed on all four fans and return to motherboard control. Only after successful
restoration does it save `case_fans_shared_enabled=true` and start HeatMap.
Without `--enable`, it only tests and restores control. Results and the backup
are stored in `%LOCALAPPDATA%/HeatMap/shared-controller-2026-09-06/`.
This command does not change sound or autostart settings.

In this mode, Mode shows `AUTO …% · SYS 1/2/4/5`. All four fans follow the same
temperature curve. The unused SYS6 on the same controller is held at a 100%
command; detecting its tachometer stops this profile. CPU and GPU retain their
own control. To return control to the motherboard, turn off
**Cooling → Automatic case fans**.

The shared profile uses a separate signed `IsaBridgeEC.bin` from PawnIO.Modules
0.2.10, with the AMD decode mask fix, SHA-256 verification, and checks of the
original registers. The installed PawnIO driver and NuGet DLLs are not replaced.
The actual EC mode is read after takeover, during operation, and after restoration;
the original PWM/mode registers are saved and restored. A nonzero command is set
before switching to manual control. Details and verification boundaries:
[four-fan validation](docs/shared-fans-2026-09-06.md).

A nonzero RPM means rotation regardless of the percentage: 100% on SYS5 does not
mean the other fans are off. The command percentage and fraction of rated RPM
(`~`) are not measured airflow; they cannot alone be used to compare different
fan models.

Before opening sensors and at every poll, HeatMap checks for known CPU-Z,
Ryzen Master, HWiNFO, and third-party fan-controller processes. On detecting a
conflict, it suspends readings until HeatMap restarts, and its controller returns
control to the motherboard normally. Standard AMD Adrenalin is not blocked.
This reduces competing access but does not guarantee detection of every driver
or elimination of system freezes. Copy diagnostics uses a snapshot from the main
sensor thread, including its age, without opening another hardware monitor.

After installation, run **`enable_case_fans.bat`** once and accept UAC. Activation
closes the previous HeatMap instance from this checkout normally, checks RPM at
100%, verifies restoration of the previous control, saves the setting, enables
sound alerts and autostart, and starts the updated HeatMap. A failed check is not
treated as successful setup. The result is stored in
`%LOCALAPPDATA%/HeatMap/activation-result.json`. A configuration backup is saved
in the same folder before changes. You can disable `Automatic case fans` in the menu.

Each component has its own linearly interpolated curve; the **highest cooling
demand** is selected, not an average temperature:

| Sensor | Temperature → case fan command |
|--------|--------------------------------------------|
| CPU | 40°C → 60%; 60°C → 70%; 75°C → 90%; 80°C → 100% |
| GPU Core | 40°C → 60%; 60°C → 80%; 75°C → 100% |
| GPU Hotspot | 60°C → 60%; 80°C → 80%; 95°C → 100% |
| GPU Memory | 60°C → 60%; 80°C → 80%; 95°C → 100% |

A large Hotspot–Core difference while hot (35°C or more), a missing required
sensor, or a lost tachometer requires 100%. Increases take effect at the next poll
(normally 2 seconds); decreases require 15 seconds of cooling and proceed at
approximately 2 percentage points per second, with a 3-point deadband and a 60%
minimum. This is a conservative setting for this profile, not a universal formula
for all fans.

Anticipatory cooling boosts SYS1/SYS2 during a sustained temperature rise: a
10-second window, at least three samples spanning 6 seconds, a rise of at least
2°C, and at least two rising steps. A 5-second projection capped at +5°C is used
in the calculation; the increase over the original target is capped at 10
percentage points. Falling temperatures, a single spike, and steady temperatures
do not add a boost. This is a bounded response heuristic, not a prediction of the
actual future temperature. Base curves and emergency 100% retain priority.
Cooling status shows the reason and current command.

If a previously spinning SYS4/SYS5/SYS6 reports 0 RPM twice consecutively under
thermal load, SYS1/SYS2 receive a 100% target. An unavailable tachometer means
missing data, not a proven stop: a separate warning appears after 10 seconds.
This assistance does not replace a working fan or cooling repairs.

During first-time setup, when saved channels do not match, or when a check is
explicitly requested, the first 15 seconds verify full speed. The command must
be confirmed and RPM must rise by at least 8% over its baseline (unless the
initial command was already 95–100%). No response means the wiring/mode needs
checking; the program does not switch PWM/DC blindly. During normal startup
with saved verification for the exact current channels, the controller immediately
selects speed from fresh temperatures and shows AUTO after reading the command
back. Old reference RPM values are not presented as a new full-speed check.
SYS4/SYS5 immediately receive the required nonzero command; the brief protective
100% setting inside LHM when switching SYS1/SYS2 remains, without the former
15-second test.

Before the first command, the original SYS1/SYS2 output bits are checked through
the public IT8688E report; an unavailable report or unsupported mode prevents
takeover. On exit, both the previous commands and restoration of these bits are
verified. This works around the current LHM mode-restoration limitation without
changing DLLs or writing to the BIOS.

After successful verification, the measured full-speed RPM is saved for each
channel. During another explicit check, a fan that is already spun up also passes
if it reaches at least 90% of that verified value and the 100% command is read
back. This prevents a false failure after a quick restart; reference RPM does
not replace current readings and is not recalculated at every startup.
The final status of a stopped process retains the restoration result instead
of turning into a stale-data message over time.

Status exchange accounts for Windows file locking: reads allow snapshot replacement,
and temporary write conflicts are retried with a total delay of no more than
0.63 seconds. A brief read failure uses the last confirmed report while it still
passes the existing freshness check (10 seconds for a running process).
A persistent write error still triggers restoration of the original control.
If the full shutdown report cannot be saved, the worker tries one compact report
that retains the actual restoration result. If that also fails, restoration
remains unknown to the window. Storage failures do not qualify for automatic
heartbeat recovery; review disk space and diagnostics before re-enabling control.
WinError 5 fix analysis: [report](docs/fan-status-io-fix-2026-09-05.md).

Hardware validation results and limitations:
[report](docs/thermal-control-validation-2026-09-05.md).

Control runs in a separate process that checks the window heartbeat and owner
process. On normal exit, an error, or loss of heartbeat, it restores the original
control through LHM and verifies readback. Restoration errors are reported
explicitly. Forcibly killing the controller process itself, a native driver hang,
or a Windows hang prevents any guarantee of software restoration: restart Windows
after such a failure. Do not run another fan controller (FanControl, SIV, GCC,
EasyTune) at the same time.

After a heartbeat timeout, HeatMap allows one automatic restart attempt per
controller: the old process must have exited, restoration of control must be
confirmed without errors, and the window must have resumed polling for at least
two seconds. The timeout remains 15 seconds; a frozen window does not retain
fan control. A settings conflict, unconfirmed restoration, an unknown stop reason,
or another timeout requires reviewing diagnostics and manually toggling OFF → ON.
The automatic attempt does not accept external GPU settings. Copy diagnostics
retains the original reason and previous stop report even after successful recovery.

Autostart from a user-writable checkout retains the normal UAC prompt at Windows
login. After HeatMap starts, the profile regulates itself. Fully silent privileged
autostart requires a separate protected installation, which this release does
not create.

AMD also [distinguishes Current and Junction Temperature in Adrenalin](https://www.amd.com/en/resources/support-articles/faqs/DH3-038.html).
Sensor names were checked against the [source of the LibreHardwareMonitor version in use](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/blob/30395c48a7f912894a7c392db8c11e1b97859658/LibreHardwareMonitorLib/Hardware/Gpu/AmdGpu.cs).
For the [980 PRO](https://download.semiconductor.samsung.com/resources/data-sheet/Samsung-NVMe-SSD-980-PRO-Data-Sheet_Rev.2.1_230509_10129500052824.pdf)
and [860 EVO](https://download.semiconductor.samsung.com/resources/data-sheet/Samsung_SSD_860_EVO_Data_Sheet_Rev1.pdf),
Samsung specifies an operating range of 0–70°C. This exception does not apply
to all SSDs/HDDs; the previous conservative threshold for unknown models is retained.

The window is created before hardware sensors open and displays warmup status.
Sensor initialization and recovery run in a background thread, with at least
30 seconds between retries. Frequent screen-edge polling pauses when Raise on
edge is disabled or Always on top is enabled.

If sensors stop producing fresh data for more than 10 seconds, old values are
replaced with `--` and `Sensors: waiting for fresh data` appears. Stale data does
not repeat alerts; readings return automatically after a fresh sample. Persistent
errors from individual devices trigger recovery with the same retry delay.
After a drive update error, its cached readings remain hidden until a new sample.

Invalid sensor values are not displayed as real percentages or RPM. For CPU,
the highest valid temperature in the Package/Tctl/Tdie group is selected; if
those sensors are absent, the highest available CPU temperature is used.

## Features

- **A fixed place on the desktop** — the widget stays in place beneath application windows
- **Dragging** — hold the title bar and drag it wherever you want
- **Alerts** — sound at the red thresholds in the table above; repeats no more than once a minute, and stale data does not trigger alerts
- **Autostart** — a least-privilege task starts HeatMap after login with no configured delay and shows the normal UAC prompt before accessing sensors. This does not promise to start before other applications: startup order and UAC timing depend on Windows and the user.
- **Always on top** — toggle through the right-click menu
- **Position memory** — remembers its location between sessions

`Raise on edge` temporarily raises the widget above windows when you hover over
the right edge of the work area. Its position and size stay unchanged: hovering
changes only window order, with no slide-out movement or animation. When the
pointer leaves both the edge and the widget, the widget returns beneath applications
and stays visible on uncovered desktop space. An open menu, the CPU reference
dialog, and dragging keep it temporarily raised.

The hover zone excludes the taskbar, the Show Desktop button, and the internal
boundary between two monitors. With a right-side taskbar, use the edge immediately
to its left. Hovering over another monitor's edge raises the widget in its existing
location; it does not move it to the pointer. `Always on top` separately enables
permanent placement above applications.

The window remains on the desktop continuously; covering it with an application
does not hide or recreate it. HeatMap does not attach its window to Explorer.
Placement beneath desktop icons is not guaranteed, so choose an area without
icons. Show Desktop/Win+D should restore the normal widget without stealing focus;
its layer check also runs with Raise on edge disabled. Windows Peek should not
make the widget transparent.

Drag the title bar to change position. On release, the window is constrained to
the monitor's work area. When resized or when a monitor disconnects, its position
is adjusted only as much as necessary to keep the widget and its controls accessible.

## Screenshot

![HeatMap overlay](https://github.com/user-attachments/assets/8b58ff7c-3003-424c-b0aa-58bbc2b2c027)

## Installation

### Versions

Public releases use ordinary version numbers: `1.2.1`, `1.2.2`, and onward.
Fixes increment the last number; new features increment the middle number (`1.3.0`).
A release request means a normal public release, without a candidate stage,
`rc`/`beta` suffixes, or GitHub prerelease status. Unperformed manual or hardware
checks do not delay publication or require separate approval. Known limitations
and unperformed hardware checks are listed in release notes and `AUDIT.md`.
Old `rc` versions remain in GitHub history. One `HeatMap` checkout on `main`
is sufficient for local work; historical source and release archives are
available on GitHub.

### Requirements

- 64-bit Python 3.10+
- 64-bit Windows
- Administrator privileges (required to read sensors)

Verified environment: Python 3.13, pythonnet 3.1.0, psutil 7.2.2. Exact production versions are pinned in `constraints-known-good.txt`; a separate CI lane checks newer allowed versions.

### Steps

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Antrakt92/HeatMap.git
   cd HeatMap
   ```

2. **Create a virtual environment and install dependencies:**
   ```bash
   python -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt -c constraints-known-good.txt
   ```

3. **Prepare the monitoring libraries:**
   ```bash
   .venv\Scripts\python setup.py
   ```
   `setup.py` restores the complete Windows runtime from the exact NuGet assets
   pinned in `runtime-lock.json`. Before replacing `lib/`, it verifies each
   package's SHA-256, each DLL's exact path/hash/size, and the entire staged
   runtime against `lib_manifest.json`; if any check fails, the previous working
   runtime is preserved. The explicit equivalent is `python setup.py --restore-runtime`.
   Close any running HeatMap instance before restoring: Windows cannot atomically
   replace the CLR DLL directory while a process has its assemblies loaded.

   CPU temperature and motherboard fan sensors also require the PawnIO driver.
   Obtain a compatible installer through the verified process:
   ```bash
   .venv\Scripts\python setup.py --download-pawnio
   ```
   Setup pins the official version, verifies size, SHA-256, and Authenticode
   publisher/certificate, and only then displays the installer path. Run that
   file as administrator and restart Windows. Setup never starts the driver
   installer automatically. If setup reports pending installer operations,
   restart Windows first, then run the PawnIO installer; another restart is
   required after installation before the hardware smoke check.

   After installation and restart, verify that LHM actually opens and reads a CPU sensor:
   ```bash
   .venv\Scripts\python setup.py --hardware-smoke
   ```
   This command must run in an elevated terminal and succeeds only with a real,
   positive CPU temperature reading.

   To check an existing `lib/` without downloading:
   ```bash
   .venv\Scripts\python setup.py --verify
   ```

4. **Start HeatMap:**
   ```bash
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
   ```bash
   .venv\Scripts\python overlay.py
   ```

## Controls

Main readings are grouped into CPU, GPU, CASE COOLING, and MEMORY & STORAGE.
The ⚙ title-bar button and right-click open the same menu with four sections.
The widget stays temporarily raised while the menu or CPU reference dialog is open.

| Action | How |
|----------|-----|
| Move | Hold and drag the title bar |
| Settings | ⚙ or right-click the widget |
| Always on top | Display → Always on top |
| Autostart | Display → Autostart |
| Sound on/off | Alerts & limits → Alerts |
| Automatic case fans | `enable_case_fans.bat`; then Cooling → Automatic case fans |
| Automatic GPU fans | Cooling → Automatic GPU fans |
| CPU fan percentage | Cooling → CPU fan % reference |
| Raise above windows when hovering at the edge | Display → Raise on edge |
| Extended metrics | Display → Details |
| Labels and thresholds explained | Alerts & limits → Colors, thresholds and sensor guide |
| Sensor diagnostics | Diagnostics → Copy diagnostics |
| Prepare PawnIO repair | Diagnostics → Prepare verified PawnIO repair |
| Quick PawnIO repair | Click the red "Driver: install PawnIO" row |
| Log | Diagnostics → Open log file / Copy log path |
| Reset peaks | Alerts & limits → Reset recorded peaks |
| Close | ✕ button or Close HeatMap |

`Copy diagnostics` gathers information in the background. While
`Collecting diagnostics...` is displayed, the window remains responsive; the
result is copied to the clipboard when complete. Clicking again does not start
another collection.

If metrics do not fit within the monitor's work area, mouse-wheel scrolling
and a dark scrollbar appear. The title bar with its close button and the warning
panel remain fixed. Details can be collapsed again through the menu. Sensor and
layout checks are described in the [1.1.0 audit](docs/audit-1.1.0.md).

Details recalculates the window size immediately, including while temporarily
raised. Long details wrap to the available width; an error does not leave the
bottom panel offscreen. On exit, the window hides before waiting for background
sensor cleanup (up to 5 seconds). Previous shutdown checks are described in the
[lifecycle audit](docs/lifecycle-audit-2026-09-05.md); open checks for persistent
desktop placement are listed in [AUDIT.md](AUDIT.md).

Startup, sensor, and error-handling check results are described in the
[follow-up audit report](docs/audit-followup-2026-09-03.md).
The history of the former slide-out Peek behavior and sensor recovery is described
in the [September 5 audit](docs/audit-2026-09-05.md).

### Autostart security

The source checkout and its virtualenv are writable by a regular user, so Task
Scheduler must not execute them with `HighestAvailable` without fresh consent.
HeatMap creates a `LeastPrivilege` task that runs the normal launcher; elevation
happens only through a visible UAC prompt. Old unsafe tasks are migrated through
a disable/verification/delete/re-create sequence. XML is passed to Task Scheduler
in memory, without privileged reading of a file from the user's `%TEMP%`.
Autostart must be configured by the same Windows account that opened the desktop
session; over-the-shoulder UAC using different administrator credentials is rejected.
A checkout path containing `%` is unsupported for the Task Scheduler action
because `cmd.exe` performs environment expansion. Silent autostart without UAC
requires a separate protected installation in an admin-owned location; checking
the checkout's ACLs/hashes does not simulate such an installation. Automatic task
restart is intentionally disabled: a retry policy for an interactive UAC launcher
would create repeated consent prompts.

## Technologies

- **Python** + **tkinter** — user interface
- **LibreHardwareMonitor** — sensor readings through .NET interop (pythonnet)
- **psutil** — additional system metrics
- **Windows API (ctypes)** — window placement beneath applications and raising without stealing focus

## Why are administrator privileges required?

LibreHardwareMonitor needs direct hardware sensor access to read temperatures, voltages, and fan speeds. These readings are unavailable without administrator privileges.

## License

HeatMap code is distributed under the MIT license. Bundled runtime components,
versions, and licenses are listed in `runtime-lock.json` and `THIRD_PARTY_NOTICES.md`.
