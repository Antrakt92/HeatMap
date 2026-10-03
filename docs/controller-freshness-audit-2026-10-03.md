# Controller freshness and storage access audit - 2026-10-03

This continuation inspected case/GPU controller apply and rollback ownership,
partial native writes, recovery journals, sample freshness, GPU cooling handback,
AMD adapter cleanup, storage discovery ordering and configuration positioning.
Baseline: 909 local tests. No hardware commands or elevated overlay launches were
used for this audit.

## Confirmed fixes

1. The case-worker freshness signature depended on fan enumeration order.
   Alternating two unchanged tachometer records kept a frozen temperature/RPM
   snapshot apparently fresh. The regression reproduced a successful worker exit
   instead of the required stale fault. The signature now sorts normalized
   identity/RPM pairs, preserving duplicate and unavailable readings. A full
   mocked worker run now requests full airflow after stale readings, faults after
   the existing deadline and verifies restoration of both controlled channels.
2. GPU assistance counted elapsed time across a blocked sampling interval as
   cooling evidence. Two cool readings at seconds 1 and 12 could release
   assistance despite the intervening lack of observations. Gaps longer than
   the existing six-second stale-degrade interval, non-finite clock values and
   repeated/reversed clock readings now restart cooling evidence. Regular fresh
   samples still release after the original ten-second hold. The existing reheat
   and missing-sensor test now feeds intermediate observations explicitly.
3. Storage discovery checked access before runtime verification and Windows
   properties, then entered a native SMART constructor before its next check.
   A competing hardware tool appearing during the preceding slow query could
   therefore overlap native disk I/O. Access is rechecked immediately before
   invoking that constructor. A mocked conflict after Windows properties now
   raises the existing conflict exception with no native constructor invocation.

The three defect regressions failed before their corresponding repairs. Two
additional tests cover normalization with duplicate/missing tachometer values
and invalid/reversed cooling clocks. Existing thermal thresholds, stale deadlines,
saved curves, rollback rules, bundled DLLs and dependency manifests are unchanged.

## Fresh verification

- All 914 tests passed via `.venv/Scripts/python.exe -m unittest discover -s tests`.
- Compilation passed for overlay, setup, changed runtime modules and tests.
- `setup.py --verify` and `setup.py --preflight` passed.
- `tools/sync_runtime_manifest.py --check` passed without generated drift.
- Git whitespace checks passed.

Expected exception logs in mocked autostart, shutdown and startup failures were
part of passing tests. GitHub-hosted tests are skipped under the local-execution
policy. Fake hardware tests prove policy and call ordering, not physical fan
response or compatibility with every monitoring application.

## Manual elevated follow-up

After a normal user-operated restart, confirm that stale case diagnostics reach
their existing full-airflow/fault states and that closing restores saved control.
Observe ordinary GPU cooling/Zero RPM handback with uninterrupted diagnostics,
and hardware-access pauses while using the usual monitoring tools. Do not force
driver installation, freeze real sensors or corrupt recovery journals to test.
Reboot/login, gaming, sleep/resume and extended coexistence remain the existing
hardware follow-ups in AUDIT.md.
