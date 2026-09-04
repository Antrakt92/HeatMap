import sys
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest import mock

import overlay
from test_overlay_helpers import _FakeHardware, _FakeSensor, _fake_lhm_modules, _memory
from test_sensor_lifecycle import sensor_app


class SensorRecoveryEdgeTests(unittest.TestCase):
    def setUp(self):
        self.modules, self.hardware_type, self.sensor_type = _fake_lhm_modules()
        contexts = ExitStack()
        self.addCleanup(contexts.close)
        contexts.enter_context(mock.patch.dict(sys.modules, self.modules))
        contexts.enter_context(mock.patch.object(overlay.psutil, "cpu_percent", return_value=12))
        contexts.enter_context(mock.patch.object(
            overlay.psutil, "virtual_memory",
            return_value=_memory(percent=25, used_gb=2, total_gb=8),
        ))
        contexts.enter_context(mock.patch.object(overlay, "log"))

    def hardware(self, kind, readings, update_error=None):
        return _FakeHardware(
            kind, getattr(self.hardware_type, kind),
            sensors=[
                _FakeSensor(name, getattr(self.sensor_type, sensor_type), value)
                for name, sensor_type, value in readings
            ],
            update_error=update_error,
        )

    def computer(self, gpu_error=None):
        return SimpleNamespace(
            Hardware=[
                self.hardware("Cpu", [("CPU Package", "Temperature", 45)]),
                self.hardware("GpuNvidia", [("GPU Core", "Temperature", 62)], gpu_error),
            ],
            Close=mock.Mock(),
        )

    def test_malformed_reading_preserves_valid_values_in_same_hardware(self):
        cases = (
            ("Cpu", ("CPU Total", "Load"), ("CPU Package", "Temperature", 45), "cpu_temp", 45),
            ("GpuNvidia", ("GPU Fan", "Fan"), ("GPU Core", "Temperature", 62), "gpu_temp", 62),
            ("GpuNvidia", ("GPU Memory Used", "SmallData"), ("GPU Core", "Load", 33), "gpu_load", 33),
            ("Motherboard", ("CPU Fan", "Control"), ("CPU Fan", "Fan", 1300), "cpu_fan", 1300),
            ("Storage", ("Used Space", "Load"), ("Temperature", "Temperature", 41), "disk_temp", 41),
        )
        for kind, invalid_sensor, valid_sensor, key, expected in cases:
            for malformed in ("N/A", object(), 10 ** 1000):
                for invalid_first in (False, True):
                    with self.subTest(kind=kind, key=key, malformed_type=type(malformed).__name__, invalid_first=invalid_first):
                        readings = [valid_sensor, (*invalid_sensor, malformed)]
                        if invalid_first:
                            readings.reverse()
                        data = overlay.read_sensors(SimpleNamespace(Hardware=[self.hardware(kind, readings)]))
                        actual = data["disks"][0]["temp"] if key == "disk_temp" and data["disks"] else data.get(key)
                        self.assertEqual(actual, expected)

    def test_persistent_hardware_failure_recovers_with_bounded_reopen_interval(self):
        old = self.computer(RuntimeError("GPU handle lost until reopen"))
        still_bad = self.computer(RuntimeError("GPU still unavailable"))
        recovered = self.computer()
        app = sensor_app(25, old)
        attempts = []

        def initialize():
            attempts.append(app._stop_event.now)
            return still_bad if len(attempts) == 1 else recovered

        with (
            mock.patch.object(overlay, "init_hardware_monitor", side_effect=initialize),
            mock.patch.object(overlay.time, "monotonic", side_effect=lambda: app._stop_event.now),
        ):
            app.sensor_loop()

        self.assertEqual(len(attempts), 2)
        self.assertGreaterEqual(attempts[1] - attempts[0], overlay.SENSOR_INIT_RETRY_SECONDS)
        self.assertEqual(app.sensor_data["gpu_temp"], 62)
        self.assertEqual(app.sensor_data["cpu_temp"], 45)
        self.assertNotIn(overlay.SENSOR_REINIT_KEY, app.sensor_data)
        for computer in (old, still_bad, recovered):
            computer.Close.assert_called_once_with()

    def test_single_transient_hardware_failure_does_not_reopen_monitor(self):
        computer = self.computer()
        gpu = computer.Hardware[1]
        gpu.Update = mock.Mock(side_effect=[RuntimeError("temporary GPU read"), None, None, None])
        app = sensor_app(4, computer)
        with (
            mock.patch.object(overlay, "init_hardware_monitor") as initialize,
            mock.patch.object(overlay.time, "monotonic", side_effect=lambda: app._stop_event.now),
        ):
            app.sensor_loop()

        initialize.assert_not_called()
        self.assertEqual(app.sensor_data["gpu_temp"], 62)
        computer.Close.assert_called_once_with()

    def test_failed_storage_cannot_publish_cached_success_while_waiting_for_recovery(self):
        computers = [self.computer() for _ in range(3)]
        for index, computer in enumerate(computers):
            computer.Hardware.append(self.hardware(
                "Storage", [("Temperature", "Temperature", 41 if index < 2 else 44)],
                RuntimeError("storage update fails until reopen") if index < 2 else None,
            ))
        app = sensor_app(25, computers[0])
        attempts = []
        samples = []
        original_read = overlay.read_sensors

        def initialize():
            attempts.append(app._stop_event.now)
            return computers[min(len(attempts), 2)]

        def read(computer, update_storage=True):
            data = original_read(computer, update_storage=update_storage)
            # Keep the same object published by sensor_loop, including its status policy.
            samples.append((computer, update_storage, data))
            return data

        with (
            mock.patch.object(overlay, "init_hardware_monitor", side_effect=initialize),
            mock.patch.object(overlay, "read_sensors", side_effect=read),
            mock.patch.object(overlay.time, "monotonic", side_effect=lambda: app._stop_event.now),
        ):
            app.sensor_loop()

        failed_samples = [sample for sample in samples if sample[0] is not computers[2]]
        self.assertTrue(any(not updated for _computer, updated, _data in failed_samples))
        for _computer, updated, data in failed_samples:
            with self.subTest(storage_updated=updated):
                self.assertEqual(data["disks"], [])
                self.assertEqual(data[overlay.SENSOR_STATUS_KEY], overlay.SENSOR_STATUS_PARTIAL)
                self.assertTrue(data[overlay.SENSOR_REINIT_KEY])
                self.assertEqual(data["cpu_temp"], 45)

        self.assertEqual(len(attempts), 2)
        self.assertGreaterEqual(attempts[1] - attempts[0], overlay.SENSOR_INIT_RETRY_SECONDS)
        self.assertEqual(app.sensor_data["disks"][0]["temp"], 44)
        self.assertNotIn(overlay.SENSOR_REINIT_KEY, app.sensor_data)
        for computer in computers:
            computer.Close.assert_called_once_with()

    def test_fractional_fan_maximum_cannot_normalize_to_zero(self):
        defaults = overlay._default_config()
        for key in ("gpu_fan_max_rpm", "cpu_fan_max_rpm"):
            for value in (0.01, 0.5, 0.99):
                with self.subTest(key=key, value=value):
                    config, invalid_keys = overlay._normalize_config({key: value}, defaults)
                    self.assertEqual(config[key], defaults[key])
                    self.assertIn(key, invalid_keys)

        config, invalid_keys = overlay._normalize_config({"gpu_fan_max_rpm": 1}, defaults)
        self.assertEqual(config["gpu_fan_max_rpm"], 1)
        self.assertEqual(invalid_keys, [])


if __name__ == "__main__":
    unittest.main()
