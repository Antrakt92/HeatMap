# HeatMap 1.2.0-rc.6: recover empty startup discovery

The user reported case control ERROR after a physical reboot on rc.5. Diagnostics
showed a 60-second SYS1 timeout with controls=0 and tachometers=0, no commands sent,
while the overlay's separate monitor showed all four connected case fans running.
GPU control was active. This disproves rc.5 physical-reboot acceptance.

## Cause and scope

Pinned LibreHardwareMonitor 0.9.5, commit
`30395c48a7f912894a7c392db8c11e1b97859658`, constructs the LPC inventory once in
`LibreHardwareMonitorLib/Hardware/Motherboard/Lpc/LpcIO.cs`. Its constructor returns
an empty inventory if `Mutexes.WaitIsaBus(100)` fails. Motherboard `SubHardware` is
then fixed; `Update()` cannot rediscover it. Actual mutex contention was not
captured in the user's report, but this upstream path explains how the two
monitor instances can disagree and is reproduced by the regression fixture.

`Computer.IsMotherboardEnabled` removes/closes its group on false and constructs
a new `MotherboardGroup` on true. The fix uses this public path only for the exact
B550 AORUS PRO AC with completely empty SubHardware, before fan ownership. It
retains the Computer, CPU/GPU instances, driver handles and startup ISettings
that preload 100% without selecting Software mode. No existing fan controller
is closed to retry. Partial inventories and missing values keep ordinary polling.

Retries are at least ten seconds apart, at most five, within the existing
60-second readiness deadline. Each replacement is followed by all original
channel, identity, running-tachometer, temperature, output-mode and full-airflow
checks. Close/open exceptions, competing tools and invalid identities remain
terminal. Stop, owner, heartbeat and deadline are checked between the group
operations and after opening. Native calls cannot be interrupted if a driver hangs.

Diagnostics retain `discovery.controller_ids` and `discovery.motherboard_reopens`
in waiting, active and terminal reports. The exhausted worker does not restart
indefinitely. Rollback uses normal HeatMap shutdown and the previous release;
no dependency, DLL, module, configuration or driver change is required.

## Implementation and automated verification

- [x] Reproduce the original 60-second timeout with an immutable empty topology.
- [x] Add bounded motherboard rediscovery and persistent diagnostic evidence.
- [x] Test fifth-attempt success, exhausted budget and missing existing channels.
- [x] Test native failure, post-discovery validation and cancellation/conflict gates.
- [x] Run full suites, compile, integrity/preflight and generated-drift checks.

Local Python 3.14: **629 tests passed**, including ten new rediscovery cases.
The original regression failed before the fix with the same 60-second SYS1 error.
Compilation, `pip check`, `setup.py --verify`, `setup.py --preflight` and
`tools/sync_runtime_manifest.py --check` passed. Slow status publication is included
in deadline coverage. These automated results do not establish physical reboot
acceptance; release CI and live restart results are reported separately.

## Manual acceptance still required

- Physical Windows reboot, login and ordinary UAC: both controllers reach active;
  if rediscovery occurred, its count remains in diagnostics and RPM respond.
- Normal shutdown confirms restoration of all owned case channels and GPU curve.
- Prolonged gaming/noise/temperature comparison with matching workload; no
  equilibrium cooling, kernel-hang recovery or unrelated Explorer/DPI claim.
