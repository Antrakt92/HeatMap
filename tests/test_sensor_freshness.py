import unittest
from unittest import mock

import overlay
from test_overlay_helpers import _sample_data, _update_ui_app


class SensorFreshnessTests(unittest.TestCase):
    def test_stalled_worker_hides_old_values_and_does_not_repeat_alerts(self):
        app = _update_ui_app()
        app.sensor_data = {**_sample_data(), "cpu_temp": 60}
        app._sensor_sample_time = 100.0
        app._check_alerts = mock.Mock()
        with mock.patch.object(overlay.time, "monotonic", return_value=111.0):
            app.update_ui()
        self.assertEqual(app._sensor_status, "stale")
        self.assertEqual(app.rows["cpu_temp"].options["text"], "--")
        self.assertEqual(app.rows["cpu_temp"].options["fg"], "#888888")
        app._check_alerts.assert_not_called()

    def test_new_sample_clears_stale_status_and_restores_readings(self):
        app = _update_ui_app()
        app.sensor_data = {**_sample_data(), "cpu_temp": 60}
        app._sensor_sample_time = 100.0
        with mock.patch.object(overlay.time, "monotonic", return_value=111.0):
            app.update_ui()
            app._sensor_sample_time = 111.0
            app.update_ui()
        self.assertIsNone(app._sensor_status)
        self.assertEqual(app.rows["cpu_temp"].options["text"], "60°C")

    def test_ten_second_boundary_is_still_fresh(self):
        app = _update_ui_app()
        app.sensor_data = {**_sample_data(), "cpu_temp": 60}
        app._sensor_sample_time = 100.0
        with mock.patch.object(overlay.time, "monotonic", return_value=110.0):
            app.update_ui()
        self.assertIsNone(app._sensor_status)
        self.assertEqual(app.rows["cpu_temp"].options["text"], "60°C")


if __name__ == "__main__":
    unittest.main()
