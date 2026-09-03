import math
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

import overlay
from test_overlay_helpers import _FakeHardware, _FakeSensor, _fake_lhm_modules, _memory


class SensorValidationTests(unittest.TestCase):
    def setUp(self):
        self.modules, self.hardware_type, self.sensor_type = _fake_lhm_modules()

    def hardware(self, kind, readings, name=None):
        return _FakeHardware(
            name or kind,
            getattr(self.hardware_type, kind),
            sensors=[
                _FakeSensor(label, getattr(self.sensor_type, sensor_type), value)
                for label, sensor_type, value in readings
            ],
        )

    def read(self, *hardware):
        with (
            mock.patch.dict(sys.modules, self.modules),
            mock.patch.object(overlay.psutil, "cpu_percent", return_value=12),
            mock.patch.object(
                overlay.psutil, "virtual_memory",
                return_value=_memory(percent=25, used_gb=2, total_gb=8),
            ),
        ):
            return overlay.read_sensors(SimpleNamespace(Hardware=list(hardware)))

    def percentage_sample(self, value):
        return self.read(
            self.hardware("Cpu", [
                ("CPU Total", "Load", value),
                ("CPU Package", "Temperature", 45),
            ]),
            self.hardware("Memory", [("Memory", "Load", value)], "Physical Memory"),
            self.hardware("GpuNvidia", [
                ("GPU Core", "Load", value),
                ("GPU Core", "Temperature", 55),
                ("GPU Fan", "Control", value),
            ]),
            self.hardware("Motherboard", [
                ("CPU Fan", "Fan", 1300),
                ("CPU", "Control", value),
            ]),
            self.hardware("Storage", [("Used Space", "Load", value)]),
        )

    def test_invalid_raw_percentages_do_not_replace_valid_fallbacks(self):
        for value in (-1, -0.1, 100.1, 101, 255, math.nan, math.inf):
            with self.subTest(value=value):
                data = self.percentage_sample(value)
                self.assertEqual(data["cpu_load"], 12)
                self.assertEqual(data["ram_pct"], 25)
                self.assertIsNone(data["gpu_load"])
                self.assertIsNone(data["gpu_fan_pct"])
                self.assertIsNone(data["cpu_fan_pct"])
                self.assertIsNone(data["disks"][0]["used_pct"])

    def test_valid_percentage_boundaries_and_rounding_are_preserved(self):
        for value, expected in ((0, 0), (100, 100), (33.4, 33)):
            with self.subTest(value=value):
                data = self.percentage_sample(value)
                for key in ("cpu_load", "ram_pct", "gpu_load", "gpu_fan_pct", "cpu_fan_pct"):
                    self.assertEqual(data[key], expected, key)
                self.assertEqual(data["disks"][0]["used_pct"], expected)

    def test_invalid_percentages_cannot_trigger_critical_alerts(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.alerts_enabled = True
        app._last_alert_time = 0
        app._ALERT_COOLDOWN = 60
        app._CRITICAL = {"cpu_temp": 85, "gpu_temp": 90, "ram_pct": 95, "disk_temp": 55, "disk_used": 90}
        with (
            mock.patch.object(overlay.time, "time", return_value=1000),
            mock.patch.object(overlay.threading, "Thread") as thread,
        ):
            app._check_alerts(self.percentage_sample(255))
        thread.assert_not_called()

    def test_negative_fan_speed_and_gpu_clock_are_unavailable(self):
        for value in (-1, -0.1, math.nan, math.inf):
            with self.subTest(value=value):
                data = self.read(
                    self.hardware("GpuNvidia", [
                        ("GPU Fan", "Fan", value), ("GPU Core", "Clock", value),
                    ]),
                    self.hardware("Motherboard", [("CPU Fan", "Fan", value)]),
                )
                self.assertIsNone(data["gpu_fan"])
                self.assertIsNone(data["cpu_fan"])
                self.assertIsNone(data["gpu_clock"])

    def test_fan_and_clock_values_are_not_limited_to_percentages(self):
        for value in (0, 12000):
            with self.subTest(value=value):
                data = self.read(
                    self.hardware("GpuNvidia", [
                        ("GPU Fan", "Fan", value), ("GPU Core", "Clock", value),
                    ]),
                    self.hardware("Motherboard", [("CPU Fan", "Fan", value)]),
                )
                self.assertEqual(data["gpu_fan"], value)
                self.assertEqual(data["cpu_fan"], value)
                self.assertEqual(data["gpu_clock"], value)

    def test_invalid_vram_pair_is_not_rendered_as_valid_usage(self):
        for used, total in ((-1, 8192), (-0.1, 8192), (8193, 8192), (512, 0), (512, -1), (math.inf, 8192)):
            with self.subTest(used=used, total=total):
                data = self.read(self.hardware("GpuNvidia", [
                    ("GPU Memory Used", "SmallData", used),
                    ("GPU Memory Total", "SmallData", total),
                ]))
                for key in ("gpu_vram_pct", "gpu_vram_used_gb", "gpu_vram_total_gb"):
                    self.assertIsNone(data[key], key)

    def test_valid_vram_zero_full_and_partial_usage_are_preserved(self):
        for used, expected in ((0, 0), (4096, 50), (8192, 100)):
            with self.subTest(used=used):
                data = self.read(self.hardware("GpuNvidia", [
                    ("GPU Memory Used", "SmallData", used),
                    ("GPU Memory Total", "SmallData", 8192),
                ]))
                self.assertEqual(data["gpu_vram_pct"], expected)
                self.assertEqual(data["gpu_vram_used_gb"], used / 1024)
                self.assertEqual(data["gpu_vram_total_gb"], 8)

    def test_remaining_life_rejects_invalid_percentages(self):
        for value in (-1, -0.1, 100.1, 255):
            with self.subTest(value=value):
                data = self.read(self.hardware("Storage", [("Remaining Life", "Level", value)]))
                self.assertNotIn("life_pct", data["disks"][0])

    def test_wear_percentage_can_exceed_one_hundred(self):
        for value, expected in ((0, 100), (50, 50), (100, 0), (120, 0), (255, 0)):
            with self.subTest(value=value):
                data = self.read(self.hardware("Storage", [("Percentage Used", "Level", value)]))
                self.assertEqual(data["disks"][0]["life_pct"], expected)

    def test_negative_wear_percentage_is_not_reported_as_healthy(self):
        for value in (-1, -0.1):
            with self.subTest(value=value):
                data = self.read(self.hardware("Storage", [("Percentage Used", "Level", value)]))
                self.assertNotIn("life_pct", data["disks"][0])

    def test_known_numbered_cpu_fan_does_not_use_another_fans_control(self):
        for control_name in ("Fan #1", "CPU Fan #1"):
            with self.subTest(control_name=control_name):
                data = self.read(self.hardware("Motherboard", [
                    ("CPU Fan #2", "Fan", 1300), (control_name, "Control", 0),
                ]))
                self.assertEqual(data["cpu_fan"], 1300)
                self.assertIsNone(data["cpu_fan_pct"])

    def test_known_numbered_cpu_fan_uses_matching_control(self):
        data = self.read(self.hardware("Motherboard", [
            ("CPU Fan #2", "Fan", 1300),
            ("Fan #1", "Control", 0), ("Fan #2", "Control", 66),
        ]))
        self.assertEqual(data["cpu_fan_pct"], 66)

    def test_cpu_temperature_uses_hottest_preferred_sensor_in_either_order(self):
        readings = [("CPU Package", "Temperature", 93), ("CPU Tctl", "Temperature", 65)]
        for ordered in (readings, list(reversed(readings))):
            with self.subTest(order=[reading[0] for reading in ordered]):
                data = self.read(self.hardware("Cpu", ordered))
                self.assertEqual(data["cpu_temp"], 93)

    def test_cpu_temperature_preserves_preferred_group_and_hottest_fallback(self):
        self.assertEqual(self.read(self.hardware("Cpu", [
            ("CPU Core #1", "Temperature", 93), ("CPU Package", "Temperature", 65),
        ]))["cpu_temp"], 65)
        for values in ((65, 93), (93, 65)):
            with self.subTest(values=values):
                data = self.read(self.hardware("Cpu", [
                    ("CPU Core #1", "Temperature", values[0]),
                    ("CPU Core #2", "Temperature", values[1]),
                ]))
                self.assertEqual(data["cpu_temp"], 93)

    def test_cpu_temperature_selection_is_stable_across_cpu_blocks(self):
        hot = self.hardware("Cpu", [("CPU Package", "Temperature", 93)], "CPU 0")
        cool = self.hardware("Cpu", [("CPU Package", "Temperature", 65)], "CPU 1")
        for ordered in ((hot, cool), (cool, hot)):
            with self.subTest(order=[hardware.Name for hardware in ordered]):
                self.assertEqual(self.read(*ordered)["cpu_temp"], 93)

    def test_empty_hardware_inventory_requests_retry_and_reports_fallback(self):
        data = self.read()
        self.assertEqual(data.get(overlay.SENSOR_STATUS_KEY), overlay.SENSOR_STATUS_PSUTIL_FALLBACK)
        self.assertTrue(data.get(overlay.SENSOR_REINIT_KEY))
        self.assertEqual(data["cpu_load"], 12)
        self.assertEqual(data["ram_pct"], 25)


if __name__ == "__main__":
    unittest.main()
