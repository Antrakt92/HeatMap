# Case fan status I/O repair — 2026-09-05

The reported AIRFLOW ERROR was a Windows status-file replacement failure, not a
temperature alarm. The stopped worker's report contained WinError 5 for the
temporary JSON rename, `restore_confirmed: true` and no restore errors. This is
readback evidence that original control was restored after this incident.

## Root cause and repair

The old UI used ordinary Python `open()` while the worker used `os.replace()`.
A native Windows regression held the reader open and reproduced the same WinError
5 at the original replace call. The incident log cannot identify the particular
process holding the file then; our own reader was sufficient to cause the bug.

The reader now opens with read/write/delete sharing and closes the handle before
parsing JSON. `ReplaceFileW` publishes subsequent snapshots while existing shared
readers retain the old complete contents; initial publication uses `os.replace()`.
Both changes are needed on the tested Windows installation: shared-delete access
alone did not make `os.replace()` succeed against an open destination.

ReplaceFileW can briefly make the destination name unavailable. Opening retries
are bounded to 35 ms of sleeps. Write retries cover WinError 5/32/33/1175, with at
most 630 ms of sleeps; other errors propagate immediately. Error 1175 preserves
both file names, allowing retry. The published JSON is never written in place.
These are sleep bounds, not guarantees on native filesystem call duration.

If opening/decoding a report fails, the UI may reuse only its last validated
snapshot. Existing worker identity, exit-state and timestamp validation still run;
an active report older than ten seconds is an error. Persistent publication failure
still runs the worker's original-control restoration and verification.

Microsoft contracts:
[CreateFileW sharing](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew),
[ReplaceFileW replacement and error states](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-replacefilew).

## Verification

- Windows native held-reader regression, Unicode path, external reader lock,
  bounded permanent denial, non-lock errors and concurrent complete snapshots.
- Cache expiry and worker restoration after status publication failure.
- Full repository suite: 294 tests passed. Compileall, DLL integrity, runtime
  preflight and manifest synchronization checks passed; runtime/dependencies unchanged.

- Extended production-client stress: 2,000 writes and 29,831 reads from three
  concurrent clients over 15.42 seconds, no errors or mismatched snapshot contents.
  A direct-reader stress run first exposed bounded read-open failures under heavy
  contention; the validated client cache handles these without bypassing expiry.
- Live elevated restart on the user's B550 AORUS PRO AC: the preceding worker
  reported normal shutdown and confirmed original-control restoration. The new
  worker passed full-speed commissioning and continued automatic control.
- During 65 seconds of live observation, 6,017 reads observed 33 distinct active
  reports with no busy reads or error states. Demand decreased from 100% to 82%;
  final SYS1/SYS2/SYS4 readback was 82%, with approximately 1116/1023/988 RPM.
  The worker was alive and its final report was fresh.
- Existing enabled case fan profile, sound OFF preference and autostart settings
  were preserved. No BIOS, fan curve or GPU configuration changes were required.

This is a short live stability check plus deterministic I/O regressions, not proof
of indefinite uptime or reduced temperatures during Helldivers 2. Gaming thermal
comparison remains in AUDIT.md.
