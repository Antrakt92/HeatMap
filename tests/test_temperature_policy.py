import itertools
import time
import unittest
from unittest import mock

import overlay
from test_overlay_helpers import _update_ui_app, _FakeLabel
import test_sensor_validation as sensor_fakes


class GpuSensorPolicyTests(unittest.TestCase):
    setUp = sensor_fakes.SensorValidationTests.setUp
    hardware = sensor_fakes.SensorValidationTests.hardware
    read = sensor_fakes.SensorValidationTests.read
    def test_core_is_not_replaced_by_hotspot_memory_or_auxiliary_sensor(self):
        readings = [
            ("GPU Core", "Temperature", 54),
            ("GPU Hot Spot", "Temperature", 110),
            ("GPU Memory", "Temperature", 74),
            ("GPU VR VDDC", "Temperature", 80),
        ]
        for ordered in itertools.permutations(readings):
            with self.subTest(order=ordered):
                data = self.read(self.hardware("GpuAmd", ordered))
                self.assertEqual(data["gpu_temp"], 54)
                self.assertEqual(data["gpu_temp_label"], "CORE")
                self.assertEqual(data["gpu_core_temp"], 54)
                self.assertEqual(data["gpu_hotspot_temp"], 110)
                self.assertEqual(data["gpu_memory_temp"], 74)

    def test_missing_core_is_not_replaced_with_a_different_kind_of_temperature(self):
        data = self.read(self.hardware("GpuAmd", [
            ("GPU Hot Spot", "Temperature", 108),
            ("GPU Memory", "Temperature", 74),
            ("GPU Liquid", "Temperature", 30),
        ]))
        self.assertIsNone(data["gpu_temp"])
        self.assertIsNone(data["gpu_temp_label"])
        self.assertEqual(data["gpu_hotspot_temp"], 108)

    def test_auxiliary_and_threshold_names_are_not_core_memory_or_hotspot(self):
        for name in ("GPU VR SoC", "GPU VR MVDD", "GPU PLX", "GPU Liquid",
                     "GPU VR Memory", "GPU Hot Spot Limit", "GPU Temperature Critical"):
            with self.subTest(name=name):
                self.assertIsNone(overlay._gpu_temperature_key(name))


class TemperaturePresentationTests(unittest.TestCase):
    def app(self, **values):
        app = _update_ui_app()
        app.sensor_data = overlay._empty_sensor_data()
        app.sensor_data.update(values)
        return app

    def test_main_memory_rows_show_capacity_and_usage_without_details(self):
        app = self.app(gpu_vram_used_gb=5.6, gpu_vram_total_gb=20.0, gpu_vram_pct=28,
                       ram_used_gb=25.6, ram_total_gb=31.9, ram_pct=80)
        app.update_ui()
        self.assertEqual(app.rows["vram"].options["text"], "5.6/20.0 GB · 28%")
        self.assertEqual(app.rows["ram_gb"].options["text"], "25.6/31.9 GB")
        self.assertEqual(app.rows["ram_pct"].options["fg"], "#facc15")
        app.sensor_data.update(gpu_vram_used_gb=None, gpu_vram_total_gb=None)
        app.update_ui()
        self.assertEqual(app.rows["vram"].options["text"], "28%")
        app.sensor_data["gpu_vram_pct"] = None
        app.update_ui()
        self.assertEqual(app.rows["vram"].options["text"], "--")

    def test_actual_game_sample_keeps_hotspot_red_and_core_green(self):
        app = self.app(cpu_temp=78, gpu_temp=54, gpu_core_temp=54,
                       gpu_hotspot_temp=110, gpu_memory_temp=74, gpu_load=100,
                       ram_pct=59)
        app.update_ui()
        for row, text, color in (
            ("cpu_temp", "78°C", "#facc15"),
            ("gpu_temp", "54°C", "#4ade80"),
            ("gpu_hotspot_temp", "110°C", "#f87171"),
            ("gpu_memory_temp", "74°C", "#4ade80"),
        ):
            with self.subTest(row=row):
                self.assertEqual(app.rows[row].options["text"], text)
                self.assertEqual(app.rows[row].options["fg"], color)
        self.assertNotEqual(app.rows["gpu_load"].options["fg"], "#f87171")

    def test_missing_and_stale_gpu_temperatures_clear_all_three_rows(self):
        app = self.app(gpu_temp=54, gpu_hotspot_temp=110, gpu_memory_temp=74)
        app.update_ui()
        app.sensor_data = overlay._empty_sensor_data()
        app.update_ui()
        for key in ("gpu_temp", "gpu_hotspot_temp", "gpu_memory_temp"):
            self.assertEqual(app.rows[key].options["text"], "--")
        app.sensor_data.update(gpu_temp=54, gpu_hotspot_temp=110, gpu_memory_temp=74)
        app.update_ui()
        app._sensor_sample_time = time.monotonic() - overlay.SENSOR_STALE_SECONDS - 1
        app.update_ui()
        for key in ("gpu_temp", "gpu_hotspot_temp", "gpu_memory_temp"):
            self.assertEqual(app.rows[key].options["text"], "--")

    def test_each_red_temperature_boundary_also_triggers_alert(self):
        for key, warning, critical in (
            ("cpu_temp", 70, 85), ("gpu_temp", 80, 90),
            ("gpu_hotspot_temp", 90, 105), ("gpu_memory_temp", 85, 100),
        ):
            for value, color, alert in (
                (warning - 1, "#4ade80", False), (warning, "#facc15", False),
                (critical - 1, "#facc15", False), (critical, "#f87171", True),
            ):
                with self.subTest(key=key, value=value):
                    app = self.app(**{key: value})
                    app.update_ui()
                    self.assertEqual(app.rows[key].options["fg"], color)
                    app.alerts_enabled = True
                    app._last_alert_time = 0
                    app._ALERT_COOLDOWN = 60
                    with mock.patch.object(overlay.threading, "Thread") as thread:
                        overlay.OverlayApp._check_alerts(app, app.sensor_data)
                    self.assertEqual(thread.called, alert)

    def test_hotspot_alert_survives_cool_or_missing_core_and_respects_cooldown(self):
        app = self.app(gpu_temp=54, gpu_hotspot_temp=108)
        app.alerts_enabled = True
        app._last_alert_time = 0
        app._ALERT_COOLDOWN = 60
        with mock.patch.object(overlay.threading, "Thread") as thread:
            overlay.OverlayApp._check_alerts(app, app.sensor_data)
            app.sensor_data["gpu_temp"] = None
            overlay.OverlayApp._check_alerts(app, app.sensor_data)
            self.assertEqual(thread.call_count, 1)
            app._last_alert_time = 0
            overlay.OverlayApp._check_alerts(app, app.sensor_data)
            self.assertEqual(thread.call_count, 2)

    def test_details_and_peak_keep_high_hotspot_visible(self):
        app = self.app(gpu_temp=54, gpu_core_temp=54, gpu_hotspot_temp=110, gpu_memory_temp=74)
        app.rows["detail_gpu_temps"] = _FakeLabel()
        app.update_ui()
        self.assertEqual(app.rows["detail_gpu_temps"].options["fg"], "#f87171")
        app.sensor_data.update(gpu_temp=60, gpu_hotspot_temp=85, gpu_memory_temp=70)
        app.update_ui()
        self.assertEqual(app.peaks["gpu_temp"], 60)
        self.assertEqual(app.peaks["gpu_hotspot_temp"], 110)
        self.assertIn("HOT 110°C", overlay._format_peak_temps(app.peaks))

    def test_known_samsung_disks_and_unknown_disk_have_matching_color_and_alert(self):
        for name, value, color, alert in (
            ("980 PRO 500GB", 61, "#facc15", False),
            ("Samsung SSD 860 EVO 1TB", 69, "#facc15", False),
            ("980 PRO 500GB", 70, "#f87171", True),
            ("860 EVO 1TB", 70, "#f87171", True),
            ("Unknown HDD", 55, "#f87171", True),
        ):
            with self.subTest(name=name, value=value):
                self.assertEqual(overlay.disk_temp_color(value, name), color)
                app = self.app(disks=[{"name": name, "temp": value}])
                app.alerts_enabled = True
                app._last_alert_time = 0
                app._ALERT_COOLDOWN = 60
                with mock.patch.object(overlay.threading, "Thread") as thread:
                    overlay.OverlayApp._check_alerts(app, app.sensor_data)
                self.assertEqual(thread.called, alert)

    def test_ram_and_disk_capacity_red_matches_alert_threshold(self):
        self.assertEqual(overlay.disk_usage_color(89), "#facc15")
        self.assertEqual(overlay.disk_usage_color(90), "#f87171")
        for value, color, alert in ((94, "#facc15", False), (95, "#f87171", True)):
            app = self.app(ram_pct=value)
            app.update_ui()
            self.assertEqual(app.rows["ram_pct"].options["fg"], color)
            app.alerts_enabled = True
            app._last_alert_time = 0
            app._ALERT_COOLDOWN = 60
            with mock.patch.object(overlay.threading, "Thread") as thread:
                overlay.OverlayApp._check_alerts(app, app.sensor_data)
            self.assertEqual(thread.called, alert)


if __name__ == "__main__":
    unittest.main()
