"""The edge only changes the stationary window's layer, never its position."""
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
import unittest
from unittest import mock

import overlay
from test_peek_lifecycle_audit import MovingRoot


class StationaryPeekTests(unittest.TestCase):
    def make_app(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.running = app.peek_enabled = True
        app.topmost = app.embedded = app.peek_visible = False
        app._drag_active = app._dragged = app._settings_dialog_open = False
        app._desktop_fallback_ready = True
        app._peek_monitor_area = None
        app._embed_after_id = app._peek_poll_after_id = None
        app._window_transition_generation = 0
        app._cursor_was_at_peek_edge = False
        app._monitor_areas = (((0, 0, 1920, 1080), (0, 0, 1920, 1040)),)
        app.config = {"x": 120, "y": 140, "peek_enabled": True}
        app.root = MovingRoot(x=120, y=140)
        app._get_hwnd = lambda: 100
        app._is_taskbar_at_cursor = lambda: False
        app._save_config = mock.Mock()
        app._set_menu_label = mock.Mock()
        return app

    @staticmethod
    def cursor_at(x, y):
        def position(pointer):
            pointer._obj.x, pointer._obj.y = x, y
            return True
        return position

    @contextmanager
    def native(self, app, cursor=(1919, 150)):
        with ExitStack() as stack:
            replacements = (
                (overlay.user32, "SetWindowPos", True),
                (overlay.user32, "ShowWindow", True),
                (overlay.user32, "SetParent", 0),
                (overlay.user32, "SetForegroundWindow", True),
                (overlay.user32, "SetFocus", 0),
                (overlay.user32, "BringWindowToTop", True),
                (overlay.user32, "IsIconic", False),
                (overlay.user32, "IsWindowVisible", True),
                (overlay.user32, "GetWindow", 300),
                (overlay.user32, "GetWindowLongW", 0),
                (overlay, "_find_desktop_surface", 200),
                (overlay, "_set_window_cloaked", True),
                (overlay, "set_tool_window", None),
                (overlay, "_get_monitor_areas", app._monitor_areas),
            )
            spies = {}
            for owner, name, value in replacements:
                spies[name] = stack.enter_context(mock.patch.object(owner, name, return_value=value, create=True))
            state = {"style": 0}

            def position(hwnd, insert_after, *_args):
                if hwnd == 100:
                    if insert_after == overlay.HWND_TOPMOST:
                        state["style"] |= overlay.WS_EX_TOPMOST
                    elif insert_after == overlay.HWND_NOTOPMOST:
                        state["style"] &= ~overlay.WS_EX_TOPMOST
                return True

            spies["SetWindowPos"].side_effect = position
            spies["GetWindowLongW"].side_effect = lambda hwnd, _index: state["style"] if hwnd == 100 else 0
            spies["GetCursorPos"] = stack.enter_context(mock.patch.object(
                overlay.user32, "GetCursorPos", side_effect=self.cursor_at(*cursor)))
            yield SimpleNamespace(**spies)

    def assert_stationary_transition(self, app, native):
        self.assertEqual((app.root.x, app.root.y), (app.config["x"], app.config["y"]))
        self.assertEqual(app.root.geometry_calls, [])
        self.assertEqual(app.root.withdraw_count, 0)
        self.assertFalse(any(name == "-alpha" and value == 0 for name, value in app.root.attribute_calls))
        native.SetParent.assert_not_called()
        native.SetForegroundWindow.assert_not_called()
        native.SetFocus.assert_not_called()
        native.BringWindowToTop.assert_not_called()
        self.assertFalse(any(call.args[1] == overlay.SW_HIDE for call in native.ShowWindow.call_args_list))
        self.assertFalse(any(call.args[1] for call in native._set_window_cloaked.call_args_list))
        for call in native.SetWindowPos.call_args_list:
            flags = call.args[6]
            self.assertTrue(flags & overlay.SWP_NOACTIVATE)
            self.assertTrue(flags & overlay.SWP_NOMOVE)
            self.assertTrue(flags & overlay.SWP_NOSIZE)

    def test_edge_raises_current_position_even_from_another_monitor(self):
        app = self.make_app()
        other = ((1920, 0, 3840, 1080), (1920, 0, 3840, 1040))
        app._monitor_areas += (other,)
        with self.native(app, cursor=(3839, 150)) as native:
            app._peek_show(other)
        self.assertTrue(app.peek_visible)
        self.assertIsNone(getattr(app, "_saved_pos", None))
        self.assert_stationary_transition(app, native)
        self.assertTrue(any(call.args[1] == overlay.HWND_TOPMOST for call in native.SetWindowPos.call_args_list))

    def test_bare_desktop_edge_can_raise_widget_covered_elsewhere(self):
        app = self.make_app()
        del app._is_taskbar_at_cursor
        with self.native(app) as native:
            with (
                mock.patch.object(overlay.user32, "WindowFromPoint", return_value=200),
                mock.patch.object(overlay.user32, "GetWindowThreadProcessId", return_value=1),
                mock.patch.object(overlay, "_window_has_class", side_effect=lambda _hwnd, classes: "Progman" in classes),
            ):
                app._poll_peek_edge()
        self.assertTrue(app.peek_visible)
        self.assert_stationary_transition(app, native)

    def test_desktop_foreground_poll_does_not_undo_peek_while_edge_is_held(self):
        app = self.make_app()
        app.peek_visible = True
        app._peek_monitor_area = app._monitor_areas[0]
        with self.native(app) as native:
            with (
                mock.patch.object(overlay.user32, "GetForegroundWindow", return_value=200),
                mock.patch.object(overlay, "_window_has_class", side_effect=lambda _hwnd, classes: "Progman" in classes),
            ):
                app._set_window_layer(True)
                native.SetWindowPos.reset_mock()
                app._poll_desktop_visibility()
                app._peek_check_mouse()
        self.assertTrue(app.peek_visible)
        native.SetWindowPos.assert_not_called()
        self.assert_stationary_transition(app, native)

    def test_departure_lowers_same_window_without_hiding_covered_location(self):
        app = self.make_app()
        with self.native(app) as native:
            app._peek_show(app._monitor_areas[0])
            app._peek_hide()
        self.assertFalse(app.peek_visible)
        self.assert_stationary_transition(app, native)
        self.assertTrue(any(call.args[1] == overlay.HWND_NOTOPMOST for call in native.SetWindowPos.call_args_list))

    def test_repeated_raise_and_lower_never_creates_a_geometry_cycle(self):
        app = self.make_app()
        with self.native(app) as native:
            for _ in range(10):
                app._peek_show(app._monitor_areas[0])
                self.assertTrue(app.peek_visible)
                app._peek_hide()
                self.assertFalse(app.peek_visible)
        self.assert_stationary_transition(app, native)

    def test_stale_lower_callback_does_not_demote_a_new_peek(self):
        app = self.make_app()
        with self.native(app) as native:
            app._schedule_embed(50)
            stale = app.root.after_calls[-1][1]
            app._peek_show(app._monitor_areas[0])
            native.SetWindowPos.reset_mock()
            stale()
        self.assertTrue(app.peek_visible)
        native.SetWindowPos.assert_not_called()

    def test_peek_off_lowers_in_place_and_persists_only_setting(self):
        app = self.make_app()
        with self.native(app) as native:
            app._peek_show(app._monitor_areas[0])
            app.toggle_peek()
        self.assertFalse(app.peek_enabled)
        self.assertFalse(app.peek_visible)
        self.assert_stationary_transition(app, native)
        app._save_config.assert_called_once()

    def test_topmost_toggles_are_layer_changes_at_the_same_position(self):
        app = self.make_app()
        with self.native(app) as native:
            app._peek_show(app._monitor_areas[0])
            app.toggle_topmost()
            self.assertTrue(app.topmost)
            self.assertFalse(app.peek_visible)
            app.toggle_topmost()
            self.assertFalse(app.topmost)
        self.assert_stationary_transition(app, native)

    def test_dragged_peek_lowers_at_the_new_user_position(self):
        app = self.make_app()
        with self.native(app, cursor=(150, 160)) as native:
            app._peek_show(app._monitor_areas[0])
            app.start_drag(SimpleNamespace(x=10, y=10))
            app.on_drag(SimpleNamespace(x=30, y=20))
            app.end_drag(None)
            self.assertEqual((app.root.x, app.root.y), (140, 150))
            self.assertEqual((app.config["x"], app.config["y"]), (140, 150))
            app.root.geometry_calls.clear()
            app._peek_hide()
        self.assert_stationary_transition(app, native)
        app._save_config.assert_called_once()

    def test_duplicate_drag_release_does_not_repeat_save_or_change_layer(self):
        app = self.make_app()
        with self.native(app):
            app.start_drag(SimpleNamespace(x=10, y=10))
            app.on_drag(SimpleNamespace(x=30, y=20))
            app.end_drag(None)
            generation = app._window_transition_generation
            callbacks = len(app.root.after_calls)
            app.end_drag(None)
        app._save_config.assert_called_once()
        self.assertEqual(app._window_transition_generation, generation)
        self.assertEqual(len(app.root.after_calls), callbacks)

    def test_unchanged_monitor_poll_leaves_coordinates_alone(self):
        app = self.make_app()
        with self.native(app) as native:
            app._poll_screen_change()
        self.assert_stationary_transition(app, native)

    def test_removed_monitor_clamps_once_to_remaining_work_area(self):
        app = self.make_app()
        app.config.update(x=2200, y=150)
        app.root.x, app.root.y = 2200, 150
        app._monitor_areas += (((1920, 0, 3840, 1080), (1920, 0, 3840, 1040)),)
        with self.native(app):
            with mock.patch.object(overlay, "_get_monitor_areas", return_value=app._monitor_areas[:1]):
                app._poll_screen_change()
        self.assertEqual((app.root.x, app.root.y), (1720, 150))
        self.assertEqual((app.config["x"], app.config["y"]), (1720, 150))
        app._save_config.assert_called_once()

    def test_failed_raise_preserves_desktop_state_and_position(self):
        app = self.make_app()
        with self.native(app) as native:
            with mock.patch.object(app, "_set_window_layer", return_value=False) as layer:
                app._peek_show(app._monitor_areas[0])
        layer.assert_called_once_with(True)
        self.assertFalse(app.peek_visible)
        self.assertIsNone(app._peek_monitor_area)
        self.assert_stationary_transition(app, native)

    def test_edge_during_startup_does_not_raise_before_initial_desktop_placement(self):
        app = self.make_app()
        app._desktop_fallback_ready = False
        with self.native(app) as native:
            app._peek_show(app._monitor_areas[0])
        self.assertFalse(app.peek_visible)
        native.SetWindowPos.assert_not_called()
        self.assert_stationary_transition(app, native)

    def test_stationary_cursor_retries_failed_raise_without_another_edge_crossing(self):
        app = self.make_app()
        with self.native(app):
            with mock.patch.object(app, "_set_window_layer", side_effect=[False, True]) as layer:
                app._poll_peek_edge()
                self.assertFalse(app.peek_visible)
                app._poll_peek_edge()
        self.assertEqual(layer.call_args_list, [mock.call(True), mock.call(True)])
        self.assertTrue(app.peek_visible)

    def test_failed_lower_preserves_raised_state_and_retries(self):
        app = self.make_app()
        with self.native(app) as native:
            app._peek_show(app._monitor_areas[0])
            with mock.patch.object(app, "_set_window_layer", return_value=False) as layer:
                app._peek_hide()
                self.assertTrue(app.peek_visible)
                delay, retry = app.root.after_calls[-1]
                self.assertEqual(delay, 200)
                layer.return_value = True
                native.GetCursorPos.side_effect = self.cursor_at(900, 900)
                retry()
        self.assertFalse(app.peek_visible)
        self.assert_stationary_transition(app, native)


if __name__ == "__main__":
    unittest.main()
