import unittest
from unittest import mock

import overlay
from test_overlay_helpers import _update_ui_app, _FakeLabel
from test_ui_layout import layout_app
import test_sensor_validation as sensor_fakes


class DisplayAuditTests(unittest.TestCase):
    def test_all_color_boundaries_and_invalid_readings(self):
        for metric, (warning, critical) in overlay._METRIC_THRESHOLDS.items():
            for value, expected in ((warning - 1, "#4ade80"), (warning, "#facc15"),
                                    (critical - 1, "#facc15"), (critical, "#f87171")):
                with self.subTest(metric=metric, value=value):
                    self.assertEqual(overlay._metric_color(value, (warning, critical)), expected)
            for value in (None, float("nan"), float("inf"), -1, True, "95"):
                with self.subTest(metric=metric, invalid=value):
                    self.assertEqual(overlay._metric_color(value, (warning, critical)), "#888888")

    def test_full_gpu_load_is_neutral_not_a_temperature_warning(self):
        for value in (0, 79, 80, 99, 100):
            self.assertEqual(overlay.load_color(value), "#cbd5e1")
        for value in (None, float("nan"), 101, True):
            self.assertEqual(overlay.load_color(value), "#888888")

    def test_cpu_reference_percentage_never_replaces_actual_duty(self):
        for rpm, duty, reference, expected in (
            (1800, 67, 2000, "1800 RPM | 67% ctl"),
            (1800, None, 2000, "1800 RPM · ~90% ref"),
            (2060, None, 2000, "2060 RPM · ~103% ref"),
            (0, None, 2000, "0 RPM · ~0% ref"),
            (None, None, 2000, "--"),
            (1800, None, None, "1800 RPM"),
            (1800, None, 0, "1800 RPM"),
        ):
            with self.subTest(rpm=rpm, duty=duty, reference=reference):
                self.assertEqual(overlay._format_cpu_fan(rpm, duty, reference), expected)

    def test_reference_configuration_rejects_bad_values_without_using_old_peak(self):
        for value in (float("nan"), float("inf"), True, -1, 10001, "2000"):
            cfg, errors = overlay._normalize_config({"cpu_fan_reference_rpm": value}, overlay._default_config())
            self.assertEqual(cfg["cpu_fan_reference_rpm"], 0)
            self.assertIn("cpu_fan_reference_rpm", errors)
        cfg, errors = overlay._normalize_config({"cpu_fan_max_rpm": 2637}, overlay._default_config())
        self.assertNotIn("cpu_fan_reference_rpm", cfg)
        self.assertFalse(errors)

    def test_muting_cancels_queued_sound_and_second_beep(self):
        for mute_before_start in (True, False):
            app = _update_ui_app()
            app.alerts_enabled = True
            app._last_alert_time = 0
            app._ALERT_COOLDOWN = 60
            with mock.patch.object(overlay.threading, "Thread") as worker:
                overlay.OverlayApp._check_alerts(app, {"cpu_temp": 95})
            callback = worker.call_args.kwargs["target"]
            if mute_before_start:
                app.alerts_enabled = False
            def mute(*_args):
                app.alerts_enabled = False
            with mock.patch.object(overlay.winsound, "Beep", side_effect=mute) as beep, mock.patch.object(overlay.time, "sleep"):
                callback()
            self.assertEqual(beep.call_count, 0 if mute_before_start else 1)

    def test_auxiliary_values_and_historical_peaks_do_not_claim_green_health(self):
        app = _update_ui_app()
        for key in ("detail_board_temps", "detail_peak_temps"):
            app.rows[key] = _FakeLabel()
        app.sensor_data = overlay._empty_sensor_data()
        app.sensor_data.update(cpu_temp=95, motherboard_temps=[{"name": "VRM", "temp": 120}])
        app.update_ui()
        for key in ("detail_board_temps", "detail_peak_temps"):
            self.assertEqual(app.rows[key].options["fg"], "#cbd5e1")

    def test_grouped_settings_keep_toggle_state_in_the_right_menu(self):
        with layout_app() as app:
            self.assertEqual(list(app.groups), ["CPU", "GPU", "CASE COOLING", "MEMORY & STORAGE"])
            self.assertEqual([app.menu.entrycget(i, "label") for i in range(4)],
                             ["Display", "Alerts & limits", "Cooling", "Diagnostics"])
            menu, index = app._menu_idx["alerts"]
            app.toggle_alerts()
            self.assertEqual(menu.entrycget(index, "label"), "Alerts: OFF")
            menu, index = app._menu_idx["cpu_reference"]
            with mock.patch.object(overlay.simpledialog, "askinteger", return_value=2000):
                app.configure_cpu_reference()
            self.assertEqual(app.config["cpu_fan_reference_rpm"], 2000)
            self.assertIn("2000 RPM", menu.entrycget(index, "label"))


class OptionalCpuFanTests(unittest.TestCase):
    setUp = sensor_fakes.SensorValidationTests.setUp
    hardware = sensor_fakes.SensorValidationTests.hardware
    read = sensor_fakes.SensorValidationTests.read

    def test_optional_fan_keeps_its_own_control_percentage(self):
        data = self.read(self.hardware("Motherboard", [
            ("CPU Fan", "Fan", 1800), ("CPU Optional Fan", "Fan", 1900),
            ("CPU Fan", "Control", 65), ("CPU Optional Fan", "Control", 72),
            ("System Fan #1", "Control", 100),
        ]))
        self.assertEqual(data["cpu_fan_pct"], 65)
        self.assertEqual(data["cpu_optional_fan_pct"], 72)


if __name__ == "__main__":
    unittest.main()
