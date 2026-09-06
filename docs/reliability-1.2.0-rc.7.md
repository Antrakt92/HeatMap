# HeatMap 1.2.0-rc.7 reliability audit

This release combines the cold-start thermal-assistance changes with GPU conflict
recovery and the subsequent lifecycle audit. Existing CPU/driver/runtime settings
and the unrelated local AGENTS.md edit are outside the published change.

## Confirmed defects and fixes

1. Normal startup forced a lengthy full-airflow test even on a cold GPU and an
   already calibrated case profile. Cold GPU standby and profile-specific cached
   case calibration now avoid that sweep. Explicit commissioning retains it and
   requires advancing report timestamps plus full-RPM evidence.
2. An unknown external fan curve could leave a recovery journal blocking every
   subsequent launch. Explicit OFF/ON can archive that conflict after validating
   the GPU, journal and twice-read settings. Automatic startup never bypasses it.
3. Ownership was checked before filesystem flush/process inspection. An external
   edit during that interval could be overwritten by the next command. Recheck
   afterward and check cancellation after the final native snapshot.
4. Interrupted-session recovery did not recheck cancellation directly before
   restoration writes. The recovery path now uses the same process/owner checks;
   cancelled recovery retains its journal and does not send restoration commands.
   Ordinary shutdown restoration remains unconditional for settings we still own.
5. Repeated GPU metric timestamps could lower the command during the stale-data
   grace period. Stale samples now preserve the previous minimum and reset the
   cooling hold. Frozen data still reaches the existing failure path.
6. The ADLX binding terminated the runtime without unloading its CDLL. A second
   initialization previously produced a native access violation on this PC.
   Close now unloads its owned library handle after termination, including partial
   initialization/cleanup failures. Released interface pointers are invalidated
   even if Release fails, preventing a second call on a possibly freed pointer.

The lifecycle ordering follows AMD's pinned
[ADLXHelper.cpp](https://github.com/GPUOpen-LibrariesAndSDKs/ADLX/blob/d9f04a9bba022d6cf6333f005dd540b4ad19fb63/SDK/ADLXHelper/Windows/Cpp/ADLXHelper.cpp).
The SDK was inspected in the external source cache; no upstream source or new DLL
was added to the application.

## Verification

- The external-edit and stale-cooling regressions failed before their fixes.
  New cancellation and lifetime tests cover the corresponding missing safeguards.
- Full local suite: **683 tests passed** on the locked Python 3.14 environment.
- Compileall over runtime, tools and tests; DLL/shared-module integrity; runtime
  preflight; generated-manifest check; dependency consistency: passed.
- Three sequential read-only AmdGpuFan open/read/close cycles passed in a single
  process on the RX 7900 XT. Metric timestamps advanced; no setters were called.
- Prior conflict-fix validation produced five fresh cold standby reports. See
  `docs/gpu-conflict-recovery-2026-09-07.md` for its exact scope and the earlier
  repeated-initialization failure subsequently addressed here.

GitHub CI, package and installed restart results are recorded in the release
notes after those checks complete. Local unit tests do not establish those results.

## Remaining acceptance and rollback

Check physical reboot/login, cool startup, ordinary game heating and cooling,
restoration on normal close, and explicit OFF/ON if an external conflict occurs.
Do not manufacture overheating or kill a live fan worker for testing. Long-run
driver/kernel stability, acoustic improvement, matching-load thermal comparisons
and Explorer/multi-monitor behavior remain open; this is a prerelease.

Rollback requires normal HeatMap shutdown and verified restoration before using
the previous release. Retain recovery journals and saved configuration; never
overwrite external driver settings to clear an error.
