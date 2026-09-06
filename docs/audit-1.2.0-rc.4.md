# HeatMap 1.2.0-rc.4 audit — 2026-09-06

Scope: audit and fix the overlay, sensor/thermal policies, case and GPU workers,
launchers, rollback and startup behavior; restart the installed checkout and
publish a new preview. No CPU/RAM/GPU clock, voltage or power-limit changes.

## Changes and review

- Case controller: preload 100% before LHM changes mode in either profile;
  reject nonfinite bounds; verify shared output/manual modes; attempt EC and
  primary-channel restoration independently after failures.
- GPU controller: attempt every native release plus ADLX termination; preserve
  initialization failures on Python 3.10–3.14; journal original fan settings
  before hardware writes and recover only matching recognized states. External
  settings are preserved and ownership loss remains an explicit error.
- Commissioning: cached status reports no longer count as sustained fresh
  measurements. Case reports also reject backward/invalid timestamps.
- UI: GPU startup errors appear before the first sensor sample, including the
  existing optional alert path. Fan percentage colors remain independent of RPM.
  Previously running fans missing entirely from enumeration now receive the
  existing ten-second unavailable-feedback warning under the relevant heat source;
  sensor reset preserves their baseline and GPU replacement retires old GPU IDs.
  Removed the unused combined CPU formatter and config-loader wrapper; retained
  tests of the actual split-label and config-result behavior.
- Startup/setup: inherited CMD delayed expansion cannot corrupt `!` paths;
  inaccessible instance ownership blocks runtime replacement. DLL read/list
  failures return actionable verification errors. Windows PowerShell 5 native
  integration scripts correctly preserve arrays and pass Python source on stdin.

Feature details and earlier hardware evidence are in
[GPU cooling](gpu-fans-2026-09-06.md) and
[four case fans](shared-fans-2026-09-06.md). The shared profile uses an unmodified
signed PawnIO.Modules 0.2.10 module; manifests, license and immutable source are
included. Existing DLLs, installed drivers and Python dependency versions did not
change. Independent agents reviewed GPU, case and startup scopes; the primary
agent reviewed integration and runs the final checks/publication.

## Verification

- `python -m unittest discover -s tests`: **562 passed** on local Python 3.14.
- `python -m compileall -q` over runtime modules, tools and tests: passed.
- `python setup.py --verify`, `--preflight`: passed; supplemental module verified.
- `python tools/sync_runtime_manifest.py --check`: passed, no generated drift.
- `python -m pip check`: passed. Exact dependency age gate passed.
- `python tools/check_third_party_licenses.py`: passed for 23 locked NuGet packages.
- Windows PowerShell launcher integration passed success/warning/error/no-Python
  paths and cleaned its temporary artifacts. Disposable Task Scheduler and native
  desktop checks passed. Python 3.10 syntax compatibility was checked locally;
  execution on supported Python versions is also covered by release CI.
- `git diff --check`: passed. Pre-existing unrelated `AGENTS.md` edits are excluded.

Live candidate verification at 14:26 local time passed:

- Previous owners closed normally and confirmed restoration.
- New shared case worker produced four advancing active samples, then confirmed
  firmware restoration with no errors. New GPU worker produced five advancing
  active samples and restored the original curve/Zero RPM with no errors; the
  recovery journal was removed after this verified return.
- The existing production `HWMonitorOverlay` task launched HeatMap through its
  normal launcher. Six advancing fresh GPU/case status pairs confirmed both
  controllers active; all enabled flags survived and runtime source hashes matched
  this candidate. Task Scheduler recorded a successful invocation.
- Last startup-check sample: SYS1/SYS2/SYS4/SYS5 approximately
  1203/1176/1221/1184 RPM, unused SYS6 0 RPM at its fixed 100% command.
  GPU 3383 RPM while its requested floor was descending from startup full speed.
  These are short actuation checks, not equilibrium cooling or noise benchmarks.

Local evidence: `%LOCALAPPDATA%/HeatMap/gpu-control-2026-09-06/audit-rc4-restart.json`.

## Reboot and acceptance boundary

The existing `HWMonitorOverlay` task is enabled for this user's interactive
logon, with a 30-second delay, `LeastPrivilege`, no network/battery prerequisite,
unlimited execution time and duplicate-launch suppression. The launcher selects
the verified Python environment and asks for ordinary UAC consent. This is not
silent startup before login. The writable checkout's elevation boundary remains.

A disposable Task Scheduler registration/export/classification/cleanup test
passed using the production XML. It never executed the disposable task.
Native off-screen Win32 checks passed for DWM settings, under-application z-order,
minimize recovery and unchanged foreground focus. These checks do not simulate
an actual reboot, Explorer Show Desktop or physical mixed-DPI monitors.

Still manual: physical Windows reboot/login + UAC acceptance, prolonged game
load/noise and matched-load Hotspot comparison, true forced-kill recovery, and
Explorer/multi-monitor acceptance listed in `AUDIT.md`. Preview status is retained
until those checks have evidence. No claim is made that HeatMap fixes a previous
Windows kernel hang or the GPU heatsink/paste contact.
