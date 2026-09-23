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
