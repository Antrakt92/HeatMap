"""Peek transitions on disposable, off-screen Tk windows; no hardware or user UI."""
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
    root.geometry("200x120+-30000+-30000")
    root.update_idletasks()
    overlay.set_tool_window(int(root.wm_frame(), 16))
    root.deiconify()
    root.update()
    app = overlay.OverlayApp.__new__(overlay.OverlayApp)
    app.root = root
    app.running = app.peek_enabled = True
    app.topmost = app.embedded = app.peek_visible = app._peek_animating = False
    app._saved_pos = app._peek_monitor_area = app._embed_after_id = None
    app._window_transition_generation = 0
    app.config = {"x": -30200, "y": -30000}
    app._monitor_areas = (((-31000, -31000, -29000, -29000),) * 2,)
    app._is_desktop_at_cursor = lambda: False
    app._desktop_foreground = lambda: False
    try:
        yield app
    finally:
        app.running = False
        for callback in root.tk.call("after", "info"):
            root.after_cancel(callback)
        root.destroy()


class NativePeekTests(unittest.TestCase):
    def test_return_stays_hidden_when_application_covers_desktop_location(self):
        with native_app() as app:
            hwnd = app._get_hwnd()
            with (
                mock.patch.object(overlay, "_covered_by_application", return_value=True),
                mock.patch.object(overlay, "find_desktop_worker_w", return_value=None),
                mock.patch.object(overlay, "_find_desktop_surface", return_value=None),
            ):
                app.peek_visible = True
                app._saved_pos = (-30200, -30000)
                app._restore_desktop_mode()
                app._cancel_scheduled_embed()
                app._embed_into_desktop()
                self.assertFalse(overlay.user32.IsWindowVisible(hwnd))
                app._poll_desktop_visibility()
                self.assertFalse(overlay.user32.IsWindowVisible(hwnd))
                # Occlusion must not prevent the next deliberate edge preview.
                with mock.patch.object(app, "_animate_slide"):
                    app._peek_show(app._monitor_areas[0])
                self.assertTrue(overlay.user32.IsWindowVisible(hwnd))
                app._restore_desktop_mode()
                app._cancel_scheduled_embed()
                app._embed_into_desktop()
                self.assertFalse(overlay.user32.IsWindowVisible(hwnd))
                # Show Desktop may leave background application HWNDs visible.
                app._desktop_foreground = lambda: True
                app._poll_desktop_visibility()
                self.assertTrue(overlay.user32.IsWindowVisible(hwnd))
                flags = ctypes.wintypes.DWORD()
                overlay.dwmapi.DwmGetWindowAttribute(ctypes.wintypes.HWND(hwnd), 14,
                    ctypes.byref(flags), ctypes.sizeof(flags))
                self.assertEqual(flags.value & 1, 0)

    def test_click_keeps_game_activation_and_leave_still_hides(self):
        with native_app() as app:
            hwnd = app._get_hwnd()
            native_user = ctypes.WinDLL("user32")
            native_user.SendMessageW.argtypes = [ctypes.wintypes.HWND, ctypes.wintypes.UINT,
                                                ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM]
            native_user.SendMessageW.restype = ctypes.c_ssize_t
            foreground = overlay.user32.GetForegroundWindow()
            # A normal client click must survive without activating Tk's wrapper.
            for _ in range(2):
                overlay.set_tool_window(hwnd)
                self.assertEqual(native_user.SendMessageW(hwnd, 0x21, hwnd, (0x201 << 16) | 1), 3)
            clicked = []
            app.root.bind("<Button-1>", lambda _event: clicked.append(True))
            # Tk routes native clicks through the real cursor position. Exercise
            # its binding with local events without moving the user's cursor.
            app.root.event_generate("<ButtonPress-1>", x=10, y=10)
            app.root.event_generate("<ButtonRelease-1>", x=10, y=10)
            app.root.update()
            self.assertEqual(clicked, [True])
            self.assertEqual(overlay.user32.GetForegroundWindow(), foreground)
            app.peek_visible = True
            def outside(pointer):
                pointer._obj.x, pointer._obj.y = -30500, -30500
                return True
            with mock.patch.object(overlay.user32, "GetCursorPos", side_effect=outside), mock.patch.object(app, "_peek_hide") as hide:
                app._peek_check_mouse()
            hide.assert_called_once()

    def test_return_is_cloaked_in_the_compositor_until_show(self):
        with native_app() as app:
            hwnd = app._get_hwnd()
            def cloaked():
                flags = ctypes.wintypes.DWORD()
                result = overlay.dwmapi.DwmGetWindowAttribute(
                    ctypes.wintypes.HWND(hwnd), 14, ctypes.byref(flags), ctypes.sizeof(flags))
                self.assertEqual(result, 0)
                return bool(flags.value & 1)
            app.peek_visible = True
            app._saved_pos = (-30200, -30000)
            app._restore_desktop_mode()
            app.root.update_idletasks()
            self.assertTrue(cloaked())
            app._cancel_scheduled_embed()
            with mock.patch.object(overlay, "find_desktop_worker_w", return_value=None):
                app._embed_into_desktop()
            self.assertFalse(cloaked())

    def test_scheduled_slide_in_and_out_returns_to_saved_position(self):
        with native_app() as app:
            app.root.geometry("200x120+-30200+-30000")
            app.root.update_idletasks()
            embed = app._embed_into_desktop
            completed = []

            def finish(generation=None):
                embed(generation)
                completed.append(True)
                app.root.quit()

            def cursor_outside(pointer):
                pointer._obj.x, pointer._obj.y = -30500, -30500
                return True

            # Mouse departure immediately after slide-in; keep the real timers,
            # animation callbacks, return delay and Win32 fallback placement.
            with (
                mock.patch.object(overlay.user32, "GetCursorPos", side_effect=cursor_outside),
                mock.patch.object(app, "_embed_into_desktop", side_effect=finish),
                mock.patch.object(overlay, "find_desktop_worker_w", return_value=None),
                mock.patch.object(overlay, "_find_desktop_surface", return_value=None),
            ):
                app._peek_show(app._monitor_areas[0])
                app.root.after(3000, app.root.quit)
                app.root.mainloop()
            self.assertEqual(completed, [True], "Peek transition did not finish")
            self.assertFalse(app.peek_visible)
            self.assertFalse(app._peek_animating)
            self.assertIsNone(app._saved_pos)
            self.assertEqual(app.root.winfo_rootx(), -30200)
            self.assertEqual(app.root.winfo_rooty(), -30000)
            self.assertTrue(overlay.user32.IsWindowVisible(app._get_hwnd()))

    def test_return_is_unmapped_until_desktop_placement_finishes(self):
        with native_app() as app:
            hwnd = app._get_hwnd()
            foreground = overlay.user32.GetForegroundWindow()
            for _ in range(5):
                app.root.wm_attributes("-topmost", True)
                app.root.wm_attributes("-alpha", 0.88)
                app.peek_visible = True
                app._saved_pos = (-30200, -30000)
                app._restore_desktop_mode()
                self.assertFalse(overlay.user32.IsWindowVisible(hwnd))
                # The independent recovery poll must not show a pending return.
                app._poll_desktop_visibility()
                self.assertFalse(overlay.user32.IsWindowVisible(hwnd))

                def position(window):
                    self.assertFalse(overlay.user32.IsWindowVisible(window))
                    self.assertEqual(app.root.wm_attributes("-alpha"), 0.88)
                    self.assertFalse(overlay.user32.GetWindowLongW(window, overlay.GWL_EXSTYLE) & overlay.WS_EX_TOPMOST)
                    return overlay.user32.SetWindowPos(
                        window, overlay.HWND_BOTTOM, 0, 0, 0, 0,
                        overlay.SWP_NOMOVE | overlay.SWP_NOSIZE | overlay.SWP_NOACTIVATE,
                    )

                app._cancel_scheduled_embed()
                with (
                    mock.patch.object(overlay, "find_desktop_worker_w", return_value=None),
                    mock.patch.object(overlay, "_position_above_desktop", side_effect=position),
                ):
                    app._embed_into_desktop()
                predecessor = overlay.user32.GetWindow(hwnd, overlay.GW_HWNDPREV)
                app.root.update()
                self.assertTrue(overlay.user32.IsWindowVisible(hwnd))
                self.assertEqual(overlay.user32.GetWindow(hwnd, overlay.GW_HWNDPREV), predecessor)
                rect = ctypes.wintypes.RECT()
                overlay.user32.GetWindowRect(hwnd, ctypes.byref(rect))
                self.assertEqual((rect.left, rect.top), (-30200, -30000))
                self.assertEqual(overlay.user32.GetForegroundWindow(), foreground)

    def test_entry_moves_offscreen_before_becoming_opaque(self):
        with native_app() as app:
            attributes = app.root.wm_attributes
            visible_positions = []

            def track(*args):
                result = attributes(*args)
                if args == ("-alpha", 0.88):
                    app.root.update_idletasks()
                    self.assertFalse(overlay.user32.IsWindowVisible(app._get_hwnd()))
                    visible_positions.append(app.root.winfo_rootx())
                return result

            with (
                mock.patch.object(app.root, "wm_attributes", side_effect=track),
                mock.patch.object(app, "_animate_slide"),
            ):
                app._peek_show(app._monitor_areas[0])
            self.assertEqual(visible_positions, [-29000])
            self.assertEqual(app._saved_pos, (-30200, -30000))

    def test_failed_detach_keeps_desktop_widget_visible(self):
        with native_app() as app:
            with mock.patch.object(app, "_detach_from_desktop", return_value=False):
                app._peek_show(app._monitor_areas[0])
            self.assertFalse(app._peek_animating)
            self.assertFalse(app.peek_visible)
            self.assertIsNone(app._saved_pos)
            self.assertTrue(overlay.user32.IsWindowVisible(app._get_hwnd()))
            self.assertEqual(app.root.wm_attributes("-alpha"), 0.88)

    def test_new_peek_cancels_pending_return_and_is_visible(self):
        with native_app() as app:
            app.peek_visible = True
            app._saved_pos = (-30200, -30000)
            app._restore_desktop_mode()
            generation = app._window_transition_generation
            with mock.patch.object(app, "_animate_slide"):
                app._peek_show(app._monitor_areas[0])
            hwnd = app._get_hwnd()
            self.assertTrue(overlay.user32.IsWindowVisible(hwnd))
            self.assertTrue(overlay.user32.GetWindowLongW(hwnd, overlay.GWL_EXSTYLE) & overlay.WS_EX_TOPMOST)
            with mock.patch.object(overlay, "embed_in_desktop") as embed:
                app._embed_into_desktop(generation)
            embed.assert_not_called()
            self.assertEqual(app._saved_pos, (-30200, -30000))

    def test_topmost_toggle_during_return_does_not_leave_window_hidden(self):
        with native_app() as app:
            app.peek_visible = True
            app._saved_pos = (-30200, -30000)
            app._restore_desktop_mode()
            with (
                mock.patch.object(app, "_set_menu_label"),
                mock.patch.object(app, "_schedule_peek_poll"),
            ):
                app.toggle_topmost()
            app.root.update()
            hwnd = app._get_hwnd()
            self.assertTrue(app.topmost)
            self.assertTrue(overlay.user32.IsWindowVisible(hwnd))
            self.assertEqual(app.root.wm_attributes("-alpha"), 0.88)
            self.assertTrue(overlay.user32.GetWindowLongW(hwnd, overlay.GWL_EXSTYLE) & overlay.WS_EX_TOPMOST)


if __name__ == "__main__":
    unittest.main()
