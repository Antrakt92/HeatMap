# B850 monitoring repair

## Confirmed causes

- The local GCC fallback disabled LHM entirely, leaving only Windows CPU load,
  memory usage, and filesystem capacity. This was a regression, not missing
  hardware support.
- B850 AORUS ELITE WIFI7 ICE exposes IT8696E tach channels through the bundled
  LHM 0.9.5 as `Fan #1` and `Fan #5`. HeatMap correctly refused to guess which
  generic fan was a CPU cooler, so both CPU rows stayed empty.
- The installed GCC `Fan/Profile-0.xml` identifies channels in this order:
  CPU, System 1, System 2, System 3, CPU OPT, System 4 Pump. A six-sample elevated
  read-only probe confirmed nonzero tach readings at `/lpc/it8696e/0/fan/0`
  and `/lpc/it8696e/0/fan/4`, with the remaining channels reporting zero.
- Before the AMD APU driver installation, a sensorless secondary display adapter
  caused unnecessary LHM reopening despite valid RX 7900 XT readings.

## Final behavior

GCC alone permits read-only CPU, GPU, motherboard and drive monitoring. Automatic
fan workers remain stopped. The extra DDR5 SPD/SMBus inventory is disabled in
this mode; Windows still supplies RAM usage. Other existing hardware conflicts
remain guarded.

Only the exact B850 board name, IT8696E identifier, and generic channel label
receive the CPU/CPU Optional display names. Unknown boards/chips retain their
original labels. This mapping does not create a control profile or send PWM
commands. The previous B550 case-control profile stays disabled and backed up.

## Earlier crash

Windows Application Error 1000 recorded a `pythonw.exe` / `clr.dll` access
violation at 20:35:44. `C:\Windows\INF\setupapi.dev.log` records the AMD APU
display-driver installation from 20:35:30 to 20:35:46 and graphics-device
restarts at 20:35:44. The previous claim that this proved GCC incompatibility
was unsupported. The coincidence supports driver replacement as a candidate
trigger; there is no native stack analysis proving the exact fault.
A second crash at 21:00:05 coincided with another AMD display-driver replacement;
the device installation log identifies Adrenalin installation at that time, followed
by another integrated-GPU package installation ending at 21:01:31. The live smoke
was restarted after the driver tools finished. AtiSetup and live PnP driver-install
commands now enter the normal hardware-access pause; detection cannot cover a
driver disappearing inside an already running native call.

## Verification

- `python -m unittest discover -s tests`: 774 passed.
- `python -m compileall -q overlay.py setup.py tests`: passed.
- `python setup.py --verify`: DLL and shared module integrity passed.
- `git diff --check`: passed.
- Regression coverage includes exact board/chip mapping, reversed sensor order,
  keeping measured duty unknown, GCC startup/arrival, fan handback, and preventing
  automatic controller recovery during GCC coexistence.

The attended application launch records its own sensor-thread values and actual
Tk row text in `%LOCALAPPDATA%\HeatMap\b850-live-sensors.json` and
`b850-live-ui.json` for ten minutes. This temporary launch wrapper opens no
additional hardware monitor and makes no fan-control calls.

Final live check after driver installation: 157 successive samples over 317 seconds,
one LHM initialization, no reinitialization hints, no sensor fallback, and no new
Application Error 1000. The same running Tk window showed both CPU fan RPM rows.
CPU fan ranges were 1366-1844 and 1403-1912 RPM; CPU temperature was 49-69 C and
GPU Core 46-59 C. GCC remained running throughout. Both HeatMap fan-worker PIDs
were absent, and `case_fans_enabled` remained false. This establishes the repaired
live behavior over that interval, not long-term stability or driver-replacement
recovery. The frozen final sample is `b850-verified-sensors.json` beside the live
reports.

## Ryzen Master follow-up

Opening Ryzen Master still triggered the older all-sensor pause. Monitoring scope
now accepts its four known executable names, alone or alongside GCC. Control scope
continues to reject both tools, and driver installers or additional conflicting
tools still block monitoring. The coexistence latch and UI now use generic names.

Verification: 777 unittest tests passed; compileall, setup.py --verify and
whitespace checks passed. Regressions exercise Ryzen Master at startup and arriving
during monitoring, combined GCC/Ryzen Master detection, and control exclusion.
After an attended elevated restart, both actual programs remained open while the
same HeatMap window produced 17 successive sensor samples over 33 seconds with
one LHM initialization. CPU/GPU temperatures, both CPU tachometers, RAM and drives
were present; both HeatMap fan workers remained absent. This is a short concurrent
monitoring check, not proof of extended stability or tuning/driver replacement.

## GPU load source correction

The same RX 7900 XT snapshot reported AMD GPU Core activity of 35% and Windows
D3D 3D utilization of 5.44%. HeatMap preferred the former. This was a source-policy
mismatch, not a demonstrated hidden process consuming the difference. The main
percentage now prefers the busiest valid Windows D3D engine, including compute,
copy and video; driver activity is a fallback only when D3D values are unavailable.
Engine selection compares value before name, so alphabetical order cannot choose
a less busy engine. Memory utilization is excluded and engine loads are not summed.

Sources: LibreHardwareMonitor v0.9.5 AmdGpu.cs (AMD activity and D3D node counters):
https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/blob/v0.9.5/LibreHardwareMonitorLib/Hardware/Gpu/AmdGpu.cs
Microsoft's busiest-engine aggregation rule:
https://devblogs.microsoft.com/directx/gpus-in-the-task-manager/

All 782 unit tests passed, including five focused load-policy regressions;
compileall, setup.py --verify and git diff --check passed. After elevated restart,
eight successive comparisons on the RX 7900 XT showed the actual Tk label at 5%,
LHM D3D 3D at 5.14-5.38%, and independently sampled Windows 3D counters at
5.06-5.41%. AMD activity stayed at 34-35%. Sampling windows were adjacent and
partially overlapping, not synchronized exactly. Local comparison evidence:
%LOCALAPPDATA%/HeatMap/gpu-load-comparison.json. Other engine selection and
invalid/missing readings were tested with fake sensors; no gaming benchmark ran.
