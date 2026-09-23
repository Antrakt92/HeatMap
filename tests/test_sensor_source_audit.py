import sys
import unittest
from types import SimpleNamespace
from unittest import mock

import overlay
from test_overlay_helpers import _FakeHardware, _FakeSensor, _fake_lhm_modules, _memory


class SensorSourceAuditTests(unittest.TestCase):
    def setUp(self):
        self.modules, self.hw, self.sensor = _fake_lhm_modules()

    def hardware(self, name, kind, readings):
        return _FakeHardware(name, kind, sensors=[
            _FakeSensor(label, getattr(self.sensor, sensor_type), value)
            for label, sensor_type, value in readings
        ])

    def read(self, hardware):
        with (mock.patch.dict(sys.modules, self.modules),
              mock.patch.object(overlay.psutil, 'cpu_percent', return_value=15),
              mock.patch.object(overlay.psutil, 'virtual_memory',
                                return_value=_memory(percent=25, used_gb=2, total_gb=8))):
            return overlay.read_sensors(SimpleNamespace(Hardware=hardware))

    def test_cpu_package_is_not_replaced_by_hotter_ccd(self):
        cpu = self.hardware('Ryzen', self.hw.Cpu, [
            ('Core (Tctl/Tdie)', 'Temperature', 60), ('CCD1 (Tdie)', 'Temperature', 75)])
        self.assertEqual(self.read([cpu])['cpu_temp'], 60)

    def test_cpu_clock_does_not_mix_reported_and_effective_clocks(self):
        cpu = self.hardware('Ryzen', self.hw.Cpu, [
            ('Core #1', 'Clock', 3500), ('Core #1 (Effective)', 'Clock', 4000)])
        self.assertEqual(self.read([cpu])['cpu_clock'], 3500)

    def test_ram_percentage_and_capacity_share_windows_snapshot(self):
        memory = self.hardware('Memory', self.hw.Memory, [('Memory', 'Load', 77)])
        data = self.read([memory])
        self.assertEqual((data['ram_pct'], data['ram_used_gb'], data['ram_total_gb']), (25, 2, 8))

    def test_vram_uses_complete_source_pair_independent_of_order(self):
        readings = [('GPU Memory Used', 'SmallData', 4096), ('GPU Memory Total', 'SmallData', 20480),
                    ('D3D Dedicated Memory Used', 'SmallData', 2048),
                    ('D3D Dedicated Memory Total', 'SmallData', 19456),
                    ('D3D Shared Memory Used', 'SmallData', 10000)]
        for order in (readings, list(reversed(readings))):
            with self.subTest(order=order):
                data = self.read([self.hardware('RX 7900 XT', self.hw.GpuAmd, order)])
                self.assertEqual((data['gpu_vram_used_gb'], data['gpu_vram_total_gb'], data['gpu_vram_pct']),
                                 (2, 19, 11))

    def test_incomplete_d3d_vram_does_not_mix_with_driver_total(self):
        data = self.read([self.hardware('RX 7900 XT', self.hw.GpuAmd, [
            ('GPU Memory Used', 'SmallData', 4096), ('GPU Memory Total', 'SmallData', 20480),
            ('D3D Dedicated Memory Used', 'SmallData', 2048)])])
        self.assertEqual((data['gpu_vram_used_gb'], data['gpu_vram_total_gb'], data['gpu_vram_pct']), (4, 20, 20))

    def test_discrete_gpu_selection_does_not_follow_temperature(self):
        for temp in (20, 95):
            igpu = self.hardware('AMD Radeon Graphics', self.hw.GpuAmd, [
                ('GPU Core', 'Temperature', temp), ('GPU Memory Total', 'SmallData', 512)])
            dgpu = self.hardware('AMD Radeon RX 7900 XT', self.hw.GpuAmd, [
                ('GPU Core', 'Temperature', 45), ('GPU Memory Total', 'SmallData', 20480),
                ('D3D 3D', 'Load', 6)])
            for order in ([igpu, dgpu], [dgpu, igpu]):
                data = self.read(order)
                self.assertEqual((data['gpu_core_temp'], data['gpu_load']), (45, 6))
