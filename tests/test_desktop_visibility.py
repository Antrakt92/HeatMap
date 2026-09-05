import ctypes
import unittest
from unittest import mock

import overlay
from test_overlay_helpers import _FakeRoot


class DesktopVisibilityTests(unittest.TestCase):
    def test_peek_does_not_slide_away_while_its_menu_or_settings_are_open(self):
        for dialog in (True, False):
            app = self.make_app()
            app.peek_visible = True
            app.menu = mock.Mock()
            app.menu.winfo_ismapped.return_value = not dialog
            app._settings_dialog_open = dialog
            with mock.patch.object(overlay.user32, "GetCursorPos") as cursor:
                app._peek_check_mouse()
            cursor.assert_not_called()
            self.assertEqual(app.root.after_calls[-1][0], 200)

    def test_stable_fallback_does_not_hide_and_reembed_every_five_seconds(self):
        app = self.make_app()
        app._desktop_fallback_ready = True
        with (
            mock.patch.object(overlay, "_get_monitor_areas", return_value=app._monitor_areas),
            mock.patch.object(app, "_schedule_embed") as schedule,
        ):
            app._poll_screen_change()
        schedule.assert_not_called()

    def make_app(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.running = True
        app.topmost = False
        app.embedded = False
        app.peek_visible = False
        app.peek_enabled = True
        app._peek_animating = False
        app._saved_pos = None
        app._peek_monitor_area = None
        app._embed_after_id = None
        app._window_transition_generation = 0
        app._cursor_was_at_peek_edge = False
        app._monitor_areas = (((0, 0, 1920, 1080), (0, 0, 1920, 1040)),)
        app.config = {"x": 120, "y": 140}
        app.root = _FakeRoot()
        app._get_hwnd = lambda: 100
        return app

    def test_bottom_and_top_taskbars_are_not_peek_triggers(self):
        for work, y in (((0, 0, 1920, 1040), 1078), ((0, 40, 1920, 1080), 2)):
            with self.subTest(work=work):
                areas = (((0, 0, 1920, 1080), work),)
                self.assertIsNone(overlay._exposed_right_edge_monitor(1919, y, areas))

    def test_right_taskbar_is_not_a_peek_trigger(self):
        areas = (((0, 0, 1920, 1080), (0, 0, 1880, 1080)),)
        self.assertIsNone(overlay._exposed_right_edge_monitor(1919, 500, areas))
        self.assertEqual(overlay._exposed_right_edge_monitor(1879, 500, areas), areas[0])

    def test_shell_taskbar_is_ignored_even_with_auto_hide_work_area(self):
        app = self.make_app()
        app._is_desktop_hwnd = mock.Mock(return_value=False)

        def cursor(ptr):
            ptr._obj.x, ptr._obj.y = 1919, 1079
            return True

        def class_name(hwnd, buffer, length):
            buffer.value = {1: "TrayShowDesktopButtonWClass", 2: "Shell_TrayWnd"}.get(hwnd, "")
            return len(buffer.value)

        with (
            mock.patch.object(overlay.user32, "GetCursorPos", side_effect=cursor),
            mock.patch.object(overlay.user32, "WindowFromPoint", return_value=1),
            mock.patch.object(overlay.user32, "GetClassName", side_effect=class_name),
            mock.patch.object(overlay.user32, "GetAncestor", side_effect=lambda hwnd, _: 2 if hwnd == 1 else 0),
        ):
            self.assertTrue(app._is_desktop_at_cursor())

    def test_show_desktop_recovers_minimized_fallback_without_activation(self):
        app = self.make_app()
        app.peek_enabled = False
        with (
            mock.patch.object(app, "_desktop_foreground", return_value=True),
            mock.patch.object(overlay.user32, "IsIconic", return_value=True),
            mock.patch.object(overlay.user32, "IsWindowVisible", return_value=False),
            mock.patch.object(overlay.user32, "ShowWindow") as show,
            mock.patch.object(overlay, "_position_above_desktop", return_value=True) as position,
        ):
            app._poll_desktop_visibility()
        show.assert_called_once_with(100, overlay.SW_SHOWNOACTIVATE)
        position.assert_called_once_with(100)
        self.assertEqual(app.root.after_calls[-1][0], 250)

    def test_desktop_recovery_leaves_embedded_and_topmost_modes_alone(self):
        for mode in ("embedded", "topmost"):
            app = self.make_app()
            setattr(app, mode, True)
            with (
                mock.patch.object(app, "_desktop_foreground", return_value=True),
                mock.patch.object(overlay.user32, "SetWindowPos") as position,
                mock.patch.object(overlay.user32, "ShowWindow") as show,
            ):
                app._poll_desktop_visibility()
            position.assert_not_called()
            show.assert_not_called()

    def test_fallback_remains_below_inactive_application_on_another_monitor(self):
        with (
            mock.patch.object(overlay, "_find_desktop_surface", return_value=200),
            mock.patch.object(overlay.user32, "GetWindow", return_value=300),
            mock.patch.object(overlay.user32, "GetWindowLongW", return_value=0),
            mock.patch.object(overlay.user32, "SetWindowPos", return_value=True) as position,
        ):
            self.assertTrue(overlay._position_above_desktop(100))
        position.assert_called_once_with(
            100, 300, 0, 0, 0, 0,
            overlay.SWP_NOMOVE | overlay.SWP_NOSIZE | overlay.SWP_NOACTIVATE,
        )

    def test_already_positioned_fallback_does_not_reorder_windows(self):
        with (
            mock.patch.object(overlay, "_find_desktop_surface", return_value=200),
            mock.patch.object(overlay.user32, "GetWindow", return_value=100),
            mock.patch.object(overlay.user32, "SetWindowPos") as position,
        ):
            self.assertTrue(overlay._position_above_desktop(100))
        position.assert_not_called()

    def test_taskbar_predecessor_does_not_promote_widget_to_topmost(self):
        with (
            mock.patch.object(overlay, "_find_desktop_surface", return_value=200),
            mock.patch.object(overlay.user32, "GetWindow", return_value=300),
            mock.patch.object(overlay.user32, "GetWindowLongW", return_value=overlay.WS_EX_TOPMOST),
            mock.patch.object(overlay.user32, "SetWindowPos", return_value=True) as position,
        ):
            self.assertTrue(overlay._position_above_desktop(100))
        self.assertEqual(position.call_args.args[1], overlay.HWND_TOP)

    def test_taskbar_focus_uses_saved_desktop_location(self):
        app = self.make_app()
        app._saved_pos = (320, 240)
        with (
            mock.patch.object(overlay.user32, "GetForegroundWindow", return_value=10),
            mock.patch.object(overlay, "_window_has_class", side_effect=[True, True]),
            mock.patch.object(overlay.user32, "WindowFromPoint", return_value=20) as at_point,
        ):
            self.assertTrue(app._desktop_foreground())
        point = at_point.call_args.args[0]
        self.assertEqual((point.x, point.y), (320, 240))

    def test_missing_explorer_does_not_raise_fallback_above_applications(self):
        with (
            mock.patch.object(overlay, "_find_desktop_surface", return_value=None),
            mock.patch.object(overlay.user32, "SetWindowPos") as position,
        ):
            self.assertFalse(overlay._position_above_desktop(100))
        position.assert_not_called()

    def test_show_desktop_cancels_peek_and_preserves_saved_position(self):
        app = self.make_app()
        app.peek_visible = True
        app._saved_pos = (320, 240)
        with (
            mock.patch.object(app, "_desktop_foreground", return_value=True),
            mock.patch.object(overlay.user32, "IsIconic", return_value=False),
            mock.patch.object(overlay.user32, "IsWindowVisible", return_value=True),
            mock.patch.object(overlay, "_position_above_desktop", return_value=True),
        ):
            app._poll_desktop_visibility()
        self.assertFalse(app.peek_visible)
        self.assertFalse(app._peek_animating)
        self.assertIsNone(app._saved_pos)
        self.assertEqual((app.config["x"], app.config["y"]), (320, 240))

    def test_shutdown_does_not_reschedule_visibility_poll(self):
        app = self.make_app()
        app.running = False
        app._poll_desktop_visibility()
        self.assertEqual(app.root.after_calls, [])

    def test_cancelled_animation_cannot_move_a_new_peek_session(self):
        app = self.make_app()
        app._peek_animating = True
        app._animate_slide(1920, 1720, 120, -20, mock.Mock())
        stale = app.root.after_calls[-1][1]
        app._cancel_scheduled_embed()
        app._peek_animating = True
        app.root.geometry_calls.clear()
        stale()
        self.assertEqual(app.root.geometry_calls, [])

    def test_tool_window_opts_out_of_windows_peek_fading(self):
        with (
            mock.patch.object(overlay.user32, "GetWindowLongW", return_value=0),
            mock.patch.object(overlay.user32, "SetWindowLongW"),
            mock.patch.object(ctypes.windll.dwmapi, "DwmSetWindowAttribute", return_value=0) as dwm,
        ):
            overlay.set_tool_window(100)
        self.assertEqual(dwm.call_args.args[:2], (100, 12))
        self.assertEqual([call.args[1] for call in dwm.call_args_list], [3, 12])
        self.assertEqual(dwm.call_args.args[2]._obj.value, 1)


if __name__ == "__main__":
    unittest.main()
