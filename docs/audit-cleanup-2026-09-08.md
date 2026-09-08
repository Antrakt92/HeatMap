# HeatMap reliability and repository cleanup audit

This audit covers the fixes released in 1.2.0-rc.8 after 1.2.0-rc.7. The existing
prerelease and physical acceptance gates are unchanged. Package, GitHub CI and
installed restart evidence are recorded in the release notes after verification.

## Confirmed defects

- CPU fan duty could come from another motherboard block, and duplicate sensor
  names could associate a valid tachometer with an earlier missing channel.
  Select the identifier and duty from the board containing the selected fan.
- A NVIDIA memory usage Load sensor was ignored when used/total capacity was missing.
  Retain valid capacity usage as a percentage fallback; memory-controller
  activity and AMD's same-named activity sensor are not used as VRAM fullness.
- An error closing one heartbeat pipe during a hardware pause prevented the
  other worker receiving its stop request. Pause and quit now share the guarded
  stop routine; each child retains responsibility for restoring its own settings.
- The case status reader accepted reports from a different profile or an earlier
  launch, and malformed restoration fields could imply successful restoration.
  Validate these fields before displaying or consuming the report.
- Owner-process inspection errors could escape startup or have an empty error
  string. Both clients now report the failure without starting a worker.
- A GPU journal flush could race an external curve edit when no optional
  pre-write callback was supplied. Recheck ownership after the journal write.
- Windows casing and junction aliases could produce different locks for the same
  runtime directory. Canonicalize the directory before deriving its lock name.
- Shared commissioning read configuration before closing the overlay, losing
  settings saved by that shutdown. Read and back up configuration after close.
- Commissioning restart errors could erase the useful result or return success.
  Persist the error and return failure; restart with the current Python's
  adjacent pythonw.exe instead of assuming a checkout-local environment.
- Shared commissioning's no-new-command flag alone did not prove that no
  restoration was required. Apply the existing no-acquisition proof before
  allowing another hardware owner to start after a failed verification. Both
  tools reject contradictory final restoration reports, preserve the original
  failure reason, return failure and suppress restart when restoration is uncertain.

GPU memory semantics were checked against the pinned LHM revision
`30395c48a7f912894a7c392db8c11e1b97859658`: `NvidiaGpu.cs` calculates the Load sensor
from used/total memory; `AmdGpu.cs` reads `ADL_PMLOG_INFO_ACTIVITY_MEM` instead.

## Refactoring and unused code

The duplicated shutdown sequence is shared because both call sites must attempt
both workers even after a pipe failure. Remove the unused commissioning import.
An AST scan found no duplicate function definitions or identical function bodies
of at least nine lines in runtime/tool modules. Name-reference candidates
`Contains` and `SetValue` are compatibility adapters used by the hardware protocol,
not removable dead code. Historical configuration keys retain compatibility.
The overlay is still large; splitting it without a behavioral boundary would
add risk without addressing another confirmed defect in this audit.

## Verification

The baseline passed 683 unit tests. New fake-object regressions reproduced the
defects before fixes. The final combined suite passed **702 tests**, including
19 new regression methods. The first combined run found two old test fixtures
missing the real producer's profile; fixtures were corrected without weakening
assertions and the complete suite was rerun successfully.

Checks used the existing locked Python 3.14 virtual environment:

- `python -m unittest discover -s tests`: 702 passed.
- `python -m compileall -q` over runtime modules, setup, tools and tests: passed.
- `python setup.py --verify`, `python setup.py --preflight`,
  `python tools/sync_runtime_manifest.py --check`, `python -m pip check`: passed.
- `python tools/check_third_party_licenses.py`: 23 packages verified;
  `python tools/check_constraint_ages.py`: pinned packages passed the age gate.
- `python tools/test_desktop_window_integration.py`: passed with disposable
  off-screen windows, including DWM exclusion, z-order, minimize recovery and
  unchanged foreground focus. This is not physical Explorer acceptance.
- `tools/test_launcher_integration.ps1`: passed with fake elevation endpoint,
  invalid first interpreter, warning/failure paths and special-character paths;
  no temporary launcher artifacts remained.
- `tools/test_task_scheduler_integration.ps1`: temporary task registered,
  exported and classified `safe_current` / `LeastPrivilege`, then removed.
  The production task was neither changed nor executed.
- Independent cross-review by two agents and `git diff --check`: passed.

GitHub checks are verified separately on the published main commit. Runtime
restoration itself was not performed locally; the restore-lock regression uses
temporary Windows directories and casing aliases, not a live runtime swap.

No driver installation, dependency upgrade, fan command, UAC prompt, production
Task Scheduler modification or live overlay restart is part of this audit.

## CI follow-up

The previous scheduled candidate-dependency run failed after installing cffi
2.1.1 because production preflight requires the known-good 2.0.0 pin. That step
rejected version drift before reaching CLR/LHM, so it was not a compatibility
test. Only the `latest-compatible` final step now calls the existing isolated
CLR/LHM bridge probe. Candidate unit tests, dependency consistency, manifest and
DLL checks remain; every known-good job and the launcher retain strict preflight.
No package version, lockfile or local installation changed. The replacement
probe is checked locally with the existing environment; future candidate
versions still require their own CI evidence and the dependency promotion gate.

## Manual acceptance

- Restart HeatMap normally; compare CPU tachometer/duty and GPU VRAM with the
  available sensors. Check missing capacity and Details display.
- Check hardware-conflict pause and ordinary exit restore each controller.
  Do not kill a fan worker or manufacture overheating for this check.
- When explicit commissioning is next needed, inspect its final report,
  confirmed restoration, retained settings and successful overlay restart.
- Complete the physical boot/login, ordinary gaming, Explorer/Win+D and
  multi-monitor/DPI scenarios still listed in `AUDIT.md`.

Automated checks do not prove kernel stability, physical cooling/noise changes
or successful real commissioning. Roll back code only after normal shutdown and
verified restoration; retain configuration and recovery journals.
