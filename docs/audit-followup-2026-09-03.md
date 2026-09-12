# HeatMap: follow-up audit of startup, sensors, and lifecycle

Date: 2026-09-03. Base commit: `ffaccc84d240c20b55196de6942bfda3982b5cdb`.
Reviewed the remaining startup paths, Copy diagnostics, sensor parsing, WinAPI
failure after reparenting, and thread shutdown. Agents independently reviewed the
changes; the runtime bundle and elevation policy were retained.

## Fixes

- Elevated startup passes the verified autostart state from reconciliation to the
  menu constructor. A second PowerShell query is no longer needed. A query error
  appears as `Autostart: ERROR`, and a missing task as `OFF`; fresh identity, task
  ownership, and created-task checks are retained when making changes.
- Reconciliation is inside the `try/finally` scope that releases the single-instance
  mutex after an unexpected exception.
- `Copy diagnostics` opens its own LHM Computer in a separate worker. The Tk thread
  receives the completed result through a queue; a duplicate request does not start
  a second collection. Errors or shutdown do not clear the clipboard. The worker
  that created the Computer closes it, including cancellation during Open.
- Shutdown waits for sensor and diagnostics workers within one shared five-second
  limit. A missing worker or one that did not start does not prevent closing;
  normal diagnostics completion closes the Computer before destroying the window.
- Raw CPU/GPU/RAM percentages, fan control, storage utilization, and remaining life
  are checked against the 0–100 range before rounding. Negative or nonnumeric
  RPM/clock/VRAM values are rejected. VRAM usage requires `0 <= used <= total`,
  `total > 0`.
- NVMe `Percentage Used` can exceed 100: this represents valid wear and a remaining
  life estimate of zero. This value is retained in accordance with the
  [Microsoft NVMe health structure](https://learn.microsoft.com/en-us/windows/win32/api/nvme/ns-nvme-nvme_health_info_log).
- CPU temperature no longer depends on sensor/CPU-block ordering. The maximum in
  the preferred Package/Tctl/Tdie group is selected; only when that group is absent
  is the maximum of other valid CPU temperatures used. Tctl offsets are not overridden.
- A known CPU Fan #2 no longer receives PWM from Fan #1: RPM-based display without
  another fan's control value replaces the false OFF state.
- An empty LHM Hardware list is marked as fallback and requests recovery with the
  existing 30-second cooldown.
- If SetParent succeeds but the subsequent SetWindowPos fails, embedding state
  reflects the actual live parent. A subsequent detach really detaches the window
  before Peek/Topmost.

## Performance checks

The duplicate Task Scheduler query was eliminated by reducing call count, without
weakening checks. A hidden real-Tk constructor was checked for ON/OFF/ERROR with
repeat queries prohibited: all three states were created without a query. A single
local query previously took about 0.88 s; this is a reference for the cost of the
removed operation, not a measured Windows boot speedup. This pass does not measure
full logon or cold-cache startup.

A synthetically blocked Open confirms that Copy diagnostics returns control to Tk
before hardware initialization finishes. A separate poll exists only during an
active request; normal sensor sampling frequency was not increased.

## Completed checks

Checks run through the existing `.venv\Scripts\python.exe`:

- `python -m unittest discover -s tests`: **222 passed**, baseline 192.
- Unit regressions: invalid raw values, boundaries, CPU temperature ordering,
  fan matching, empty inventory, retained/lost desktop parent, cached autostart,
  mutex cleanup, slow diagnostics, and cancellation.
- `python -m compileall -q overlay.py setup.py tests`: passed.
- `python setup.py --verify`: passed.
- `python setup.py --preflight`: passed.
- `python tools/sync_runtime_manifest.py --check`: passed.
- `pwsh -NoProfile -File tools/test_task_scheduler_integration.ps1`: passed;
  a separate disposable task was classified as safe_current/LeastPrivilege, then
  deleted. The production task, launcher, and UAC were not started.
- `git diff --check`: passed.

The new sensor/native-parent regressions reproduced defects before the fixes.
DLL/runtime integrity and the known RPC/CLIXML autostart protections were checked
by the existing suites. Installed DLLs, the driver, user settings, and the running
application instance were unchanged.

## Manual checks

After a normal elevated restart, check real CPU/GPU/RAM/storage/fan readings,
`Copy diagnostics`, closing during collection, and several Peek/Always on top
transitions. After the next Windows sign-in, check startup with the existing
30-second delay. Physical multi-monitor/mixed-DPI acceptance remains a separate
item in `AUDIT.md`.

Native Open/Close cannot be safely interrupted externally. If the driver hangs
beyond the bounded shutdown wait, daemon-worker cleanup is not guaranteed when
the process exits. This limitation is not presented as proof that the driver
cannot hang.
