"""Exercise real Win32 window recovery using only disposable off-screen windows.

No sensors, elevation, clipboard access, shell toggles or user windows are used.
The shell reference is synthetic; actual Explorer/Win+D acceptance is separate.
"""
import ctypes
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import overlay  # noqa: E402


def main():
    user32 = overlay.user32
    user32.CreateWindowExW.argtypes = [
        ctypes.wintypes.DWORD, ctypes.wintypes.LPCWSTR, ctypes.wintypes.LPCWSTR,
        ctypes.wintypes.DWORD, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ctypes.wintypes.HWND, ctypes.wintypes.HMENU, ctypes.wintypes.HINSTANCE, ctypes.c_void_p,
    ]
    user32.CreateWindowExW.restype = ctypes.wintypes.HWND
    user32.DestroyWindow.argtypes = [ctypes.wintypes.HWND]
    user32.DestroyWindow.restype = ctypes.wintypes.BOOL
    owned = []
    flags = overlay.SWP_NOMOVE | overlay.SWP_NOSIZE | overlay.SWP_NOACTIVATE

    def create():
        hwnd = user32.CreateWindowExW(
            overlay.WS_EX_TOOLWINDOW | overlay.WS_EX_NOACTIVATE,
            "STATIC", "HeatMap synthetic native check", overlay.WS_POPUP,
            -32000, -32000, 16, 16, 0, 0, 0, None,
        )
        if not hwnd:
            raise RuntimeError("Could not create disposable native window")
        owned.append(hwnd)
        user32.ShowWindow(hwnd, overlay.SW_SHOWNOACTIVATE)
        return hwnd

    try:
        desktop, widget, application = create(), create(), create()
        results = []
        native_set_attribute = overlay.dwmapi.DwmSetWindowAttribute

        def set_attribute(*args):
            result = native_set_attribute(*args)
            results.append(result)
            return result

        # EXCLUDED_FROM_PEEK is setter-only; check the real HRESULT rather than
        # querying it through DwmGetWindowAttribute (which returns E_INVALIDARG).
        with mock.patch.object(overlay.dwmapi, "DwmSetWindowAttribute", side_effect=set_attribute):
            overlay.set_tool_window(widget)
        assert results == [0, 0], "DWM rejected transition suppression or Peek exclusion"

        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.running = True
        app.topmost = app.embedded = app.peek_visible = app._peek_animating = False
        app.root = SimpleNamespace(after=lambda *_args: None)
        app._get_hwnd = lambda: widget
        app._desktop_foreground = lambda: False
        # Model a normal app ahead of a raised shell, with our widget buried below.
        assert user32.SetWindowPos(desktop, overlay.HWND_BOTTOM, 0, 0, 0, 0, flags)
        assert user32.SetWindowPos(widget, overlay.HWND_BOTTOM, 0, 0, 0, 0, flags)
        assert user32.SetWindowPos(application, user32.GetWindow(desktop, overlay.GW_HWNDPREV), 0, 0, 0, 0, flags)
        foreground = user32.GetForegroundWindow()
        with mock.patch.object(overlay, "_find_desktop_surface", return_value=desktop):
            app._poll_desktop_visibility()
            assert user32.GetWindow(desktop, overlay.GW_HWNDPREV) == widget
            assert user32.GetWindow(widget, overlay.GW_HWNDPREV) == application
            assert not user32.GetWindowLongW(widget, overlay.GWL_EXSTYLE) & overlay.WS_EX_TOPMOST
            # Minimize only our disposable widget, then recover without activation.
            user32.ShowWindow(widget, 7)  # SW_SHOWMINNOACTIVE
            assert user32.IsIconic(widget), "Synthetic minimize was not applied"
            app._poll_desktop_visibility()
            assert not user32.IsIconic(widget)
            assert user32.IsWindowVisible(widget)
            assert user32.GetWindow(desktop, overlay.GW_HWNDPREV) == widget
            assert user32.GetForegroundWindow() == foreground, "Recovery changed foreground focus"
        print("Native desktop checks passed: DWM exclusion, under-app z-order, minimize recovery, unchanged focus")
    finally:
        for hwnd in reversed(owned):
            user32.DestroyWindow(hwnd)


if __name__ == "__main__":
    main()
