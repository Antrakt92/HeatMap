# Filesystem isolation audit - 2026-10-03

The earlier per-disk SMART isolation still left Windows filesystem capacity and
volume-extent queries inside the CPU/GPU owner. A blocked filesystem call could
therefore stall unrelated sensors. This is a confirmed ownership defect; no new
physical disk hang was induced on this machine.

## Confirmed repairs

- A separate volume owner performs fixed-letter enumeration and each volume's
  capacity/extent query in a disposable process, with an eight-second deadline.
  Cancellation uses the same bounded cleanup as isolated SMART probes. Only these
  read-only disposable processes can be terminated; fan workers are untouched.
- Failed volumes back off for five minutes. Other mounted fixed volumes continue
  sampling. Successful reads publish individually, and still-fresh readings of
  other volumes remain available while a later request waits. Removed letters are
  discarded when a complete inventory confirms their absence.
- Each capacity sample retains its own monotonic timestamp. A later publication
  cannot revive an older capacity reading. Expired values are withheld from rows,
  thermal warnings and diagnostics through the existing freshness consumer.
- Process-launch and inventory failures produce visible volume errors rather than
  killing the volume owner or retaining old capacity rows as current.
- A freshly identified disk model on a failed SMART request takes priority over
  the previous cached model. A malformed identification message becomes a probe
  error rather than escaping as a KeyError.
- Native Tk fixtures suppress the added background owner so layout verification
  cannot issue real filesystem queries or retain a destroyed interpreter.

## Evidence

Baseline: 887 unit tests passed. Two new regressions failed before the volume
owner/freshness repair: the sensor owner called the blocking filesystem reader,
and republishing a snapshot retained an expired volume sample.

Focused coverage includes one failed volume between healthy volumes, five-minute
backoff, incremental publication, preservation of other sample ages, disconnected
letters, cancellation, process-launch failure, malformed capacities/identities,
network/optical exclusion, process disposal, and the disk-model regression.

The read-only integration tool `tools/test_volume_probe_integration.py` starts a
synthetic sleeping process, waits for proof that its blocking call was reached,
times out, and confirms that the child PID no longer exists. It completed in
2.22 seconds. Real capacity and physical mapping reads passed for C:, D:, E: and
F:. No disk writes, elevation, driver installation or fan commands were used.

Local verification commands:

Final result: all 900 unit tests passed (13 additions over baseline). Compilation,
DLL/shared-module integrity, preflight, generated-manifest drift, license provenance
for 23 packages, dependency consistency, and whitespace checks passed. The native
off-screen desktop check passed DWM exclusion, under-application z-order, minimize
recovery and unchanged foreground focus. No remote test execution was used.

```powershell
.venv/Scripts/python.exe -m unittest discover -s tests
.venv/Scripts/python.exe -m compileall -q overlay.py setup.py storage_probe.py volume_probe.py tests tools/test_volume_probe_integration.py
.venv/Scripts/python.exe setup.py --verify
.venv/Scripts/python.exe setup.py --preflight
.venv/Scripts/python.exe tools/sync_runtime_manifest.py --check
.venv/Scripts/python.exe tools/check_third_party_licenses.py
.venv/Scripts/python.exe -m pip check
.venv/Scripts/python.exe tools/test_volume_probe_integration.py
.venv/Scripts/python.exe tools/test_desktop_window_integration.py
git diff --check
```

## Remaining boundaries

The storage controller/cable/device cause of the existing SATA errors remains
unknown. A user-mode deadline cannot repair Windows kernel I/O or guarantee that
a broken driver releases kernel resources promptly. Already-terminated disposable
probes are excluded from multiprocessing's unbounded exit join if Windows delays
their exit; hardware controllers never use that exception.

This audit also inspected configuration normalization/atomic saves, runtime
restore staging/rollback and integrity checks, shared heartbeat/status validation,
startup readiness, GPU restoration ownership, and display/error freshness consumers.
The broad local suite exercises their fake-object failure paths. It does not prove
real fan handback, sleep/resume, sustained gaming, physical reboot/login, Explorer
Show Desktop, or mixed-DPI multi-monitor acceptance. Those existing backlog items
remain open.

Manual elevated smoke after the user's normal restart: compare drive fullness with
Windows and ensure CPU/GPU keep updating; verify a capacity read failure is visible
without a stale percentage; close normally and confirm fan restoration through the
existing controller diagnostics. Do not unplug a disk or force a driver hang to
test this repair. GitHub-hosted tests are not requested for this change.
