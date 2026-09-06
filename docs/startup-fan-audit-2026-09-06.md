# Startup fan control audit

## Confirmed cause

Normal GPU startup previously sent 100% unconditionally for a 15-second airflow
test. The ramp then held another ten seconds before falling at two percentage
points per second. A cold launch could therefore stay at full speed for roughly
25 seconds and take about a minute to reach its idle floor. The case worker ran
its own unconditional full-speed test. Both workers start when HeatMap opens.

This is an application startup defect relative to the requested thermal-assistance
behavior. It does not establish why fans might surge immediately at power-on,
before Windows and HeatMap run. No physical reboot was performed in this audit.

GPU demand already consumed only Core, Hotspot and Memory. CPU heat controls the
case policy, never the GPU worker. Existing status snapshots showed that separation
and the old GPU worker imposing a low-temperature floor. The saved GPU baseline
had Zero RPM disabled; preserving it does not mean stopping the GPU fans at idle.

## Behavior and implementation

- `gpu_fans.py`: cool startup is driver-owned standby, with no new curve or Zero
  RPM writes. HeatMap assists at Core >=70, Hotspot >=85 or Memory >=85 degrees C.
  Full demand remains Core >=75, Hotspot >=90 or Memory >=90. These are application
  thresholds, not hardware damage limits. CPU readings and case failures do not
  participate in GPU decisions.
- Handback requires all three fresh readings at or below 60/75/75 for ten seconds.
  A missing or repeated sample cannot count as cooling. A new intervention captures
  the latest driver settings, preserving changes made while HeatMap was idle.
- Intervention preserves the original curve temperature knots and never lowers
  their fan speeds. The requested floor is combined with that curve; the retained
  hardware fallback reaches 100% no later than its own 90-degree input, sometimes
  earlier when five available knots require a conservative envelope. Driver input
  is not assumed to equal Hotspot. Zero RPM is restored to its original value.
- Cold zero RPM is normal. Thermal takeover allows six seconds to spin up before
  escalating a missing/stopped tachometer to full demand, with failure after ten
  seconds. Once owned, missing temperatures retain the full-demand fail-safe;
  frozen metrics eventually stop the worker and restore its saved settings.
- `GpuSession` checks ownership again during restoration, including shutdown and
  read failures. Unknown external settings survive; recognized partial native
  writes can still roll back. Recovery journals remain until restoration is
  verified. Cooling ramps no longer spend elapsed time from inside the hold.
- `case_fans.py`: normal launch reuses full-RPM calibration only for the exact
  connected channel set. It starts at fresh thermal demand and confirms command
  feedback before AUTO. Cached calibration is not published as a new RPM test.
  Unknown profiles and explicit commissioning still perform the 15-second sweep.
- `shared_fans.py`: SYS4/SYS5 prime to requested nonzero duty, while unused SYS6
  retains 100%. LHM's brief internal 100% priming for independent SYS1/SYS2 remains
  as a safeguard against mode-first zero duty; the sustained test/hold is removed.
- The GPU and case commissioning helpers explicitly request `--commission` and
  require full-airflow evidence plus advancing timestamps before saving success.
  Standby and ordinary active status cannot masquerade as calibration.

No CPU fan policy, BIOS, drivers, DLLs, runtime locks, autostart task, or saved
configuration was changed. The unrelated existing `AGENTS.md` edit is preserved.

## Verification

The pre-change suite passed 629 tests. New fake-hardware regressions exercise cold
startup, hot CPU/cold GPU, each GPU sensor, heat/cool/reheat cycles, Zero RPM,
frozen or missing readings, shutdown, external settings, partial restoration,
commissioning evidence and profile-matched case startup. A withdrawn Tk test
checks driver-owned status and sensor-error rendering without touching live UI.

The new commissioning evidence/timestamp regressions failed before the helper
fixes and passed afterward. Existing readiness/commissioning fixtures now supply
the real full-RPM proof required by the stricter contract; ordinary GPU lifecycle
tests explicitly opt into commissioning when testing its full-speed path.

Final local checks, using `.venv/Scripts/python.exe` (Python 3.14):

- `-m unittest discover -s tests`: **663 passed**, including 34 added tests.
- `-m compileall -q` over runtime files, commissioning helpers and tests: passed.
- `setup.py --verify` and `setup.py --preflight`: passed.
- `tools/sync_runtime_manifest.py --check` and `-m pip check`: passed.
- `git diff --check`: passed; runtime manifests, bundled libraries/modules and
  saved configuration have no diff.

Initial full-suite failures in commissioning fixtures were corrected by adding
the required full-RPM evidence. Owner-death fixtures now terminate after actual
takeover rather than after an arbitrary number of owner checks; their restoration
assertions remain intact. No hardware interfaces were opened for live actuation,
no live overlay was restarted, and no release was published during this audit.

## Manual acceptance

1. Close HeatMap normally so the old workers can restore their saved settings.
   Launch the updated checkout normally. On a cool GPU, expect
   `AUTO ready · Driver curve`, no GPU calibration pulse, and the saved driver RPM.
2. Reboot and log in normally. Distinguish any pre-Windows burst from behavior
   after HeatMap starts. Confirm matching case calibration avoids a long 100% test.
3. Under an ordinary game workload, compare concurrent Core/Hotspot/Memory and
   measured RPM. Confirm intervention and later handback at the documented limits.
   Do not manufacture overheating, missing hardware or stalled fans for this test.
4. Close normally after assistance and confirm restoration. Check driver-setting
   changes while in standby become the next baseline. Do not operate a competing
   fan controller while HeatMap owns the GPU.

Automated tests do not establish physical fan response, boot noise, long-run
cooling improvement, or recovery from a kernel hang or forced worker termination.
