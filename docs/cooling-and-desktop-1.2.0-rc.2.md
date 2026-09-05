# HeatMap 1.2.0-rc.2: stationary desktop and independent cooling

## Problem and behavior

The reported Pump5/Pump6 control readings were 82%/81%. The previous worker
required 99–100% on both before selecting any case fan, so it refused SYS1/SYS2
even though those headers belong to a different controller.

The worker now selects the approved SYS1/SYS2 pair on IT8688E when the shared
IT8792E guard cannot pass. SYS4 stays in firmware mode. Exact sensor and owning
chip identifiers are required before any command. Selection is fixed until
restart; a session containing SYS4 still checks the pump guard every cycle.
No speed commands are sent to CPU/GPU or pump headers.

The installed LHM 0.9.5 source at commit
`30395c48a7f912894a7c392db8c11e1b97859658` supports this separation:

- [LpcIO controller assignment](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/blob/30395c48a7f912894a7c392db8c11e1b97859658/LibreHardwareMonitorLib/Hardware/Motherboard/Lpc/LpcIO.cs)
- [Board channel mapping](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/blob/30395c48a7f912894a7c392db8c11e1b97859658/LibreHardwareMonitorLib/Hardware/Motherboard/SuperIOHardware.cs)
- [Native control and restoration](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/blob/30395c48a7f912894a7c392db8c11e1b97859658/LibreHardwareMonitorLib/Hardware/Motherboard/Lpc/IT87XX.cs)

Full-speed command feedback, measured RPM response, heartbeat ownership,
readback checks and normal firmware restoration remain mandatory for the
selected set. RPM calibration accepts exactly SYS1/SYS2 or SYS1/SYS2/SYS4.
Missing reference data never bypasses acceleration verification.

The desktop window remains visible and stationary. Edge hover changes only
z-order; leaving lowers it without relocation, hiding or desktop reparenting.
The live Cooling status and policy window explains the selected channels,
requested airflow, actual command and limiting source without blocking Tk or
the controller heartbeat.

## Formula review

The existing piecewise linear curves, maximum across component demands,
rounding upward, 60% minimum and immediate increase on the next sample are
retained. Missing required temperatures and a large hot GPU temperature gap
still request full airflow. These are conservative application settings for
this board, not universally optimal fan curves.

Slowdown previously subtracted at least one point per call, even with zero
elapsed time, and could count time spent inside the 15-second delay. The ramp
now accumulates fractional time only after the delay: repeated timestamps do
not reduce speed, short intervals share the 2-point/second budget, and a long
pause grants at most five seconds of reduction. Emergency increases reset the
delay and unused fractional budget. Malformed demands request 100%.

Each curve's knots and midpoints are checked independently, including upward
rounding. The reason identifies the limiting component. The persistent GPU
temperature-gap timer starts again when the GPU identity changes.

## Verification on 2026-09-06

- 402 tests passed with the project virtualenv; compilation, DLL verification,
  runtime manifest consistency, preflight, installed dependencies, constraint
  ages, license provenance and native desktop-window checks passed.
- Elevated verification selected exactly SYS1/SYS2. Baseline RPM was 696/573;
  full-airflow commissioning measured 1223/1156 with 100% command feedback.
- Eleven active samples over approximately 20 seconds after commissioning
  remained active. The issued command decreased to 90% after the hold period,
  with temperature demand 69% from the CPU curve. Fan readbacks in each report
  are sampled before that iteration's next command, not synchronously after it.
- Normal stop reported `restore_confirmed=true`, no restore errors, and both
  original automatic control modes. The updated overlay then launched normally.

## Acceptance limits

Automated tests use independently constructed two-chip fixtures and disposable
Tk/Win32 windows. They cover channel selection, excluded-header writes,
failed reads/status publication, owner shutdown, calibration migration,
dialog lifecycle and stationary desktop transitions.

Real Explorer Show Desktop, physical multi-monitor/mixed-DPI, gaming load and
comparative acoustic/thermal performance remain separate manual acceptance.
No claim of improved measured cooling or quieter operation follows from the
formula review alone. Keep this release a prerelease until desktop acceptance
is complete. Normal shutdown returns selected channels to firmware; killing a
native worker cannot be treated as a verified restoration.
