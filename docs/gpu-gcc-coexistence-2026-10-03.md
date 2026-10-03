# GPU assistance beside background GCC - 2026-10-03

The user uses Gigabyte Control Center for update checks, not GPU fan tuning.
Previously its process name suppressed GPU assistance both in the overlay and
in the ADLX worker. GPU automation was enabled in the local configuration but
did not start beside GCC. No threshold change was needed.

GPU control now has a separate access scope: GCC alone is allowed, while known
driver installers and the other previously blocked tools remain rejected.
Read-only shared sensor mode and case/EC exclusion are unchanged. The overlay
keeps separate pause state for GPU ownership, including startup, explicit enable,
watchdog recovery, menu display and sensor-owner handback. A normal full shutdown
still stops both controllers. GPU assistance continues to use the existing
Core/Hotspot/Memory curves, cooling hold and bounded decay.

GCC presence does not prove it is idle or actively controlling the GPU. Therefore
ADLX snapshots remain the ownership guard: compare curve and Zero RPM on each
poll, before writing and after slow journal flushes; stop and preserve external
changes on mismatch. This is not atomic arbitration with another writer. Do not
actively tune GPU fans in GCC or Adrenalin while HeatMap owns them. Known installer
checks cannot guarantee safety against every installer or replacement during an
in-flight native call.

Local regressions cover GCC-only GPU permission with continued EC rejection,
GCC combined with every other known blocker, worker takeover and verified return
of the original curve beside GCC, live GCC arrival without GPU handback, explicit
enable, watchdog recovery and full shutdown. Existing external-change and
driver-installation tests remain mandatory.

All 923 local unit tests passed. Compilation, setup --verify, setup --preflight,
runtime-manifest drift and Git whitespace checks passed. Nine new tests include
an external curve change beside GCC: the worker faults, preserves the changed
settings and retains the recovery journal, without issuing further fan writes.
Hosted execution is skipped under repository policy.

Manual follow-up after a normal elevated restart: leave GCC in its ordinary
background/update-check state, confirm GPU diagnostics reach standby or active,
and observe ordinary fan response under the user's normal workload. Compare
driver readings at the same time. Do not install drivers, deliberately heat the
card, or start a commissioning/full-speed test to validate coexistence. Extended
noise, gaming, sleep/resume and physical cooling effectiveness remain unverified.
