# GPU fan conflict recovery

The supplied rc.6 diagnostics showed a terminated GPU worker reporting changed
fan settings, with restoration deliberately refused to preserve external changes.
The remaining journal recorded the older worker's 32/35% curves and a baseline
with Zero RPM disabled. The running process predated the existing local startup
policy changes. The report did not capture the mismatched readback, so it cannot
identify the program or driver event responsible for the original conflict.

The confirmed recovery defect was that restarting or toggling the controller
could encounter the same journal conflict indefinitely, with no supported way
to accept current driver settings.

## Change

- An explicit menu OFF/ON passes a one-launch acceptance flag. Automatic starts
  and ordinary client starts still reject unknown recovery states.
- GPU identity and the entire journal schema must match. Unknown current settings
  must be valid and match a second snapshot before the old journal is renamed to
  a unique `recovery.conflict-*.json` archive. This branch sends no fan commands.
- Recognized interrupted HeatMap states retain the existing verified restoration
  path. Acceptance never skips identity checks or adopts a known leftover curve.
- Ownership failures include exact expected and observed settings in diagnostics;
  the warning describes the explicit OFF/ON recovery action.

## Verification

- Before implementation, the new acceptance/routing/diagnostic regressions failed.
- Final `.venv/Scripts/python.exe -X utf8 -m unittest discover -s tests`:
  **673 passed**. The GPU subset passed **87 tests**.
- `-m compileall -q overlay.py gpu_fans.py setup.py tests`, `setup.py --verify`,
  and `git diff --check` passed. Dependencies, drivers and runtime bundles unchanged.
- A regression fixture initially changed the curve before actual thermal takeover;
  it was corrected to trigger after the first real fake-adapter write. The worker
  then reported the conflict, retained the journal and preserved the external curve.

The first background live worker finished with verified restoration and removed
the resolved journal. Its enclosing verification script subsequently failed on a
second ADLX initialization in the same process with a native access violation.
It therefore did not establish unchanged settings or an archive-path live result.
Repeated ADLX initialization in one process needs separate investigation; the
ordinary worker uses one adapter lifetime.

A subsequent fresh worker, started without the acceptance flag, produced five
distinct thermally-ready standby reports and stopped normally with
`control_attempted=false`, no recovery pending and no restoration errors. Evidence:
`%LOCALAPPDATA%/HeatMap/diagnostics/gpu-conflict-fix-2026-09-07.json`.
This confirms ordinary startup is no longer blocked and the cool GPU remained
driver-controlled during this short check. It does not prove long-run driver
stability, physical fan response or the cause of the original settings change.

## Remaining manual acceptance

The existing elevated overlay was not restarted. Close it normally and launch
`run_as_admin.bat` to load the updated UI; a cool GPU should show
`AUTO ready · Driver curve`. Check normal game heating/cooling and handback,
and the explicit OFF/ON action if an external conflict recurs. The exact conflict
archive path has fake-hardware coverage, not a manufactured live driver conflict.
