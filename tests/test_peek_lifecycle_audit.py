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
        app.topmost = app.embedded = app._peek_animating = False
        app._settings_dialog_open = False
        app._saved_pos = (120, 140)
        app._embed_after_id = app._peek_poll_after_id = None
        app._window_transition_generation = 0
        app._cursor_was_at_peek_edge = False
        app._monitor_areas = (((0, 0, 1920, 1080), (0, 0, 1920, 1040)),)
        app._peek_monitor_area = app._monitor_areas[0]
        app.config = {"x": 120, "y": 140}
        app.root = MovingRoot()
        app._get_hwnd = lambda: 0
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
        with mock.patch.object(overlay.user32, "GetCursorPos", side_effect=self.cursor_at(1800, 160)):
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

    def test_drag_cancels_a_slide_tick_before_it_moves_the_window(self):
        app = self.make_app()
        app.peek_visible = False
        app._peek_animating = True
        app._animate_slide(1860, 1720, 140, -20, app._peek_shown)
        old_tick = app.root.after_calls[-1][1]
        app.start_drag(SimpleNamespace(x=10, y=10, x_root=1870, y_root=150))
        app.on_drag(SimpleNamespace(x=30, y=20, x_root=1890, y_root=160))
        dragged_position = (app.root.x, app.root.y)
        old_tick()
        self.assertEqual((app.root.x, app.root.y), dragged_position)

    def test_release_returns_desktop_drag_inside_work_area(self):
        app = self.make_app()
        app.peek_visible = False
        app._saved_pos = None
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
        app._desktop_foreground = lambda: False
        with (
            mock.patch.object(overlay, "_hide_covered_desktop_window", return_value=True) as hide,
            mock.patch.object(overlay.user32, "GetAsyncKeyState", return_value=0x8000),
        ):
            app._poll_desktop_visibility()
        hide.assert_not_called()
        self.assertEqual(app.root.after_calls[-1][0], 250)

    def test_missed_button_release_is_recovered_by_visibility_poll(self):
        for mode in ("peek", "desktop", "topmost"):
            with self.subTest(mode=mode):
                app = self.make_app()
                app.peek_visible = mode == "peek"
                app.peek_enabled = mode == "peek"
                app.topmost = mode == "topmost"
                app._drag_active = True
                app._desktop_foreground = lambda: False
                app.end_drag = mock.Mock()
                with mock.patch.object(overlay.user32, "GetAsyncKeyState", return_value=0):
                    app._poll_desktop_visibility()
                app.end_drag.assert_called_once_with(None)
                self.assertEqual(app.root.after_calls[-1][0], 250)

    def test_edge_poll_does_not_start_peek_during_desktop_drag(self):
        app = self.make_app()
        app.peek_visible = False
        app._drag_active = True
        app._is_desktop_at_cursor = lambda: False
        app._peek_show = mock.Mock()
        with mock.patch.object(overlay.user32, "GetCursorPos", side_effect=self.cursor_at(1919, 150)):
            app._poll_peek_edge()
        app._peek_show.assert_not_called()
        self.assertTrue(app.root.after_calls)

    def test_direct_peek_request_does_not_interrupt_desktop_drag(self):
        app = self.make_app()
        app.peek_visible = False
        app._drag_active = True
        app._is_desktop_at_cursor = lambda: False
        app._detach_from_desktop = mock.Mock(return_value=True)
        with (
            mock.patch.object(overlay, "_set_window_cloaked"),
            mock.patch.object(overlay, "_show_without_reordering"),
            mock.patch.object(overlay.user32, "ShowWindow"),
        ):
            app._peek_show(app._monitor_areas[0])
        app._detach_from_desktop.assert_not_called()
        self.assertFalse(app._peek_animating)

    def test_duplicate_release_cannot_cancel_slide_started_by_first_release(self):
        app = self.make_app()
        app._peek_hide = overlay.OverlayApp._peek_hide.__get__(app)
        app.start_drag(SimpleNamespace(x=10, y=10, x_root=1730, y_root=150))
        app._dragged = True
        with (
            mock.patch.object(overlay, "_get_monitor_areas", return_value=app._monitor_areas),
            mock.patch.object(overlay.user32, "GetCursorPos", side_effect=self.cursor_at(900, 900)),
        ):
            app.end_drag(None)
            self.assertTrue(app._peek_animating)
            next_tick = app.root.after_calls[-1][1]
            position = (app.root.x, app.root.y)
            app.end_drag(None)
            next_tick()
        self.assertNotEqual((app.root.x, app.root.y), position)
        app._save_config.assert_called_once()

    def test_click_during_slide_in_settles_preview_without_moving_desktop_position(self):
        app = self.make_app()
        app.peek_visible = False
        app._peek_animating = True
        app.root.x = 1860
        app.start_drag(SimpleNamespace(x=10, y=10, x_root=1870, y_root=150))
        with mock.patch.object(overlay.user32, "GetCursorPos", side_effect=self.cursor_at(1870, 150)):
            app.end_drag(None)
        self.assertTrue(app.peek_visible)
        self.assertLessEqual(app.root.x + app.root.width, 1914)
        self.assertEqual(app._saved_pos, (120, 140))
        self.assertEqual((app.config["x"], app.config["y"]), (120, 140))

    def test_peek_leaves_a_clear_corridor_for_underlying_edge_controls(self):
        for work_right, overlay_width in ((1920, 200), (1880, 200), (160, 100)):
            with self.subTest(work_right=work_right, overlay_width=overlay_width):
                app = self.make_app()
                app.peek_visible = False
                app.root.width = overlay_width
                app._is_desktop_at_cursor = lambda: False
                app._detach_from_desktop = lambda: True
                app._animate_slide = mock.Mock()
                area = ((0, 0, 1920, 1080), (0, 0, work_right, 1040))
                with (
                    mock.patch.object(overlay, "_set_window_cloaked"),
                    mock.patch.object(overlay, "_show_without_reordering"),
                    mock.patch.object(overlay.user32, "ShowWindow"),
                ):
                    app._peek_show(area)
                target_x = app._animate_slide.call_args.args[1]
                self.assertGreaterEqual(target_x, 0)
                self.assertLessEqual(target_x + overlay_width, work_right - 6)

    def test_returning_to_edge_during_slide_out_is_not_lost(self):
        app = self.make_app()
        app._peek_animating = True
        app._is_desktop_at_cursor = lambda: False
        app._peek_show = mock.Mock()
        with (
            mock.patch.object(overlay.user32, "GetCursorPos", side_effect=self.cursor_at(1919, 150)),
            mock.patch.object(overlay, "_set_window_cloaked"),
            mock.patch.object(overlay.user32, "ShowWindow"),
        ):
            app._poll_peek_edge()
            app._peek_show.assert_not_called()
            app._peek_hidden()
            app._poll_peek_edge()
        app._peek_show.assert_called_once_with(app._monitor_areas[0])


if __name__ == "__main__":
    unittest.main()
