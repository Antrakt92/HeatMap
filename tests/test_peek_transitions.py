"""Stationary preview on disposable off-screen Tk windows; no hardware or user UI."""
import ctypes
from contextlib import contextmanager
import unittest
from unittest import mock

import overlay


@contextmanager
def native_app():
    root = overlay.tk.Tk()
    root.withdraw()
    root.overrideredirect(True)
    root.wm_attributes("-alpha", 0)
    root.geometry("200x120+-30200+-30000")
    root.update_idletasks()
    overlay.set_tool_window(int(root.wm_frame(), 16))
    root.deiconify()
    root.update()
    app = overlay.OverlayApp.__new__(overlay.OverlayApp)
    app.root = root
    app.running = app.peek_enabled = True
    app.topmost = app.embedded = app.peek_visible = False
    app._embed_after_id = None
    app._desktop_fallback_ready = False
    app._window_transition_generation = 0
    app.config = {"x": -30200, "y": -30000}
    app._monitor_areas = (((-31000, -31000, -29000, -29000),) * 2,)
    app._is_taskbar_at_cursor = lambda: False
    try:
        app._embed_into_desktop()
        yield app
    finally:
        app.running = False
        for callback in root.tk.call("after", "info"):
            root.after_cancel(callback)
        root.destroy()


class NativePeekTests(unittest.TestCase):
    def rectangle(self, hwnd):
        rect = ctypes.wintypes.RECT()
        self.assertTrue(overlay.user32.GetWindowRect(hwnd, ctypes.byref(rect)))
        return rect.left, rect.top, rect.right, rect.bottom

    def assert_window_state(self, app, bounds, topmost, foreground):
        hwnd = app._get_hwnd()
        self.assertTrue(overlay.user32.IsWindowVisible(hwnd))
        self.assertFalse(overlay.user32.GetParent(hwnd), "The window must remain independent of Explorer")
        self.assertEqual(self.rectangle(hwnd), bounds)
        flags = ctypes.wintypes.DWORD()
        self.assertEqual(overlay.dwmapi.DwmGetWindowAttribute(
            ctypes.wintypes.HWND(hwnd), 14, ctypes.byref(flags), ctypes.sizeof(flags)), 0)
        self.assertEqual(flags.value & 1, 0, "Desktop and preview must remain uncloaked")
        style = overlay.user32.GetWindowLongW(hwnd, overlay.GWL_EXSTYLE)
        self.assertEqual(bool(style & overlay.WS_EX_TOPMOST), topmost)
        self.assertEqual(overlay.user32.GetForegroundWindow(), foreground)

    def test_repeated_edge_preview_changes_only_z_order(self):
        with native_app() as app:
            hwnd = app._get_hwnd()
            bounds = self.rectangle(hwnd)
            saved_config = dict(app.config)
            foreground = overlay.user32.GetForegroundWindow()
            other_monitor = ((-29000, -31000, -27000, -29000),) * 2
            with (
                mock.patch.object(overlay.user32, "ShowWindow", wraps=overlay.user32.ShowWindow) as show,
                mock.patch.object(overlay.user32, "SetParent", wraps=overlay.user32.SetParent) as parent,
                mock.patch.object(app.root, "geometry", wraps=app.root.geometry) as geometry,
                mock.patch.object(overlay, "_set_window_cloaked", wraps=overlay._set_window_cloaked) as cloak,
            ):
                for index in range(5):
                    trigger = app._monitor_areas[0] if index % 2 == 0 else other_monitor
                    app._peek_show(trigger)
                    self.assertTrue(app.peek_visible)
                    self.assert_window_state(app, bounds, True, foreground)
                    app._peek_hide()
                    self.assertFalse(app.peek_visible)
                    self.assert_window_state(app, bounds, False, foreground)
                geometry.assert_not_called()
                parent.assert_not_called()
                self.assertFalse(any(call.args[1] == overlay.SW_HIDE for call in show.call_args_list))
                self.assertFalse(any(call.args[1] is True for call in cloak.call_args_list))
            self.assertEqual(app.config, saved_config)

    def test_desktop_placement_never_parents_or_unmaps_existing_window(self):
        with native_app() as app:
            hwnd = app._get_hwnd()
            bounds = self.rectangle(hwnd)
            foreground = overlay.user32.GetForegroundWindow()
            with (
                mock.patch.object(overlay.user32, "SetParent", wraps=overlay.user32.SetParent) as parent,
                mock.patch.object(overlay.user32, "ShowWindow", wraps=overlay.user32.ShowWindow) as show,
                mock.patch.object(app.root, "geometry", wraps=app.root.geometry) as geometry,
            ):
                app._embed_into_desktop()
                app._poll_desktop_visibility()
                app.root.update_idletasks()
                self.assert_window_state(app, bounds, False, foreground)
                parent.assert_not_called()
                geometry.assert_not_called()
                self.assertFalse(any(call.args[1] == overlay.SW_HIDE for call in show.call_args_list))

    def test_click_keeps_foreground_and_pointer_departure_lowers_preview(self):
        with native_app() as app:
            hwnd = app._get_hwnd()
            bounds = self.rectangle(hwnd)
            native_user = ctypes.WinDLL("user32")
            native_user.SendMessageW.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.UINT,
                                                ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
            native_user.SendMessageW.restype = ctypes.c_ssize_t
            foreground = overlay.user32.GetForegroundWindow()
            app._peek_show(app._monitor_areas[0])
            self.assertEqual(native_user.SendMessageW(hwnd, 0x21, hwnd, (0x201 << 16) | 1), 3)
            clicked = []
            app.root.bind("<Button-1>", lambda _event: clicked.append(True))
            app.root.event_generate("<ButtonPress-1>", x=10, y=10)
            app.root.event_generate("<ButtonRelease-1>", x=10, y=10)
            app.root.update()
            self.assertEqual(clicked, [True])
            self.assert_window_state(app, bounds, True, foreground)

            def outside(pointer):
                pointer._obj.x, pointer._obj.y = -30500, -30500
                return True

            with mock.patch.object(overlay.user32, "GetCursorPos", side_effect=outside):
                app._peek_check_mouse()
            self.assertFalse(app.peek_visible)
            self.assert_window_state(app, bounds, False, foreground)

    def test_stale_desktop_callback_cannot_lower_new_preview(self):
        with native_app() as app:
            bounds = self.rectangle(app._get_hwnd())
            foreground = overlay.user32.GetForegroundWindow()
            generation = app._window_transition_generation
            app._peek_show(app._monitor_areas[0])
            app._embed_into_desktop(generation)
            self.assertTrue(app.peek_visible)
            self.assert_window_state(app, bounds, True, foreground)

    def test_topmost_toggle_keeps_same_visible_stationary_window(self):
        with native_app() as app:
            bounds = self.rectangle(app._get_hwnd())
            foreground = overlay.user32.GetForegroundWindow()
            with mock.patch.object(app, "_set_menu_label"), mock.patch.object(app, "_schedule_peek_poll"):
                app._peek_show(app._monitor_areas[0])
                app.toggle_topmost()
                self.assertTrue(app.topmost)
                self.assertFalse(app.peek_visible)
                self.assert_window_state(app, bounds, True, foreground)
                app.toggle_topmost()
                self.assertFalse(app.topmost)
                self.assert_window_state(app, bounds, False, foreground)

    def test_minimization_recovery_restores_window_without_focus_or_relocation(self):
        for raised in (False, True):
            with self.subTest(raised=raised), native_app() as app:
                hwnd = app._get_hwnd()
                bounds = self.rectangle(hwnd)
                foreground = overlay.user32.GetForegroundWindow()
                if raised:
                    app._peek_show(app._monitor_areas[0])
                overlay.user32.ShowWindow(hwnd, 7)  # SW_SHOWMINNOACTIVE; only this off-screen fixture.
                self.assertTrue(overlay.user32.IsIconic(hwnd))
                app._poll_desktop_visibility()
                app.root.update_idletasks()
                self.assertFalse(overlay.user32.IsIconic(hwnd))
                self.assertEqual(app.peek_visible, raised)
                self.assert_window_state(app, bounds, raised, foreground)

    def test_desktop_edge_preview_stays_raised_through_visibility_polls(self):
        with native_app() as app:
            hwnd = app._get_hwnd()
            bounds = self.rectangle(hwnd)
            foreground = overlay.user32.GetForegroundWindow()
            app._peek_show(app._monitor_areas[0])
            with (
                mock.patch.object(overlay, "_window_has_class", return_value=True),
                mock.patch.object(overlay.user32, "WindowFromPoint", return_value=123),
            ):
                for _ in range(4):
                    app._poll_desktop_visibility()
                    self.assertTrue(app.peek_visible)
                    self.assert_window_state(app, bounds, True, foreground)


if __name__ == "__main__":
    unittest.main()
