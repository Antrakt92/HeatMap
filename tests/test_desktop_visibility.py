import ctypes
import unittest
from unittest import mock

import overlay
from test_overlay_helpers import _FakeRoot


class DesktopVisibilityTests(unittest.TestCase):
    def test_application_foreground_keeps_desktop_window_mapped(self):
        app = self.make_app()
        with (
            mock.patch.object(overlay.user32, "IsIconic", return_value=False),
            mock.patch.object(overlay.user32, "IsWindowVisible", return_value=True),
            mock.patch.object(overlay.user32, "ShowWindow") as show,
            mock.patch.object(app, "_set_window_layer", return_value=True) as layer,
            mock.patch.object(overlay, "_set_window_cloaked") as cloak,
        ):
            app._poll_desktop_visibility()
        show.assert_not_called()
        cloak.assert_not_called()
        layer.assert_called_once_with(False)
        self.assertEqual(app.root.geometry_calls, [])

    def test_preview_stays_raised_while_its_menu_or_settings_are_open(self):
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
            self.assertTrue(app._is_taskbar_at_cursor())

    def test_show_desktop_recovers_minimized_window_without_activation(self):
        app = self.make_app()
        app.peek_enabled = False
        with (
            mock.patch.object(overlay.user32, "IsIconic", return_value=True),
            mock.patch.object(overlay.user32, "IsWindowVisible", return_value=False),
            mock.patch.object(overlay.user32, "ShowWindow") as show,
            mock.patch.object(app, "_set_window_layer", return_value=True) as layer,
        ):
            app._poll_desktop_visibility()
        show.assert_called_once_with(100, overlay.SW_SHOWNOACTIVATE)
        layer.assert_called_once_with(False)
        self.assertEqual(app.root.after_calls[-1][0], 250)

    def test_visible_preview_and_always_on_top_do_not_reorder_repeatedly(self):
        for mode in ("peek_visible", "topmost"):
            app = self.make_app()
            setattr(app, mode, True)
            with (
                mock.patch.object(overlay.user32, "IsIconic", return_value=False),
                mock.patch.object(overlay.user32, "IsWindowVisible", return_value=True),
                mock.patch.object(overlay.user32, "GetWindowLongW", return_value=overlay.WS_EX_TOPMOST),
                mock.patch.object(app, "_set_window_layer") as layer,
                mock.patch.object(overlay.user32, "ShowWindow") as show,
            ):
                app._poll_desktop_visibility()
            layer.assert_not_called()
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

    def test_bare_desktop_edge_is_allowed_to_raise_widget(self):
        for desktop_class in ("Progman", "WorkerW"):
            app = self.make_app()
            def cursor(pointer):
                pointer._obj.x, pointer._obj.y = 1919, 500
                return True
            def class_name(_hwnd, buffer, _length):
                buffer.value = desktop_class
                return len(buffer.value)
            with (
                mock.patch.object(overlay.user32, "GetCursorPos", side_effect=cursor),
                mock.patch.object(overlay.user32, "WindowFromPoint", return_value=20),
                mock.patch.object(overlay.user32, "GetClassName", side_effect=class_name),
                mock.patch.object(overlay.user32, "GetAncestor", return_value=0),
                mock.patch.object(app, "_peek_show") as raise_widget,
            ):
                self.assertFalse(app._is_taskbar_at_cursor())
                app._poll_peek_edge()
            raise_widget.assert_called_once_with(app._monitor_areas[0])

    def test_missing_explorer_does_not_raise_fallback_above_applications(self):
        with (
            mock.patch.object(overlay, "_find_desktop_surface", return_value=None),
            mock.patch.object(overlay.user32, "SetWindowPos") as position,
        ):
            self.assertFalse(overlay._position_above_desktop(100))
        position.assert_not_called()

    def test_foreground_desktop_does_not_demote_preview_before_cursor_leaves(self):
        app = self.make_app()
        app.peek_visible = True
        position = dict(app.config)
        with (
            mock.patch.object(overlay.user32, "GetForegroundWindow", return_value=10),
            mock.patch.object(overlay, "_window_has_class", return_value=True),
            mock.patch.object(overlay.user32, "WindowFromPoint", return_value=20),
            mock.patch.object(overlay.user32, "IsIconic", return_value=False),
            mock.patch.object(overlay.user32, "IsWindowVisible", return_value=True),
            mock.patch.object(overlay.user32, "GetWindowLongW", return_value=overlay.WS_EX_TOPMOST),
            mock.patch.object(app, "_set_window_layer", return_value=True) as layer,
            mock.patch.object(overlay.user32, "ShowWindow") as show,
            mock.patch.object(overlay, "_set_window_cloaked") as cloak,
        ):
            app._poll_desktop_visibility()
        self.assertTrue(app.peek_visible)
        self.assertEqual(app.config, position)
        self.assertEqual(app.root.geometry_calls, [])
        layer.assert_not_called()
        show.assert_not_called()
        cloak.assert_not_called()

    def test_shutdown_does_not_reschedule_visibility_poll(self):
        app = self.make_app()
        app.running = False
        app._poll_desktop_visibility()
        self.assertEqual(app.root.after_calls, [])

    def test_raising_layer_preserves_position_size_visibility_and_focus(self):
        app = self.make_app()
        with mock.patch.object(overlay.user32, "SetWindowPos", return_value=True) as position:
            self.assertTrue(app._set_window_layer(True))
        position.assert_called_once_with(
            100, overlay.HWND_TOPMOST, 0, 0, 0, 0,
            overlay.SWP_NOMOVE | overlay.SWP_NOSIZE | overlay.SWP_NOACTIVATE,
        )
        self.assertEqual(app.root.geometry_calls, [])
        self.assertEqual(app.root.withdraw_count, 0)

    def test_lowering_layer_drops_topmost_before_desktop_placement(self):
        app = self.make_app()
        sequence = []
        with (
            mock.patch.object(overlay.user32, "GetWindowLongW", return_value=overlay.WS_EX_TOPMOST),
            mock.patch.object(overlay.user32, "SetWindowPos", side_effect=lambda *args: sequence.append(args) or True),
            mock.patch.object(overlay, "_position_above_desktop", side_effect=lambda hwnd: sequence.append(("desktop", hwnd)) or True),
        ):
            self.assertTrue(app._set_window_layer(False))
        self.assertEqual(sequence, [
            (100, overlay.HWND_NOTOPMOST, 0, 0, 0, 0,
             overlay.SWP_NOMOVE | overlay.SWP_NOSIZE | overlay.SWP_NOACTIVATE),
            ("desktop", 100),
        ])

    def test_missing_shell_lowers_window_to_bottom_without_hiding_it(self):
        app = self.make_app()
        with (
            mock.patch.object(overlay.user32, "GetWindowLongW", return_value=0),
            mock.patch.object(overlay, "_position_above_desktop", return_value=False),
            mock.patch.object(overlay.user32, "SetWindowPos", return_value=True) as position,
            mock.patch.object(overlay.user32, "ShowWindow") as show,
        ):
            self.assertTrue(app._set_window_layer(False))
        position.assert_called_once_with(
            100, overlay.HWND_BOTTOM, 0, 0, 0, 0,
            overlay.SWP_NOMOVE | overlay.SWP_NOSIZE | overlay.SWP_NOACTIVATE,
        )
        show.assert_not_called()

    def test_cancelled_desktop_placement_cannot_lower_new_preview(self):
        app = self.make_app()
        app._schedule_embed()
        stale = app.root.after_calls[-1][1]
        app._cancel_scheduled_embed()
        app.peek_visible = True
        with mock.patch.object(app, "_set_window_layer") as layer:
            stale()
        layer.assert_not_called()
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
