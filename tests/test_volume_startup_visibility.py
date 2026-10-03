"""Fresh capacity must remain visible even before native initialization completes."""
import unittest
from unittest import mock

import overlay
from test_overlay_helpers import _update_ui_app, _sample_data
from test_ui_layout import TkTestCase, layout_app


class VolumeStartupVisibilityTests(unittest.TestCase):
    def app(self):
        app = _update_ui_app()
        app.health_label = mock.Mock()
        app._volume_snapshot = (100, {'volumes': [
            {'name': 'C:', 'used_pct': 99, 'free_bytes': 2**30}], 'volume_errors': []})
        return app

    def test_fresh_capacity_and_warning_are_visible_before_first_sensor_sample(self):
        app = self.app()
        app.sensor_data = {}
        with mock.patch.object(overlay.time, 'monotonic', return_value=100):
            app.update_ui()
        self.assertEqual(app.rows['disk_0_usage'].options['text'], '99.0%')
        self.assertTrue(any('Volume C:' in message for message in app.health_messages))

    def test_hardware_access_pause_keeps_independent_capacity_and_pressure_warning(self):
        app = self.app()
        app.sensor_data = _sample_data()
        app._hardware_pause_reason = 'Sensors paused: driver installation'
        with mock.patch.object(overlay.time, 'monotonic', return_value=100):
            app.update_ui()
        self.assertEqual(app.rows['disk_0_usage'].options['text'], '99.0%')
        self.assertIn(app._hardware_pause_reason, app.health_messages)
        self.assertTrue(any('Volume C:' in message for message in app.health_messages))


class VolumeStartupNativeLayoutTests(TkTestCase):
    def test_waiting_for_sensors_keeps_capacity_and_warning_at_supported_scales(self):
        for scaling in (1.333, 2.0, 2.666):
            with self.subTest(scaling=scaling), layout_app(scaling) as app:
                app.sensor_data = {}
                app._volume_snapshot = (100, {'volumes': [
                    {'name': 'C:', 'used_pct': 99, 'free_bytes': 2**30}], 'volume_errors': []})
                with mock.patch.object(overlay.time, 'monotonic', return_value=100):
                    app.update_ui()
                self.assertEqual(app.rows['disk_0_usage'].cget('text'), '99.0%')
                self.assertIn('Volume C:', app.health_label.cget('text'))
                self.assertLessEqual(app.rows['disk_0_usage'].master.winfo_reqwidth(),
                                     int(app.canvas.itemcget(app._content_window, 'width')))


if __name__ == '__main__':
    unittest.main()
