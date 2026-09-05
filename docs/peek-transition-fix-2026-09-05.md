# Peek transition fix — 2026-09-05

Reported: after sliding away from the right edge, HeatMap briefly appeared again
to the left, near its saved desktop position.

## Findings and correction

The return path kept the native HWND mapped, using alpha zero while moving it
from topmost Peek to its saved position. It restored alpha after native desktop
placement. The independent 250 ms visibility poll also had no guard for the
pending return. These intermediate states made presentation depend on the timing
of Tk, native window changes and desktop composition.

The return now hides the HWND before moving back. Embed preparation applies
opacity and flushes Tk while the HWND is hidden. Only after desktop placement
does `SetWindowPos` show it, with `SWP_NOZORDER | SWP_NOACTIVATE`. Visibility
recovery skips the pending embed. Generation checks still reject stale callbacks.

A second verified defect was in entry: alpha became 0.88 at the saved position,
before moving off-screen. Entry now stays hidden through detachment, layer
selection, layout and positioning, then becomes visible at the animation origin.
If detachment fails, the desktop widget is shown again in its existing layer.

No fan settings, sensor policy, dependencies, elevation or autostart changes.

## Evidence and limits

Before the fix, two new native tests failed: opacity was restored at the old
coordinate instead of the off-screen origin, and the returning HWND remained
visible according to `IsWindowVisible`. They pass after the fix.

Six native Tk regressions cover the real scheduled slide-in/slide-out sequence,
five repeated returns, hidden placement with final z-order and focus preserved,
new Peek cancellation of a pending return, failed detachment recovery, and
switching to always-on-top while returning. Test windows remain off-screen and use no hardware access or user
settings. These checks verify native states and ordering; they do not record DWM
frames or prove the absence of a perceptible flash on every display/Explorer setup.

Local checks: 317 unit tests passed; compileall, DLL verification, setup preflight,
runtime manifest drift check and the native desktop integration check passed.

The patched checkout was restarted through normal WM_CLOSE cleanup. The previous
fan worker reported `restore_confirmed: true`; the new worker completed its
airflow check and reported `active` with measured RPM on all three controlled
channels. The live overlay HWND was visible, non-topmost, at the saved (1725, 322)
position. Alerts OFF, Details OFF and automatic case fans ON were preserved.
This is startup/native-state evidence, not a visual recording of live Peek.

Manual acceptance: repeat right-edge hover/departure over an application; verify
no flash at the saved desktop location. Repeat fast re-entry and Win+D during
both directions of animation. Physical mixed-DPI/multi-monitor acceptance remains
tracked in AUDIT.md.
