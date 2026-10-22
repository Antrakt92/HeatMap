# Driver-pause and interrupted-ownership reliability audit

## Evidence and cause

The user supplied an overlay with missing CPU/GPU/fan readings, an explicit
PnPUtil pause, and a historical sensor snapshot aged 72.4 seconds. The snapshot
contained valid readings before the pause; these are historical evidence only.

HeatMap.log records the pause on 2026-10-22 at 12:38:49. Windows
`setupapi.dev.log` records `pnputil /delete-driver ... /uninstall` operations at
12:38:50. Pausing native access during live device changes was correct. The
confirmed application defect was permanent termination of the sensor/storage
owner loops after a transient block. Process disappearance alone did not restore
monitoring. The initiating application and relevance of individual removed
packages were not established and are not needed to repair that lifecycle defect.

## Repairs

- Only explicitly classified driver/inventory conflicts may recover. The same
  sensor owner closes its monitor, stops fan assistance normally, checks access
  every two seconds and requires 15 uninterrupted clear seconds. A new blocker
  restarts that interval. Reopening requires successful native closure, exited
  fan workers and explicit verified handback or untouched ownership evidence.
  Generic conflicts, failed closure and uncertain restoration stay stopped.
- Storage-temperature polling survives a transient pause and resumes without
  creating another owner. Fresh Windows volume capacity and one validated RAM
  snapshot remain available during native failures. RAM display, warnings and
  alerts use the same sample; historical thermal values are excluded.
- A pending GPU recovery journal is applied before waiting for thermal readiness.
  Missing/frozen temperatures cannot prevent rollback of validated interrupted
  settings. Foreign or conflicting journals remain blocked. New takeover still
  requires complete advancing metrics and all existing ownership checks.
- PnPUtil legacy live mutation flags are recognized. Missing/malformed arguments
  cannot authorize native reads. Read-only enumeration/export and package staging
  remain available. The current incident was a real driver change.

Fan assistance remains stopped after a driver pause. Check Cooling diagnostics
and toggle the desired automatic-fan setting OFF then ON to start a fresh owner.
Monitoring recovery does not silently authorize new fan takeover.

## Verification

New fake-object regressions reproduce the old failures before the fixes and
exercise stable clearance, renewed blocking, cancellation, pending restoration,
failed Close, independent RAM warnings/alerts, storage resumption, interrupted
GPU rollback, and unchanged new-takeover readiness.

Local integration verification passed:

- `.venv/Scripts/python.exe -m unittest discover -s tests`: 1,143 tests, OK.
- `compileall` for the overlay, setup, access guard, GPU worker and tests: passed.
- `setup.py --verify` and `setup.py --preflight`: passed.
- `tools/sync_runtime_manifest.py --check`: passed.
- `tools/check_third_party_licenses.py`: 23 package provenance checks passed.
- `tools/test_desktop_window_integration.py`: native DWM, z-order, minimize
  recovery and unchanged-focus checks passed.
- `tools/test_notice_integration.py`: timers progressed with both notice types.
- `git diff --check`: passed.

The first full integration run exposed two older assertions that expected empty
alert data during native failure; they now assert an exact deterministic fresh
Windows RAM/volume snapshot. A separate existing bounded-panel assertion caught
the missing diagnostics hint; the hint was restored. The final full suite passed
after these corrections and the partial-initialization cleanup regression.

Independent review additionally found and fixed failure to respect unsuccessful
cleanup after partial native initialization. Failed Open/sanity checks followed
by failed Close now hard-stop rather than authorize a second monitor. The new
regression exercises both stages and ordinary/typed original errors.

No real driver replacement or deliberate overheating is used as a test.

## Installed-runtime check

The old overlay closed normally through the existing shutdown helper. The
independent `HWMonitorOverlay` task accepted the new launch (return code 0).
The new overlay window was visible, answered WM_NULL within one second and
remained inside the work area at (1751,236)-(2048,753).

Three independently read controller reports six seconds apart remained fresh:
the B850 case owner was active with CPU temperatures 63, 62 and 59 degrees C;
GPU standby reported Core 45, Hotspot 46 and Memory 62 degrees C, with advancing
native timestamps. Report ages were below two seconds. These prove a normal
restart and current telemetry/control state, not equivalent-load performance,
real-driver-replacement acceptance or long-duration stability. No new full-speed
commissioning procedure was run. Receipts are stored locally under
`%LOCALAPPDATA%/HeatMap/reliability-{restart,live-window,live-samples}-20261022.*`.

## Remaining limits

Native LHM reads still run in a thread inside the overlay process. A hung native
call cannot be safely interrupted by this thread. A driver removed during an
in-flight native call, a kernel fault or a Windows freeze can still require a
restart. Process isolation is a possible architectural improvement, not a
validated cause or a small equivalent repair. Long gaming/load, sleep/resume,
actual driver replacement and physical multi-monitor acceptance remain separate
checks. Software tests cannot establish permanent hardware/driver stability.
