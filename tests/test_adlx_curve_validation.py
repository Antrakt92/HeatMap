"""ADLX fan mutation validation and optional activity metrics without hardware."""
from contextlib import nullcontext
import ctypes
import unittest
from unittest import mock

import amd_gpu_fan as native


POINTS = [[30, 23], [50, 35], [70, 60], [80, 75], [90, 100]]


class AdlxCurveValidationTests(unittest.TestCase):
    def adapter(self, *, error_index=-1, result=0):
        adapter = native.AmdGpuFan.__new__(native.AmdGpuFan)
        point = mock.MagicMock()
        point.__enter__.return_value = point
        point.call.return_value = 0
        states = mock.Mock(pointer=ctypes.c_void_p(1))
        states.child.return_value = point
        adapter.states = lambda: nullcontext(states)

        def fan_call(slot, _types, values):
            if slot == 6:
                ctypes.cast(values[1], ctypes.POINTER(ctypes.c_int)).contents.value = error_index
                return result
            return 0

        adapter.fan = mock.Mock()
        adapter.fan.call.side_effect = fan_call
        return adapter

    def test_successful_validation_with_invalid_index_never_applies_curve(self):
        for index in (-2, 0, 2, 100):
            with self.subTest(index=index):
                adapter = self.adapter(error_index=index)
                with self.assertRaisesRegex(native.AdlxError, "Invalid fan curve.*point"):
                    adapter.set_points(POINTS)
                self.assertEqual([call.args[0] for call in adapter.fan.call.call_args_list], [6])

    def test_native_validation_failure_never_applies_curve(self):
        adapter = self.adapter(result=12)
        with self.assertRaisesRegex(native.AdlxError, "ADLX error 12"):
            adapter.set_points(POINTS)
        self.assertEqual([call.args[0] for call in adapter.fan.call.call_args_list], [6])

    def test_valid_driver_response_applies_curve_after_validation(self):
        adapter = self.adapter()
        adapter.set_points(POINTS)
        self.assertEqual([call.args[0] for call in adapter.fan.call.call_args_list], [6, 7])


class AdlxOptionalLoadTests(unittest.TestCase):
    def adapter(self, load):
        adapter = native.AmdGpuFan.__new__(native.AmdGpuFan)
        adapter.gpu = mock.Mock(pointer=ctypes.c_void_p(1))
        metrics = mock.MagicMock()
        metrics.__enter__.return_value = metrics

        def get(slot, kind):
            if slot == 4:
                self.assertIs(kind, ctypes.c_double)
                if isinstance(load, Exception):
                    raise load
                return load
            return {3: 1234, 7: 48.0, 8: 65.0, 15: 67.0, 11: 1400}[slot]

        metrics.get.side_effect = get
        base = mock.MagicMock()
        base.__enter__.return_value = base
        base.query.return_value = metrics
        adapter.metrics_service = mock.Mock()
        adapter.metrics_service.child.return_value = base
        return adapter

    def test_valid_load_is_read_from_double_slot_four_without_control(self):
        for load in (0.0, 35.5, 100.0):
            with self.subTest(load=load):
                adapter = self.adapter(load)
                values = adapter.readings(strict=True)
                self.assertEqual(values["gpu_load"], load)
                self.assertEqual(values["gpu_core_temp"], 48)
                self.assertEqual(values["timestamp_ms"], 1234)

    def test_invalid_optional_load_never_blocks_temperature_readiness(self):
        for load in (None, False, "40", float("nan"), float("inf"), -1, 101,
                     native.AdlxError("activity unsupported", result=12),
                     native.AdlxError("activity inactive", result=14)):
            with self.subTest(load=load):
                values = self.adapter(load).readings(strict=True)
                self.assertIn("gpu_load", values)
                self.assertIsNone(values["gpu_load"])
                self.assertEqual((values["gpu_core_temp"], values["gpu_hotspot_temp"],
                                  values["gpu_memory_temp"], values["gpu_fan"]),
                                 (48, 65, 67, 1400))


if __name__ == "__main__":
    unittest.main()
