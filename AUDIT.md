# HeatMap Audit Backlog

This file contains only confirmed open tasks. Closed findings are removed rather
than retained as session history. Priority reflects impact and likelihood, not
change size.

The manual and hardware checks below are informational follow-up tasks, not
publication conditions. A release request produces a normal `X.Y.Z` release,
without candidates or prereleases; open reboot/gaming/Explorer/multi-monitor checks
do not delay it. The publication rule is defined in `AGENTS.md` and supersedes
previous conditions in historical reports. Check statuses are retained as factual
records.

Full audit results, fixes, and the scope of release 1.2.0-rc.4:
`docs/audit-1.2.0-rc.4.md`.
Autostart and sensor-readiness waiting checks in 1.2.0-rc.5:
`docs/immediate-startup-2026-09-06.md`.
Verified fixes for sensors, controller reports, and commissioning:
`docs/audit-cleanup-2026-09-08.md`. This software audit does not close the hardware
checks listed below.

## P2 - Extended operation after a low-space failure

Low-space recovery follow-up: a controller reported `ENOSPC` while C: was nearly
full. Automated fault injection now covers full and compact terminal-report
failures, preserving verified restoration or explicitly leaving it unknown.
These tests do not simulate a physically full system volume. Extended operation
after the user frees disk space remains to be checked; do not fill C: deliberately
or treat successful report publication as evidence of hardware restoration.

## P2 - Physical reboot/login acceptance

The new audit removed the forced prolonged GPU test at 100% during normal startup
and repeated case-fan calibration when saved channels match. A cold GPU now stays
under driver control. Checks and manual scenarios:
`docs/startup-fan-audit-2026-09-06.md`. The next actual boot/login, noise, and GPU
transitions from standby to assistance and back remain untested.

After an actual reboot, the user confirmed failure of case-fan automation in rc.5:
SYS1 was absent from the controller's LHM instance for all 60 seconds, while the
main monitor displayed its RPM. rc.6 added bounded rediscovery of a completely
empty motherboard; testing the next physical reboot remains open. Cause, fix
scope, and checks: `docs/startup-rediscovery-1.2.0-rc.6.md`.

Check startup after a real Windows restart and user sign-in: the `HWMonitorOverlay`
task starts the launcher without a configured delay and requests UAC. After
approval, the window should appear without waiting for Task Scheduler; controllers
wait up to 60 seconds for temporarily unavailable readings without sending new fan
commands. Once sensors are ready, the case-fan controller checks the current
command; full-speed operation is tested only if suitable calibration is missing
or an explicit check is requested. On a cold card, the GPU shows readiness and
leaves control with the driver. Task Scheduler registration and classification,
the launcher, and an ordinary restart are checked separately; they do not replace
a physical reboot. The current installation from a mutable checkout does not
support silent boot before user sign-in.

## P1 - Cause of the complete Windows freeze remains unknown

After the forced reboot on 2026-09-06, the event log contains Kernel-Power 41 with
BugcheckCode 0. There is no newly confirmed crash dump. Ryzen Master installation
errors before the event and bthmodem.sys corruption require a separate Windows
investigation; coincident timing does not establish causation. Details and evidence
limitations: `docs/freeze-and-fan-ownership-2026-09-06.md`.

Independent HeatMap risks were fixed: SYS4 no longer takes over the shared EC based
on a one-off Pump 100% reading; known competing programs pause sensors; Copy
diagnostics does not open an additional monitor. Ryzen Master detection recognizes
`AMD Ryzen Master.exe` from the official MSI, including spaces; a separate
regression checks that hardware access is blocked. Extended stability testing is
needed after Windows repair. Automated tests do not prove that the kernel freeze
is resolved and do not replace this check.

## P2 - Extended RX 7900 XT fan-control testing

Check extended operation of both controllers after the watchdog fix: keep the menu
and CPU reference dialog open for more than 15 seconds, close them, and check for
fresh case/GPU statuses. After normal sleep/resume, check restoration of control
and the single automatic recovery attempt following a confirmed timeout. Copy
diagnostics must retain the original stop reason; OFF → ON must remain available
after a repeat failure. Do not suspend or kill hardware processes for this test.
Automated fake regressions check timeouts, restart boundaries, and diagnostic
retention, but do not replace sleep, sustained-load, and driver-hang checks.

Conflict diagnostics now retain expected and read-back settings. Manual OFF → ON
accepts an external curve/Zero RPM after checks and archives the conflict journal;
autostart keeps the lockout. Automated regressions cover this path. In the updated
elevated overlay, check re-enabling after a conflict, cold standby, and a normal
return after gaming load. A short background run of the new worker confirmed five
fresh standby reports without new commands. ADLX reinitialization was fixed: the
DLL is unloaded after Terminate; three consecutive read/close cycles passed on
this PC. New cancellation, external-change, and stale-measurement regressions:
`docs/reliability-1.2.0-rc.7.md`.

A separate ADLX GPU controller was added with independent Core/Hotspot/Memory
curves and full-speed cooling from Hotspot 90°C. A short hardware test confirmed
100% and 55% commands, RPM changes, and exact restoration of the original curve/Zero
RPM. Extended gaming, noise, and temperature comparison under identical loads are
still needed. Responses to synthetic temperatures are covered by tests; there is
no need to deliberately heat the card to these thresholds. Details:
`docs/gpu-fans-2026-09-06.md`. GPU fan percentage now uses the shared 80%/95% color
thresholds separately from RPM; speed color alone does not trigger a temperature
alert.

## P2 - Extended testing of the shared four-case-fan profile

The SYS1/SYS2/SYS4/SYS5 profile with unused SYS6 has now been tested on this PC:
SYS4 rose from 666 to 1188 RPM, restoration of the actual EC mode and original
registers passed, and all four channels follow the shared curve after restart.
The new signed module and hardware-test boundaries are described in
`docs/shared-fans-2026-09-06.md`. Historical limitations of the previous LHM path:
`docs/airflow-audit-2026-09-06.md`.

Extended stability, noise, physical airflow direction, and GPU Core/Hotspot
comparison at identical power and GPU fan speeds remain open. A short RPM check
does not prove that overheating is resolved, establish the original cause of the
Windows freeze, or demonstrate recovery from a kernel hang/forced controller
termination.

## P2 - Check GPU temperatures and new indicators after restart

Files: `overlay.py`, `tests/test_temperature_policy.py`; thresholds: `README.md`.

Background GPU reads through the current LHM without elevation produced three
consecutive RX 7900 XT samples: Core 54°C, Memory 74°C, Hotspot 108/109/110°C. This
confirms the reading's source, but is not an independent check of driver accuracy
or a cooling-system diagnosis. The 54–56°C Hotspot–Core difference requires checking.

The parser, separate rows and alert thresholds, unavailable/stale values, peaks,
and Samsung exceptions are covered by automated regressions. The updated elevated
overlay and automatic cooling were started and checked outside a game; hardware
results are recorded in `docs/thermal-control-validation-2026-09-05.md`. Comparison
with Adrenalin, sound checks under gaming load, and Explorer/DPI acceptance remain
open. A controlled comparison of gaming temperatures has not yet been performed.
A separate check of the controller's file-exchange fix is described in
`docs/fan-status-io-fix-2026-09-05.md`; it does not replace the temperature comparison.
`docs/audit-1.1.0.md` records additional sensor/warning regressions and hidden native
Tk scrolling/scaling checks; physical acceptance remains open. The main VRAM row
now includes GB and percentage; the empty footer collapses. Native Tk checks cover
the panel returning when a warning appears and retention of runtime errors; check
the updated compact layout in the gaming smoke test. Color, CPU percentage, and
grouped-layout audit: `docs/display-audit-2026-09-05.md`. Activity highlighting is
now neutral; RPM/reference estimates must be distinguished from measured duty.

Manual Windows smoke check after gaming:

- Close the old HeatMap and start the updated version normally with
  `run_as_admin.bat` and UAC.
- Compare G.CORE and HOTSPOT with Current/Junction in Adrenalin at the same moment;
  check V.TEMP, unavailable sensors shown as `--`, absence of clipped labels and
  overlaps at the current DPI, Details OFF/ON, and placement of an expanded window
  near the edge.
- Under ordinary gaming load, check the separate red HOTSPOT indicator and sound
  with Alerts enabled; do not deliberately cause overheating. Check Alerts OFF
  and Reset peaks. Freshness/errors are safely covered by fake tests; do not
  disable the driver.
- If Hotspot again remains at 108–110°C with a substantially cooler Core, reduce
  load, compare Adrenalin readings, and check cooling. No temperature reduction
  from the display fix is claimed.

## P2 - Confirm fixed widget placement in the real Windows shell

Files: `overlay.py`, `tests/test_layout_audit.py`; behavior: `README.md`.

Current model: an independent window remains on the desktop beneath applications.
`Raise on edge` temporarily changes only the window order. The widget's position,
size, and monitor are preserved; animation, movement to the edge, hiding because
an application covers it, and WorkerW attachment are no longer used. `Always on top`
enables persistent placement above applications.

Check the following after starting the updated HeatMap:

- Hover at the right edge, repeatedly leave and return, and click readings, the
  title, and the menu: the widget stays in place, returns beneath applications
  after departure, and the active application retains focus.
- Maximize and minimize applications, Show Desktop/Win+D, hover and repeatedly
  click the button beside the clock, Raise on edge OFF, and an auto-hide taskbar.
  The widget remains accessible on an unobstructed desktop.
- The menu, CPU reference entry, dragging, and a lost ButtonRelease during shell
  changes; Details and sensor errors near the bottom edge. Position adjustment
  is allowed only if an expanded window extends beyond the work area.
- Close during sensor opening and a modal dialog, then restart normally. Placement
  beneath icons is not guaranteed: check that the widget does not cover needed
  icons at the chosen position.

Automated regressions and transparent off-screen Tk/Win32 windows check geometry
and lifecycle, but do not replace these Explorer, real-rendering, and gaming
checks. Historical reports of sliding Peek refer to the previous behavior model
and do not establish acceptance of the new one. Verification of independent
cooling, formulas, and restart for the current model is described in
`docs/cooling-and-desktop-1.2.0-rc.2.md`.

## P3 - Complete the physical multi-monitor and mixed-DPI acceptance matrix

File: `overlay.py`.

Geometry tests use predefined monitor areas. The available physical test setup
has one monitor; negative coordinates, staggered layouts, mixed DPI, and
disconnect/reconnect require a second display or a separate Windows VM.

Checks:

- Two monitors: horizontal, staggered, negative origin, mixed 100%/150% scaling;
  drag, save, and restart on each.
- Hovering over the outer right edge of any monitor while in an application raises
  the widget at its existing position. Internal seams and the taskbar do not
  trigger it. The cursor's monitor does not change the widget's size or position
  on another monitor.
- Disconnect and reconnect a monitor in normal mode, while temporarily raised,
  and with Always on top. The window remains accessible within a connected
  monitor's work area; the saved position reflects the new placement.
- Details and warnings at different DPI settings: labels remain accessible through
  wrapping and scrolling, the title and close button are visible, and the window
  does not cover the taskbar.
- Check compact width, separate CPU/SYS percentages, and FW beside SYS4: actual
  0/100%, unknown --%, estimated ~%, a long warning, and sensor loss. Native Tk
  regressions cover the absence of clipping at 100/150/200%.
- Menu state matches actual window order after toggles. Check top/right taskbars
  only on Windows/VM setups with supported layouts; do not change the shell
  registry for testing.

## Parking - Update LibreHardwareMonitor and PawnIO atomically to the next bundle

Promote when: a specific hardware fix/security reason warrants an upgrade, or the
scheduled latest-compatible/provenance lane identifies an incompatibility.

Why not now:

- LibreHardwareMonitor and PawnIO form a compatible pair; updating only one
  component independently reintroduces the original failure mode after a package
  update.
- The next bundle must update the LHM DLL graph, PawnIO installer metadata,
  runtime lock, manifest, sensor fixtures, and licenses in one reviewable change.

Required checks when promoted:

- Clean-room restore and staged CLR smoke checks pass before the runtime swap.
- Failed downloads/hash checks/type imports preserve the previous working bundle.
- After driver installation/reboot, an elevated hardware smoke check confirms
  CPU, GPU, RAM, storage, and available fan sensors.

## Recommended next work bundles

1. **Fixed placement:** hovering, Show Desktop, and returning to an application
   after restart.
2. **Desktop acceptance:** the physical multi-monitor/mixed-DPI matrix and only
   reproducible follow-up fixes.
