"""Cursor, timer and drag regressions without opening any native windows."""
import re
import unittest
from types import SimpleNamespace
from unittest import mock

import overlay
from test_overlay_helpers import _FakeRoot


class MovingRoot(_FakeRoot):
    def __init__(self, x=1720, y=140):
        super().__init__(width=200, height=120)
        self.x, self.y = x, y

    def geometry(self, spec):
        super().geometry(spec)
        match = re.fullmatch(r"\+(-?\d+)\+(-?\d+)", spec)
        if match:
            self.x, self.y = map(int, match.groups())

    def winfo_rootx(self):
        return self.x

    def winfo_rooty(self):
        return self.y


class PeekLifecycleAuditTests(unittest.TestCase):
    def make_app(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.running = app.peek_enabled = app.peek_visible = True
        app.topmost = app.embedded = False
        app._settings_dialog_open = False
        app._embed_after_id = app._peek_poll_after_id = None
        app._window_transition_generation = 0
        app._cursor_was_at_peek_edge = False
        app._monitor_areas = (((0, 0, 1920, 1080), (0, 0, 1920, 1040)),)
        app._peek_monitor_area = app._monitor_areas[0]
        app.config = {"x": 120, "y": 140}
        app.root = MovingRoot(x=120)
        app._get_hwnd = lambda: 0
        app._set_window_layer = mock.Mock(return_value=True)
        app._save_config = mock.Mock()
        app._peek_hide = mock.Mock()
        return app

    @staticmethod
    def cursor_at(x, y):
        def position(pointer):
            pointer._obj.x, pointer._obj.y = x, y
            return True
        return position

    def test_old_mouse_poll_cannot_reschedule_in_a_new_peek(self):
        app = self.make_app()
        with mock.patch.object(overlay.user32, "GetCursorPos", side_effect=self.cursor_at(130, 160)):
            app._peek_check_mouse()
            old_callback = app.root.after_calls[-1][1]
            app._cancel_scheduled_embed()
            count = len(app.root.after_calls)
            old_callback()
        self.assertEqual(len(app.root.after_calls), count)
        app._peek_hide.assert_not_called()

    def test_other_monitor_edge_does_not_hold_the_old_peek_open(self):
        app = self.make_app()
        second = ((0, 1080, 1920, 2160), (0, 1080, 1920, 2120))
        app._monitor_areas += (second,)
        with mock.patch.object(overlay.user32, "GetCursorPos", side_effect=self.cursor_at(1919, 1500)):
            app._peek_check_mouse()
        app._peek_hide.assert_called_once()

    def test_cursor_query_failure_preserves_peek_and_retries(self):
        app = self.make_app()
        with mock.patch.object(overlay.user32, "GetCursorPos", return_value=False):
            app._peek_check_mouse()
        app._peek_hide.assert_not_called()
        self.assertTrue(app.root.after_calls)

    def test_release_returns_desktop_drag_inside_work_area(self):
        app = self.make_app()
        app.peek_visible = False
        app.root.x, app.root.y = 1800, 950
        app.start_drag(SimpleNamespace(x=10, y=10, x_root=1810, y_root=960))
        app._dragged = True
        app.config.update(x=1800, y=950)
        with mock.patch.object(overlay, "_get_monitor_areas", return_value=app._monitor_areas):
            app.end_drag(SimpleNamespace())
        self.assertEqual((app.root.x, app.root.y), (1720, 920))
        self.assertEqual((app.config["x"], app.config["y"]), (1720, 920))
        app._save_config.assert_called()

    def test_mouse_departure_does_not_hide_while_header_is_held(self):
        app = self.make_app()
        app.start_drag(SimpleNamespace(x=10, y=10, x_root=1730, y_root=150))
        with mock.patch.object(overlay.user32, "GetCursorPos", side_effect=self.cursor_at(900, 900)):
            app._peek_check_mouse()
        app._peek_hide.assert_not_called()
        self.assertTrue(app.root.after_calls)

    def test_desktop_embed_waits_until_drag_release(self):
        app = self.make_app()
        app.peek_visible = False
        app._drag_active = True
        self.assertFalse(app._can_embed_now())

    def test_coverage_poll_does_not_hide_the_window_being_dragged(self):
        app = self.make_app()
        app.peek_visible = False
        app._drag_active = True
        with (
            mock.patch.object(app, "_set_window_layer", return_value=True, create=True) as layer,
            mock.patch.object(overlay.user32, "GetAsyncKeyState", return_value=0x8000),
        ):
            app._poll_desktop_visibility()
        layer.assert_not_called()
        self.assertEqual(app.root.after_calls[-1][0], 250)

    def test_missed_button_release_is_recovered_by_visibility_poll(self):
        for mode in ("peek", "desktop", "topmost"):
            with self.subTest(mode=mode):
                app = self.make_app()
                app.peek_visible = mode == "peek"
                app.peek_enabled = mode == "peek"
                app.topmost = mode == "topmost"
                app._drag_active = True
                app.end_drag = mock.Mock()
                with (
                    mock.patch.object(overlay.user32, "GetAsyncKeyState", return_value=0),
                    mock.patch.object(overlay.user32, "IsIconic", return_value=False),
                    mock.patch.object(overlay.user32, "IsWindowVisible", return_value=True),
                    mock.patch.object(overlay.user32, "GetWindowLongW", return_value=overlay.WS_EX_TOPMOST),
                ):
                    app._poll_desktop_visibility()
                app.end_drag.assert_called_once_with(None)
                self.assertEqual(app.root.after_calls[-1][0], 250)

    def test_edge_poll_does_not_start_peek_during_desktop_drag(self):
        app = self.make_app()
        app.peek_visible = False
        app._drag_active = True
        app._is_taskbar_at_cursor = lambda: False
        app._peek_show = mock.Mock()
        with mock.patch.object(overlay.user32, "GetCursorPos", side_effect=self.cursor_at(1919, 150)):
            app._poll_peek_edge()
        app._peek_show.assert_not_called()
        self.assertTrue(app.root.after_calls)

    def test_direct_peek_request_does_not_interrupt_desktop_drag(self):
        app = self.make_app()
        app.peek_visible = False
        app._drag_active = True
        app._is_taskbar_at_cursor = lambda: False
        app._set_window_layer = mock.Mock(return_value=True)
        app._peek_show(app._monitor_areas[0])
        app._set_window_layer.assert_not_called()


if __name__ == "__main__":
    unittest.main()
