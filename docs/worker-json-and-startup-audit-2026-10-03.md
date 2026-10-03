# Worker JSON and startup display audit - 2026-10-03

This continuation traced shared status parsing, GPU/shared-EC recovery journals,
heartbeat validation, shutdown ownership, commissioning evidence, and the startup,
stale, error and hardware-pause display paths. Baseline: 900 local tests.

## Confirmed fixes

1. The old bounded JSON readers parsed the first 65,536 characters without
   checking whether the file continued. A valid terminal status followed by
   padding and a corrupt tail was accepted as confirmed restoration. Both worker
   clients reproduced this failure. The shared reader now reads one extra
   character and rejects oversized documents before parsing. A document exactly
   at the limit remains valid.
2. A 40,001-character deeply nested document caused RecursionError to escape
   both status clients and recovery-journal readers. Parsing now converts this
   into the existing ValueError path. Polling continues with a recent previously
   verified report only while its normal freshness checks pass; otherwise it
   reports an error. Corrupt recovery journals remain intact and produce
   "settings preserved" errors before any adapter, bus, register or bridge call.
3. Fresh volume capacity was hidden before the first hardware sample, even though
   its independent owner had already supplied it. Initialization now displays
   current capacity and pressure warnings while sensor rows remain unavailable.
4. Hardware-access pauses discarded the same independent capacity readings and
   warnings. Pauses now preserve them alongside the pause reason and controller
   restoration warnings. No temperature or cached SMART reading is revived.

The parser fixes affect both fan status clients and both recovery journals through
one shared helper. Normal validators, worker stop/restore ordering, hardware
curves, DLLs, dependencies and runtime manifests remain unchanged.

## Verification

New regressions failed before repair for oversized status/journal documents,
parser recursion and both missing-volume display paths. Nine tests cover those
failures, the exact size boundary, verified-cache expiry, journal preservation
with no hardware calls, and native Tk capacity/warning layout at 100/150/200%.

Local gates: full unittest discovery; compileall; setup --verify and --preflight;
runtime-manifest drift; third-party license provenance; pip check; diff whitespace;
and disposable off-screen Win32 desktop checks. Final command results are recorded
in the PR. All 909 unit tests passed, along with compilation, integrity, preflight,
manifest drift, 23-package license provenance, dependency and whitespace checks.
Native Tk tests passed all three scales; native Win32 checks passed DWM exclusion,
under-application z-order, minimize recovery and unchanged focus. GitHub-hosted
execution is skipped under the local verification policy.

## Remaining evidence boundaries

Synthetic corrupt files and mocked hardware prove software rejection and ownership
ordering. They do not prove physical fan restoration, sleep/resume, gaming,
reboot/login, Explorer Show Desktop or mixed-DPI multi-monitor acceptance.
Existing hardware and shell backlog items remain open.

Manual elevated follow-up after a normal user restart: compare volume fullness
with Windows, confirm that unavailable sensor rows do not hide current drive rows,
and verify ordinary fan handback in diagnostics after closing. Do not corrupt the
real recovery journal, replace a GPU driver or force a hardware hang for testing.
