"""Settings belong to the current hover session, including native Windows menus."""
import ctypes
import os
from types import SimpleNamespace
import unittest
from unittest import mock

import overlay
from test_peek_transitions import native_app
import test_stationary_peek


class SettingsPeekTests(unittest.TestCase):
    def test_native_menu_hit_requires_our_process(self):
        app = test_stationary_peek.StationaryPeekTests().make_app()
        app._menu_open = True
        for pid, expected in ((os.getpid(), True), (os.getpid() + 1, False)):
            def owner(_hwnd, pointer):
                pointer._obj.value = pid
                return 1
            with (mock.patch.object(overlay.user32, "WindowFromPoint", return_value=123),
                  mock.patch.object(overlay.user32, "GetWindowThreadProcessId", side_effect=owner),
                  mock.patch.object(overlay, "_window_has_class", return_value=True)):
                self.assertEqual(app._pointer_over_settings(800, 400), expected)

    def test_departure_ends_native_menu_before_lowering_and_allows_fresh_hover(self):
        helper = test_stationary_peek.StationaryPeekTests()
        app = helper.make_app()
        app._menu_open = True
        order = []
        with (helper.native(app, cursor=(900, 900)),
              mock.patch.object(app, "_pointer_over_settings", return_value=False),
              mock.patch.object(overlay.user32, "EndMenu", side_effect=lambda: order.append("close")),
              mock.patch.object(app, "_set_window_layer", side_effect=lambda raised: order.append(raised) or True)):
            app._peek_show(app._monitor_areas[0])
            app._peek_check_mouse()
            self.assertFalse(app.peek_visible)
            app._menu_open = False  # Native popup returns through show_menu's finally.
            app._peek_show(app._monitor_areas[0])
            self.assertTrue(app.peek_visible)
        self.assertEqual(order, [True, "close", False, True])

    def test_popup_exception_releases_grab_and_tracking(self):
        app = test_stationary_peek.StationaryPeekTests().make_app()
        app.menu = mock.Mock()
        app.menu.tk_popup.side_effect = overlay.tk.TclError("closed")
        with self.assertRaises(overlay.tk.TclError):
            app.show_menu(SimpleNamespace(x_root=20, y_root=30))
        self.assertFalse(app._menu_open)
        app.menu.grab_release.assert_called_once()

    def test_close_heatmap_from_menu_does_not_touch_destroyed_tk(self):
        app = test_stationary_peek.StationaryPeekTests().make_app()
        app.menu = mock.Mock()
        def close(*_args):
            app.running = False
        app.menu.tk_popup.side_effect = close
        app.show_menu(SimpleNamespace(x_root=20, y_root=30))
        self.assertFalse(app._menu_open)
        app.menu.grab_release.assert_not_called()

    def test_native_dialog_hover_departure_cancel_and_reentry(self):
        with native_app() as app:
            dialog = overlay.tk.Toplevel(app.root)
            dialog.wm_attributes("-alpha", 0)
            dialog.geometry("200x120+-30600+-30000")
            closed = []
            def close():
                closed.append(True)
                dialog.destroy()
            dialog.protocol("WM_DELETE_WINDOW", close)
            app.root.update()
            bounds = ctypes.wintypes.RECT()
            self.assertTrue(overlay.user32.GetWindowRect(int(dialog.wm_frame(), 16), ctypes.byref(bounds)))
            self.assertTrue(app._pointer_over_settings(bounds.left + 20, bounds.top + 5))
            app._peek_show(app._monitor_areas[0])
            def cursor(pointer):
                pointer._obj.x = dialog.winfo_rootx() + 30
                pointer._obj.y = dialog.winfo_rooty() + 30
                return True
            with mock.patch.object(overlay.user32, "GetCursorPos", side_effect=cursor):
                app._peek_check_mouse()
            self.assertTrue(app.peek_visible)
            self.assertTrue(dialog.winfo_exists())
            with mock.patch.object(overlay.user32, "GetCursorPos",
                                   side_effect=test_stationary_peek.StationaryPeekTests.cursor_at(-30800, -30500)):
                app._peek_check_mouse()
            self.assertEqual(closed, [True])
            self.assertFalse(app.peek_visible)
            app._peek_show(app._monitor_areas[0])
            self.assertTrue(app.peek_visible)
            self.assertEqual(app._settings_windows(), [])

    def test_nested_dialogs_close_before_parent(self):
        with native_app() as app:
            parent = overlay.tk.Toplevel(app.root)
            parent.withdraw()
            child = overlay.tk.Toplevel(parent)
            child.withdraw()
            order = []
            for window, name in ((child, "child"), (parent, "parent")):
                def close(window=window, name=name):
                    order.append(name)
                    window.destroy()
                window.protocol("WM_DELETE_WINDOW", close)
            app._close_peek_settings()
            self.assertEqual(order, ["child", "parent"])

    def test_departure_cancels_real_cpu_reference_dialog_without_saving(self):
        with native_app() as app:
            app._save_config = mock.Mock()
            app._set_menu_label = mock.Mock()
            app._peek_show(app._monitor_areas[0])
            app.root.after(100, app._peek_check_mouse)
            with mock.patch.object(overlay.user32, "GetCursorPos",
                                   side_effect=test_stationary_peek.StationaryPeekTests.cursor_at(-30800, -30500)):
                app.configure_cpu_reference()
            self.assertFalse(app._settings_dialog_open)
            self.assertFalse(app.peek_visible)
            self.assertEqual(app._settings_windows(), [])
            app._save_config.assert_not_called()


if __name__ == "__main__":
    unittest.main()
