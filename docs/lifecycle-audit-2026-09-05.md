# HeatMap: display, Peek, and shutdown

Reviewed `overlay.py`, the current `AUDIT.md`, tests, and interaction with
`FanWorkerClient`. Three agents independently reviewed Peek, layout, and shutdown;
runtime changes were combined and verified in the main work process.

## Fixes

- An old cursor-check timer no longer services a new Peek session. Hovering over
  another monitor's edge does not keep the previous Peek open. Failure to read
  the cursor position triggers a retry instead of movement toward fictional
  coordinates `(0, 0)`.
- Returning to the edge during slide-out is no longer lost: after the return to
  the desktop, the trigger is ready to open Peek again. Six pixels remain between
  Peek's right edge and the work-area edge for operating the application window.
- Pressing the title cancels the old animation. While the button is held,
  automatic embedding, hiding, and positioning do not compete with dragging.
  Releasing the button constrains the position to the work area. If Tk loses the
  release event, independent polling restores the state from the mouse button.
  A repeated ButtonRelease has no effect; a simple click during slide-in leaves
  Peek fully visible and preserves the original desktop position.
- Details changes the layout immediately in Peek. The error panel stays within
  the work area. Long details wrap to the available width; moving to a wide monitor
  restores the normal width without cumulative shrinking.
- Shutdown first prevents new jobs and signals a stop, then closes the controller
  heartbeat. A pipe error or an already destroyed Tk does not interrupt the rest
  of cleanup. Coordinates are saved only as a complete pair. The window is hidden
  before waiting for sensor/diagnostics threads with a shared deadline of up to
  five seconds; the mutex is released even if the window has already been destroyed.
- Both normal exit and a mainloop exception trigger cleanup. A CPU reference
  dialog result received after the application closes does not change configuration
  or the menu. Failure to start the PawnIO preparation thread uses the existing
  error path, allowing a retry; a closed application does not start that retry.

The fan controller still restores firmware control itself. Its process is not
killed; the bounded UI wait does not confirm completion of native restoration.
DLLs, dependencies, and UAC policy were unchanged.

## Automated verification

Result: **367 tests passed**, including 30 behavior regressions and a separate
regression for cleaning up test Tk objects in the owning thread. `compileall`,
DLL manifest verification, runtime manifest synchronization verification, native
Win32 smoke checks, and `git diff --check` passed. No packages were installed or
runtime files downloaded; unittest messages saying `Downloading Test.Package`
come from mocked test downloaders.

New regressions: `tests/test_peek_lifecycle_audit.py`,
`tests/test_layout_audit.py`, `tests/test_shutdown_audit.py`.
The existing Peek position expectation was adjusted for the intentional 6px gap.
Each group of primary defects was reproduced before the fixes.

Layout is tested on real Tk: a temporary transparent off-screen window with
activation disabled. Actual widget dimensions are checked, rather than only the
requested dimensions of hidden Tk. These checks do not use sensors, sound,
configuration, the clipboard, or the user's overlay.

Commands from the repository root, using `.venv/Scripts/python.exe`:

```text
-X utf8 -m unittest discover -s tests
-m compileall -q overlay.py setup.py tests
setup.py --verify
tools/sync_runtime_manifest.py --check
tools/test_desktop_window_integration.py
```

The last command uses temporary Win32 HWNDs and checks DWM attributes, placement
below applications, recovery from minimization, and unchanged foreground focus.
It does not switch the real Explorer shell into Show Desktop.

## Manual Windows acceptance

After closing the old instance normally, launch the updated version through
`run_as_admin.bat` with normal UAC, at a convenient time outside a game:

1. Repeat Peek → leave → immediately return to the edge; click the title during
   entry and drag during entry/exit. Release outside the widget, then repeat Peek.
   Check that the application's scrollbar remains accessible.
2. Check the menu, CPU reference, and Details in Peek; errors and long lines near
   the bottom edge, scrolling, and access to the close button.
3. Check Win+D, the button beside the clock, Peek OFF, Always on top, returning
   to an application, and an auto-hide taskbar. The overlay must not take focus.
4. On two physical monitors, check different DPI settings, negative coordinates,
   the outer edge of the other monitor, moving between screens, and disconnecting
   a screen during dragging/animation. Save the position and restart.
5. Close during warm-up/diagnostics and with a dialog open; check that no window
   remains and that the next normal launch works. With fan control enabled,
   separately confirm firmware restoration.

These checks on the user's Explorer shell, real hardware, and multiple monitors
were not performed in this audit. Automated tests do not prove operation in every
Windows state; the open acceptance gates in `AUDIT.md` were retained.

The first release CI run on Python 3.10 found deferred garbage collection of test
Tk reference cycles in a file-test thread (`Tcl_AsyncDelete`). Shared layout-test
teardown now collects them in the owning thread after the test method finishes.
The regression checks that the destroyed root is released and identifies its
finalization thread; it fails with the previous teardown. This fix does not change
application runtime behavior.

During release preparation, a separate normal elevated restart was performed: the
previous controller confirmed restoration of motherboard control, the new overlay
remained running, and the controller passed its startup check and provided eight
fresh active reports with positive RPM during 14 seconds of observation. This
confirms restart and subsequent automatic control; the listed manual UI/Explorer/DPI
scenarios and game-temperature comparison remain unconfirmed.
