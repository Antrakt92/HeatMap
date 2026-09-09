# Project Rules - HeatMap

Global Codex rules apply. This is a Windows-only hardware monitor overlay that
uses admin-only sensor access, pythonnet, bundled DLLs, and WinAPI desktop
embedding.

## Session Start

1. Read `README.md` for install, runtime, admin, DLL, and launcher behavior.
2. Read `AUDIT.md` before planning non-trivial work; it tracks confirmed
   backlog around tests, DLL provenance, sensor parsing, and monolith risk.
3. Check `git status --short --branch` before edits.

## Project Profile

- Python desktop overlay; main runtime is `overlay.py`.
- `setup.py` manages/downloads/verifies the hardware-monitoring runtime.
- `lib_manifest.json` and `lib/` define the bundled DLL baseline.
- `run_as_admin.bat` runs setup preflight and starts the overlay elevated.
- Tests are under `tests/`; many WinAPI/tkinter behaviors still need targeted
  fake-object tests or manual Windows smoke checks.

## Release Policy

- A user request to release HeatMap means a normal public GitHub release with
  an ordinary `X.Y.Z` version, not a release candidate or prerelease.
- Do not add `rc`, alpha or beta suffixes. The published result must not remain
  a draft or have GitHub's prerelease flag enabled.
- Physical reboot/login, gaming, fan/noise, Explorer and multi-monitor checks
  are informational follow-ups, not publication gates. Do not delay a requested
  release, ask for their approval or invent a candidate stage because they are open.
- Keep the automated verification and runtime-integrity checks below. Describe
  actual evidence honestly; publication does not imply unperformed checks passed.
- This policy supersedes candidate/physical-acceptance publication conditions
  in historical audit reports and release notes. Keep old tags as history.

## Risk Areas

- Do not change admin/elevation behavior, DLL verification, or bundled runtime
  policy casually. Broken setup means users cannot read sensors.
- `lib_manifest.json` hashes are integrity data. Update only when the exact DLL
  source/provenance is known and verification supports the change.
- Sensor-name matching depends on LibreHardwareMonitor variants. Add tests for
  new matching behavior rather than relying on one local machine.
- Positioning, desktop embedding, alerts, autostart, and always-on-top behavior
  are lifecycle/UI surfaces; preserve safe fallback states.
- Avoid broad readability-only rewrites of `overlay.py`. Extract pure logic only
  when it removes real risk or creates useful test coverage.

## Verification

Default checks:

```powershell
python -m unittest discover -s tests
python -m compileall -q overlay.py setup.py tests
python setup.py --verify
```

For dependency/runtime setup changes:

```powershell
python setup.py --preflight
```

For UI, WinAPI embedding, autostart, alert, or real sensor behavior, include a
manual elevated Windows smoke checklist because automated coverage is partial.

## Implementation Guidance

- Use fake LHM objects for sensor parsing tests.
- Keep color/alert/sensor policy changes synchronized across read, render, and
  alert paths.
- During authorized implementation, update `AUDIT.md` for in-scope closures
  or confirmed risks; read-only reviews report findings without editing it.

## Git

- Stage only files changed for the current task.
- Do not add AI co-author trailers.

## Runtime Maintenance Boundary

Keep `runtime-lock.json`, `runtime_sources.json`, `constraints-known-good.txt`
and DLL manifests consistent when the authorized change touches their runtime.
Use the dependency gate before downloads or dependency changes. `setup.py --verify`
checks integrity; setup/download modes and elevated launchers are different
operations. Do not launch UAC or the live overlay for an instruction-only edit.
Report unperformed hardware/elevated checks separately from automated results.
