# Display, alerts and Peek audit

## Confirmed repairs

- The five-second screen poll treated a correctly positioned independent desktop
  fallback as an embedding failure. It repeatedly hid and remapped the window.
  Successful fallback placement is now tracked independently of WorkerW parenting.
- The previous WS_VISIBLE-only return protection did not establish that DWM could
  not replay the old surface. Transitions now cloak the composed surface before
  geometry/layer changes and uncloak only after placement. Shell transitions are
  disabled; HeatMap's own slide remains. This is an additional compositor guard,
  not proof that every reported flash had the same cause.
- Peek no longer slides away while its menu or CPU reference dialog is open.
- Muting cancels a queued alert before its first beep and between its two beeps.
  A native beep already executing cannot be interrupted. Preview fixtures remain
  silent and never invoke the sound worker.

## Color policy

The complete yellow/red table is in [README](../README.md#температуры-и-пороги).
CPU/GPU utilization and clocks are neutral: high activity alone is not a fault.
Current temperatures and capacity use share thresholds with the alert policy.
RAM/VRAM row warnings start at 80/90%; panel and sound start at 95/98%.
Fan RPM does not become yellow merely because it is high. A previously running
fan stopped for ten seconds under the corresponding component's heat is red.
Auxiliary board sensors without model-specific limits and historical peaks are
neutral. Missing/invalid values are gray. Neutral is not a hardware health verdict.

Temperature thresholds are application action thresholds, not measured firmware
limits or proof of damage. The CPU red threshold of 85°C deliberately precedes
the [5600X's published 95°C Tjmax](https://www.amd.com/en/products/processors/desktops/ryzen/5000-series/amd-ryzen-5-5600x.html).
No GPU damage or throttling limit is inferred from these colors.

## Fan percentage and layout

The CPU and CPU Optional headers each prefer their own measured control duty.
If unavailable, the optional rated-RPM setting enables `~90% ref` alongside RPM.
It is an estimate of rotation relative to a nominal reference, not PWM or airflow.
No default model is assumed and legacy observed maxima are ignored.
[NH-U12A specifications](https://www.noctua.at/en/products/nh-u12a/specifications)
give 2000 RPM for its supplied fans; the low-noise adapter changes the reference.
The user's local configuration can select this model's standard nominal value.

Four groups separate CPU, GPU, case cooling, and memory/storage. Hotspot and its
delta share a row; both RAM and VRAM include GB and percent. A visible settings
button opens Display, Alerts & limits, Cooling, and Diagnostics. Secondary board,
disk and historical readings remain available through Display → Details.

## Verification and remaining acceptance

- 333 unit/native Tk tests passed, including color boundaries/invalid values,
  CPU reference and control pairing, mute races, menu state, fallback scheduling,
  transitions and DWM cloak readback on real off-screen windows.
- Compileall, DLL integrity and runtime manifest checks passed.
- Native desktop integration passed: window order, minimize recovery, unchanged
  foreground focus and successful DWM setters.
- Silent off-screen PrintWindow renders of normal and warning states were reviewed.
  Layout tests cover expanded content, constrained work areas and multiple scales.

[Microsoft's DWM attributes](https://learn.microsoft.com/en-us/windows/win32/api/dwmapi/ne-dwmapi-dwmwindowattribute)
document transition suppression and owner cloaking. Successful API/state checks
do not prove frame-perfect behavior in the user's game. Remaining acceptance:
repeated edge entry/exit while gaming, interrupted slides, Win+D/Show Desktop,
reboot, Explorer restart and physical mixed-DPI/multi-monitor transitions.
No game temperature improvement or physical cooling repair is claimed.

## Applied local check

After the approved normal elevated restart, the new overlay HWND was visible and
uncloaked at the preserved desktop position. Alerts remained OFF, Details OFF,
and automatic case fans ON; the CPU reference was set to 2000 RPM. The previous
worker reported confirmed restoration with no restore errors. The new worker
passed its full-speed check and returned to temperature-based AUTO at 80%, with
SYS 1/2/4 reporting approximately 1082/1000/974 RPM. These are a point-in-time
startup observation, not a controlled cooling comparison or reboot test.

## Click activation follow-up

The user reported that Peek remained open after clicking it and moving the cursor
back over the game, until another click activated the game. A real disposable Tk
window confirmed `WM_MOUSEACTIVATE` returned `MA_ACTIVATE` (1) despite the window's
`WS_EX_NOACTIVATE` style. This was a gap in the previous focus tests, which covered
programmatic window movement but not the mouse-activation message.

The overlay wrapper now has a UI-thread `SetWindowSubclass` handler returning
`MA_NOACTIVATE` (3); other messages go to `DefSubclassProc`, and the handler is
removed on `WM_NCDESTROY`. A process-lifetime callback reference prevents native
calls into a garbage-collected Python callback. Reinstallation is idempotent.
Separate settings dialogs are not subclassed.

[Microsoft documents](https://learn.microsoft.com/en-us/windows/win32/inputdev/wm-mouseactivate)
that this return value preserves the click while preventing activation.
[Subclass lifetime/thread requirements](https://learn.microsoft.com/en-us/windows/win32/api/commctrl/nf-commctrl-setwindowsubclass)
and [Tk 9.0.4 wrapper source](https://github.com/tcltk/tk/blob/core-9-0-4/win/tkWinWm.c)
were inspected; Tcl reports 9.0.4 in the local virtualenv.

The new native regression failed before the fix (1 instead of 3) and passed
afterwards. It covers repeated installation, the activation response, Tk-local
click bindings, unchanged foreground and hiding on a mocked cursor departure.
Mouse movement and physical clicks in the user's game were not automated;
the Tk-local event check does not replace that acceptance scenario.

## Return visibility follow-up

The click-activation fix did not resolve the user's report. A fresh user-triggered
trace finally captured entry, cursor departure, slide-out, and return. The cursor
left the widget at about epoch 1788645648.9; the slide then ended and `peek=False`,
`animating=False`, and native topmost=False remained stable with the normal polling
callbacks still scheduled. The menu was not mapped. The saved desktop position
coincided with the Peek position. The user still saw the panel after this logical
return: always remapping the independent desktop window was insufficient.

The first temporary recorder had stopped at 3000 idle samples before an earlier
attempt; it did not capture that attempt and was not used as evidence. A bounded
rotating recorder without a time limit captured the successful reproduction.

The independent fallback now remains unmapped and owner-cloaked while a visible,
non-minimized application fully covers its rectangle. All application windows are
considered, including a game behind a narrow active window. Shell windows, HeatMap's
own windows and DWM-cloaked windows are excluded. Two logical pixels of tolerance
cover native/Tk rounding at fractional DPI. Partial overlap retains existing desktop
behavior. Real WorkerW embedding and explicit Always on top are unchanged.

The normal visibility poll cannot remap this covered window. A deliberate edge
preview still uncloaks it; Show Desktop or an uncovered location restores normal
desktop visibility without taking focus. The new native regression failed before
the implementation because the covered window was visible after return, then passed
after it. Coverage tests include partial overlap, fractional edges, minimized,
hidden, shell, own-process and cloaked windows. The timed slide regression now
exercises the real mouse-leave handler instead of replacing it with immediate hide.

336 tests, compileall, DLL integrity and the native desktop integration check passed
locally. One full run emitted Python/Tk's interpreter-finalization RuntimeWarning
from a later test thread; no test failed. Physical game-frame acceptance remains
separate from these checks and the trace of the earlier failing build.
