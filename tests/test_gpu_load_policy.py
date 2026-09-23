import unittest

import overlay
from test_overlay_helpers import _FakeHardware, _FakeSensor, _fake_lhm_modules


class GpuLoadPolicyTests(unittest.TestCase):
    def read(self, readings):
        _, hardware, sensor_type = _fake_lhm_modules()
        gpu = _FakeHardware('RX 7900 XT', hardware.GpuAmd, sensors=[
            _FakeSensor(name, sensor_type.Load, value) for name, value in readings
        ])
        candidates = {'gpus': []}
        overlay._read_hardware_block(gpu, hardware, sensor_type,
                                     overlay._empty_sensor_data(), candidates)
        return candidates['gpus'][0][1]['gpu_load']

    def test_observed_driver_and_windows_disagreement(self):
        readings = [('GPU Core', 35), ('D3D 3D', 5.44), ('D3D Copy', 0.2)]
        for ordered in (readings, list(reversed(readings))):
            self.assertEqual(self.read(ordered), 5)

    def test_busiest_engine_not_alphabetical_or_sum(self):
        for engine in ('D3D 3D', 'D3D Compute 0', 'D3D Copy', 'D3D Video Decode 1'):
            with self.subTest(engine=engine):
                self.assertEqual(self.read([
                    ('GPU Core', 99), ('D3D Video Codec', 4), (engine, 70),
                    ('D3D Dedicated Memory Used', 95), ('GPU Memory', 98),
                ]), 70)

    def test_idle_windows_does_not_fall_back_to_driver_activity(self):
        self.assertEqual(self.read([('GPU Core', 38), ('D3D 3D', 0)]), 0)

    def test_invalid_windows_values_fall_back_to_driver(self):
        for value in (None, float('nan'), float('inf'), -1, 101):
            with self.subTest(value=value):
                self.assertEqual(self.read([('GPU Core', 38), ('D3D 3D', value)]), 38)

    def test_missing_all_valid_loads_is_unavailable(self):
        self.assertIsNone(self.read([('D3D 3D', None), ('GPU Core', None)]))
