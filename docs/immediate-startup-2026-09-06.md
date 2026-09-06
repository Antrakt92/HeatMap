# HeatMap 1.2.0-rc.5: immediate logon startup — 2026-09-06

Remove HeatMap's fixed 30-second delay after Windows login. Keep ordinary UAC
and the writable-checkout elevation boundary. Show the overlay without waiting
for Task Scheduler reconciliation; acquire fan control only after read-only
hardware readiness checks. No clock, voltage, power-limit or driver changes.

## Implementation checklist

- [x] Zero-delay task XML and migration preserve disabled/absent preferences,
  current-user ownership, least privilege and definition-change guards.
- [x] Shared bounded readiness helper: one-second retry, 60-second budget,
  owner/heartbeat/conflict checks and cancellation before and after probes.
- [x] Case readiness validates SYS1/SYS2 and optional shared SYS4/SYS5/unused SYS6,
  working tachometers and CPU/Core/Hotspot/Memory inputs before takeover.
- [x] GPU readiness requires the supported adapter, three temperatures and two
  increasing metric timestamps before journal recovery or new fan commands.
- [x] Waiting states are visible before the first overlay sample, preserve
  errors, do not trigger thermal alerts, and show pending GPU recovery honestly.
- [x] Commissioning budgets include readiness, full-airflow validation and fresh
  sustained reports. A timeout before any GPU command preserves its cause only
  when no recovery risk is positively established.
- [x] Background task reconciliation and UI cancellation/concurrency checks.
- [x] Full automated and Windows integration checks.
- [x] Verified live shutdown, restoration, restart and production task readback.

## Behavior and limits

Removing the task delay does not assign HeatMap first place among startup apps.
Windows scheduling, launcher preflight and the user's UAC response still take
time. After sensor readiness, the existing 15-second full-airflow validation runs
before the enabled controllers settle onto their temperature policies.

Only typed, positively identified temporary absence is retried. Invalid readings,
unsupported/ambiguous devices, competing tools, native setup/cleanup errors and
unconfirmed restoration remain terminal. Stopping during readiness sends no new
fan commands. GPU recovery records are retained if earlier ownership is unknown.

The 60-second budget is checked between native calls and again before takeover;
it cannot interrupt a hung kernel/driver call. LHM updates the same Computer
instance, so waiting can recover delayed readings but does not guarantee discovery
of a device entirely absent from that instance. No blind driver reopen loop.

Normal startup retains existing firmware/driver settings until readiness. A
previous interrupted GPU session is a separate case: its journal is reconciled
only against the exact saved identity and recognized settings, after readiness.

## Verification boundary

Local Python 3.14: **619 tests passed** (`python -m unittest discover -s tests`).
Compileall, runtime manifest drift check, setup verification and preflight passed.
Windows PowerShell 5 disposable Task Scheduler registration/export/classification
passed with the current zero-delay XML; its task was removed. Launcher integration
passed success/warning/failure/missing-interpreter paths and cleaned all temporary
artifacts. Off-screen native desktop checks passed without changing foreground focus.

Regression coverage includes slow readiness through commissioning, no writes on
timeout/cancellation, malformed and backward GPU metrics, pending recovery,
cleanup failures, disabled/foreign task preservation, background Scheduler failure,
UI responsiveness and waiting-row geometry at multiple Tk scales.

Live verification completed successfully on this PC:

- Previous controller owners closed normally and confirmed restoration.
- The new case controller produced four advancing active samples and restored
  firmware with no errors. GPU produced five samples and restored its saved curve
  with no errors; the recovery journal was removed after verified restoration.
- Normal production-task launch produced six advancing fresh active case/GPU
  report pairs. All enabled cooling flags survived restart, and runtime source
  hashes stayed unchanged throughout verification.
- Production task readback changed from `PT30S` to omitted Delay (Windows' zero
  default); the complete current-user, enabled, least-privilege contract classified
  as `safe_current`. Migration ran through the new background startup path.
- Last short startup sample: SYS1/SYS2/SYS4/SYS5 about 1201/1176/1218/1180 RPM;
  unused SYS6 stayed at 0 RPM. GPU was 3359 RPM while its floor descended through
  92%, with Core/Hotspot/Memory 37/45/56 C. These are actuation observations, not
  equilibrium temperature measurements.

Local evidence is in
`%LOCALAPPDATA%/HeatMap/gpu-control-2026-09-06/immediate-startup-restart.json`.
The original production task XML is backed up alongside it as
`task-before-immediate-startup.xml`. To restore the old delay, restore that
current-user task together with the previous application revision; otherwise the
new revision intentionally migrates an enabled old-delay task on next launch.
Disabled/absent autostart remains an independent preference and is preserved.

Physical Windows reboot/login/UAC, prolonged gaming/noise, true driver hangs and
Explorer/mixed-monitor acceptance remain separate manual checks in AUDIT.md.
Short live actuation tests do not establish equilibrium cooling or fix heatsink
contact. This revision is released as 1.2.0-rc.5; the rc.4 artifact remains unchanged.
