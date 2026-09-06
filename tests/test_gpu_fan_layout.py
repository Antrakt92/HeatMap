from unittest.mock import Mock

from gpu_fans import mode_text
from test_ui_layout import TkTestCase, layout_app


class GpuFanLayoutTests(TkTestCase):
    def test_gpu_startup_failure_is_visible_before_first_sensor_sample(self):
        with layout_app() as app:
            app.sensor_data = {}
            app.gpu_fan_worker = Mock()
            app.gpu_fan_worker.poll.return_value = dict(state='error', reason='Recovery conflict')
            app._check_alerts = Mock()
            app.update_ui()
            self.assertIn('GPU fans: Recovery conflict', app.health_messages)
            self.assertEqual(app.health_label.cget('fg'), '#f87171')
            app._check_alerts.assert_called_once_with({})

    def test_cooling_row_fits_at_supported_scales_and_survives_sensor_failure(self):
        for scaling in (1.333, 2.0, 2.666):
            with self.subTest(scaling=scaling), layout_app(scaling) as app:
                status = dict(state='active', command_pct=100, reason='Hotspot')
                label = app.rows['gpu_fan_control']
                label.config(text=mode_text(status))
                app._fit_content()
                app.root.update_idletasks()
                # Withdrawn Tk windows retain their old allocated widths. Compare
                # the complete row request with the newly configured viewport.
                self.assertLessEqual(label.master.master.winfo_reqwidth(),
                                     int(app.canvas.itemcget(app._content_window, 'width')))
                app._show_sensor_error()
                self.assertEqual(label.cget('text'), 'AUTO 100%+ · Hotspot')

    def test_normal_window_close_stops_gpu_owner(self):
        with layout_app() as app:
            app.gpu_fan_worker = Mock()
            app.root.destroy = Mock()
            app.quit()
            app.gpu_fan_worker.stop.assert_called_once()
            # The fixture performs the actual interpreter destruction.
            del app.root.destroy
