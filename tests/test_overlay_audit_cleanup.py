import sys
import unittest
from types import SimpleNamespace
from unittest import mock

import overlay
from hardware_access_guard import HardwareAccessConflict
from test_overlay_helpers import _FakeHardware, _FakeSensor, _fake_lhm_modules, _memory
from test_sensor_lifecycle import sensor_app


class OverlaySensorOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.modules, self.hardware_type, self.sensor_type = _fake_lhm_modules()

    def hardware(self, kind, name, readings):
        sensors = []
        for label, sensor_type, value, identifier in readings:
            sensor = _FakeSensor(label, getattr(self.sensor_type, sensor_type), value)
            sensor.Identifier = identifier
            sensors.append(sensor)
        return _FakeHardware(name, getattr(self.hardware_type, kind), sensors=sensors)

    def read(self, *hardware):
        with (
            mock.patch.dict(sys.modules, self.modules),
            mock.patch.object(overlay.psutil, "cpu_percent", return_value=12),
            mock.patch.object(overlay.psutil, "virtual_memory", return_value=_memory(25, 2, 8)),
        ):
            return overlay.read_sensors(SimpleNamespace(Hardware=list(hardware)))

    def test_cpu_duty_is_not_borrowed_from_another_board(self):
        data = self.read(
            self.hardware("Motherboard", "First", [("CPU Fan", "Fan", 1200, "/first/fan/0")]),
            self.hardware("Motherboard", "Second", [("CPU Fan", "Control", 85, "/second/control/0")]),
        )
        self.assertEqual(data["cpu_fan"], 1200)
        self.assertEqual(data["cpu_fan_id"], "/first/fan/0")
        self.assertIsNone(data["cpu_fan_pct"])

    def test_valid_cpu_tach_keeps_its_own_identity_after_missing_same_name(self):
        data = self.read(
            self.hardware("Motherboard", "First", [("CPU Fan", "Fan", None, "/first/fan/0")]),
            self.hardware("Motherboard", "Second", [
                ("CPU Fan", "Fan", 1200, "/second/fan/0"),
                ("CPU Fan", "Control", 55, "/second/control/0"),
            ]),
        )
        self.assertEqual(data["cpu_fan"], 1200)
        self.assertEqual(data["cpu_fan_id"], "/second/fan/0")
        self.assertEqual(data["cpu_fan_pct"], 55)

    def test_cpu_control_uses_exact_channel_when_board_chips_repeat_names(self):
        for name, value_key in (("CPU Fan", "cpu_fan_pct"), ("CPU Optional Fan", "cpu_optional_fan_pct")):
            for matching_duty in (None, 55):
                with self.subTest(name=name, matching_duty=matching_duty):
                    readings = [
                        (name, "Fan", 1200, "/first/fan/0"),
                        (name, "Control", 85, "/second/control/0"),
                    ]
                    if matching_duty is not None:
                        readings.append((name, "Control", matching_duty, "/first/control/0"))
                    data = self.read(self.hardware("Motherboard", "Board", readings))
                    self.assertEqual(data[value_key], matching_duty)

    def test_gpu_memory_percent_survives_unavailable_capacity(self):
        for name in ("GPU Memory", " gpu_memory "):
            with self.subTest(name=name):
                data = self.read(self.hardware("GpuNvidia", "GPU", [
                    (name, "Load", 98, "/gpu/load/memory"),
                    ("GPU Memory Controller", "Load", 14, "/gpu/load/controller"),
                ]))
                self.assertEqual(data["gpu_vram_pct"], 98)
                self.assertIsNone(data["gpu_vram_used_gb"])
                self.assertIsNone(data["gpu_load"])

    def test_amd_memory_activity_is_not_vram_capacity_usage(self):
        data = self.read(self.hardware("GpuAmd", "GPU", [
            ("GPU Memory", "Load", 99, "/gpu/load/memory"),
        ]))
        self.assertIsNone(data["gpu_vram_pct"])

    def test_capacity_ratio_overrides_load_fallback_and_invalid_loads_stay_missing(self):
        data = self.read(self.hardware("GpuNvidia", "GPU", [
            ("GPU Memory", "Load", 98, "/gpu/load/memory"),
            ("GPU Memory Used", "SmallData", 1024, "/gpu/memory/used"),
            ("GPU Memory Total", "SmallData", 4096, "/gpu/memory/total"),
        ]))
        self.assertEqual(data["gpu_vram_pct"], 25)
        for value in (None, -1, 100.1, float("nan")):
            with self.subTest(value=value):
                data = self.read(self.hardware("GpuNvidia", "GPU", [
                    ("GPU Memory", "Load", value, "/gpu/load/memory"),
                ]))
                self.assertIsNone(data["gpu_vram_pct"])


class OverlayPauseCleanupTests(unittest.TestCase):
    def test_pause_stops_other_controller_when_one_heartbeat_close_fails(self):
        for failed_worker in ("fan_worker", "gpu_fan_worker"):
            with self.subTest(failed_worker=failed_worker):
                computer = mock.Mock()
                app = sensor_app(1, computer)
                app.fan_worker = mock.Mock()
                app.gpu_fan_worker = mock.Mock()
                getattr(app, failed_worker).stop.side_effect = ValueError("heartbeat already closed")
                with (
                    mock.patch.object(overlay, "require_hardware_access", side_effect=HardwareAccessConflict("cpuz.exe")),
                    mock.patch.object(overlay.psutil, "cpu_percent"),
                    mock.patch.object(overlay, "log"),
                ):
                    app.sensor_loop()
                app.fan_worker.stop.assert_called_once_with()
                app.gpu_fan_worker.stop.assert_called_once_with()
                computer.Close.assert_called_once_with()
                self.assertIsNone(app.computer)
                self.assertIn("cpuz.exe", app._hardware_pause_reason)


if __name__ == "__main__":
    unittest.main()
