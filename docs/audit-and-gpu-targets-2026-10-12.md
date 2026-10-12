# Deep audit and GPU temperature regulation

## Scope and policy

- Review and reproduce fan ownership, recovery, startup and shutdown failures.
- Review storage timeouts, sensor source freshness, setup integrity and launch guards.
- Replace the explicitly selected board's aggressive saved driver curve, preserving
  Zero RPM and an original-settings receipt before any native mutation.
- Add bounded temperature feedback around Core 60 C, Hotspot 80 C and memory
  80 C; keep independent full-airflow protection at Core 75 C and Hotspot/memory
  90 C. Use the greatest normalized thermal request, fresh samples only, immediate
  protective increases, ordinary acceleration at 10 percentage points/second
  and delayed gradual decreases. Feedback tolerance is Core +/-3 C and
  Hotspot/memory +/-2 C. Targets are reset after verified idle handback.
- Exercise focused regressions, the complete local suite, compilation, runtime
  verification/preflight and native read-back after ordinary restart.
- Review the integrated diff, record actual results and publish the verified change.

The targets and curve are application policy, not manufacturer-certified optimum
or guaranteed temperatures. The completed user-operated Witcher baseline used
high fan speed (Core maximum 54 C, Hotspot 74 C, memory 72 C). No matched quieter
workload has been measured and no new game/stress test is launched by this audit.

Driver profile: 30 C/23%, 50 C/35%, 70 C/60%, 80 C/75%, 90 C/100%. Its temperature
input is the driver's own curve input; Edge vs Hotspot mapping is not asserted.
The installation is explicit and preserves Zero RPM. A durable original-settings
receipt precedes mutation, advancing telemetry is checked again after staging,
and atomic journal rebasing commits the verified new baseline. Interrupted
pre-commit writes restore the original; interrupted post-commit cleanup retains
the newly installed profile. Future transient HeatMap overrides restore this
saved balanced profile after cooling or shutdown.

## Confirmed closures

- B850 ownership compares SYS1/2/3 only; automatic CPU/CPU OPT changes cannot
  stop case assistance or falsely invalidate handback. CPU stop/missing tach
  cannot be hidden by an available PWM percentage. Status publication errors
  return a failing worker result.
- Both case profiles accelerate ordinary demand gradually, retain emergency
  bypass and initialize from connected-channel feedback. Unused SYS6 at 100%
  cannot force connected fans to 100%. Explicit commissioning remains distinct.
- Generic case activation selects the saved B850 profile and preserves Alerts OFF.
- B550 shared-journal v2 records expected/pending state before every command,
  recovers known partial writes and preserves external/unknown state. A v1
  journal has no manual-state ownership proof: it is retired only if firmware
  already owns the original modes; otherwise recovery is refused with evidence.
- ADLX rejects a successful validation call whose error index identifies an
  invalid point. Optional activity counters do not participate in fan readiness.
  Display activity uses fresh exact-board ADLX where available; stale, invalid,
  foreign or unsupported metrics fall back to the existing labeled source.
- Storage readers drain queued final replies after child exit. Invalid Windows
  extents cannot bind a volume to an impossible disk. Capacity is published
  before mapping; mapping-only failures preserve capacity/sample age and use
  a separate five-minute backoff with capacity-only refreshes.
- Config writes use caller-owned temporary files; overlapping saves cannot
  consume each other's staged output.
- Runtime replacement recognizes checkout junction aliases. PawnIO preparation
  bounds reads by the locked size and stages each call independently. Hardware
  smoke is CPU-only and guarded. PnP uninstall/removal/device changes prevent
  native reads and control until safe restart.

Upstream candidates retained separately: LHM's D3D query can leave old in-range
values after failure, and early returns may skip native adapter-handle cleanup.
Neither establishes the cause of this machine's provider counters. No DLLs,
runtime versions, clocks, voltages, power limits or dependencies were changed.

## Verification

Focused regressions reproduced the confirmed prior defects before their fixes.
Whole-worker cases cover gentle starts, independent emergency temperatures,
missing/frozen/backward telemetry, Zero RPM handback, conflict preservation and
profile-commit interruption. Final local checks:

- `.venv/Scripts/python.exe -m unittest discover -s tests`: 1017 passed.
- `compileall` across changed modules, tools and tests: passed.
- `setup.py --verify`, `setup.py --preflight`, manifest synchronization and
  `git diff --check`: passed; no runtime downloads/installations performed.
- Disposable off-screen native desktop integration: DWM exclusion, z-order,
  minimized-window recovery and unchanged foreground focus passed.
- Read-only fixed-volume integration: C/D/E/F capacity and mapping passed;
  synthetic blocked child disposed in 2.23 s with no surviving child.

Real Explorer interaction, a physical reboot/multiple-monitor matrix and
matched-load thermal/noise validation of the new profile remain unperformed.
They do not change the scope of the automated/native checks above.

Normal elevated activation and restart completed. Driver read-back exactly
matched the balanced five points with Zero RPM true; the previous curve is
retained in a local profile receipt. Current preferences remain GPU/case AUTO
enabled, B850 selected and Alerts OFF. A fresh post-restart status showed GPU
Core 55 C, Hotspot 70 C, memory 74 C, 2293 RPM and ADLX activity 97%, with the
worker monitoring the driver curve and sending no transient override. Case
SYS1/2/3 were active and followed the higher CPU fan estimate. This single
observation is runtime/identity/read-back evidence, not sustained quieter-load
acceptance or a measured improvement attributable to one cooling change.

Primary references: [AMD fan tuning](https://www.amd.com/en/resources/support-articles/faqs/DH3-020.html),
[ADLX validation contract](https://raw.githubusercontent.com/GPUOpen-LibrariesAndSDKs/ADLX/d9f04a9bba022d6cf6333f005dd540b4ad19fb63/SDK/Include/IGPUManualFanTuning.h),
[PnPUtil operations](https://learn.microsoft.com/en-us/windows-hardware/drivers/devtest/pnputil-command-syntax),
[Windows disk extents](https://learn.microsoft.com/en-us/windows/win32/api/winioctl/ns-winioctl-disk_extent).
