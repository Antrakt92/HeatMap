"""Mapped, transparent offscreen Tk regressions; no real overlay or hardware."""
from contextlib import contextmanager
import unittest
from unittest import mock

import overlay
from test_ui_layout import layout_app


@contextmanager
def mapped_layout_app(scaling=1.333, width=1280, height=900):
    # Tk does not propagate Canvas child geometry fully while withdrawn.
    # Mapping only this disposable, alpha-zero, no-activate HWND permits actual
    # clipping checks without showing UI or taking foreground from another app.
    origin = 20000
    area = ((origin, origin, origin + width, origin + height),) * 2
    with layout_app(scaling, height) as app:
        with mock.patch.object(overlay, "_get_monitor_areas", return_value=(area,)):
            app.config.update(x=origin, y=origin)
            app.root.geometry(f"+{origin}+{origin}")
            app.root.wm_attributes("-alpha", 0)
            overlay.set_tool_window(app._get_hwnd())
            app.root.deiconify()
            app._set_sensor_status(None)
            app._set_health_panel([], 0)
            app._fit_content()
            yield app, area


class LayoutAuditTests(unittest.TestCase):
    def test_sensor_error_growth_keeps_footer_inside_work_area(self):
        with mapped_layout_app() as (app, area):
            bottom = area[1][3]
            app.config["y"] = bottom - app.root.winfo_height()
            app.root.geometry(f"+{app.config['x']}+{app.config['y']}")
            app.root.update_idletasks()

            app._set_sensor_status(overlay.SENSOR_STATUS_STALE)
            app._show_sensor_error(text="--", color="#888888")
            app.root.update_idletasks()

            self.assertLessEqual(app.root.winfo_rooty() + app.root.winfo_height(), bottom)
            self.assertLessEqual(app.config["y"] + app.root.winfo_height(), bottom)

    def test_details_toggle_in_peek_refits_canvas_immediately(self):
        with mapped_layout_app(height=700) as (app, area):
            app.peek_visible = True
            app._peek_monitor_area = area
            app._saved_pos = (50, 50)
            old_region = app.canvas.cget("scrollregion")

            app.toggle_details()
            app.root.update_idletasks()

            self.assertNotEqual(app.canvas.cget("scrollregion"), old_region)
            self.assertEqual(app._saved_pos, (50, 50))
            self.assertEqual(app.root.winfo_rootx() + app.root.winfo_width(), area[1][2] - 6)
            self.assertLessEqual(app.root.winfo_height(), 700)

    def test_narrow_work_area_wraps_detail_value_instead_of_clipping(self):
        with mapped_layout_app(width=300) as (app, _area):
            app.details_enabled = True
            app._apply_details_visibility()
            value = app.rows["detail_board_temps"]
            value.configure(text="Sensor one 54 C | Sensor two 74 C | Sensor three 88 C | Sensor four 56 C")

            app._fit_content()

            self.assertLessEqual(app.root.winfo_width(), 300)
            self.assertGreaterEqual(value.winfo_width(), value.winfo_reqwidth())
            self.assertGreaterEqual(value.winfo_height(), value.winfo_reqheight())

    def test_detail_wrapping_recovers_after_moving_to_wider_monitor(self):
        with mapped_layout_app(width=300) as (app, area):
            app.details_enabled = True
            app._apply_details_visibility()
            value = app.rows["detail_board_temps"]
            value.configure(text="Sensor one 54 C | Sensor two 74 C | Sensor three 88 C | Sensor four 56 C " * 3)
            app._fit_content()
            narrow_height = value.winfo_height()
            narrow_width = app.root.winfo_width()
            left, top, _right, bottom = area[1]
            wider = ((left, top, left + 1280, bottom),) * 2

            with mock.patch.object(overlay, "_get_monitor_areas", return_value=(wider,)):
                app._fit_content()
                self.assertLess(value.winfo_height(), narrow_height)
                self.assertGreater(app.root.winfo_width(), narrow_width)
                size = (app.root.winfo_width(), app.root.winfo_height())
                for _ in range(3):
                    app._fit_content()
                    self.assertEqual((app.root.winfo_width(), app.root.winfo_height()), size)


if __name__ == "__main__":
    unittest.main()
