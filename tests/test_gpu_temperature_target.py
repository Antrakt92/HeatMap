import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from gpu_fans import GpuTemperatureTarget, GpuRamp, emergency_cooling
from test_gpu_fans import FakeAdapter
from tools.configure_gpu_curve import configure


def sample(**changes):
    return dict(dict(gpu_core_temp=60, gpu_hotspot_temp=80, gpu_memory_temp=80), **changes)


class TemperatureTargetTests(unittest.TestCase):
    def test_normal_rise_takes_seconds_and_emergency_bypasses_it(self):
        ramp = GpuRamp()
        self.assertEqual([ramp.update(85, now) for now in (0, 2, 4, 6)], [30, 50, 70, 85])
        self.assertEqual(ramp.update(100, 6, emergency=True), 100)

    def test_duplicate_samples_and_long_pause_cannot_bank_acceleration(self):
        ramp = GpuRamp(initial=30)
        self.assertEqual(ramp.update(100, 0), 30)
        self.assertEqual(ramp.update(100, 0), 30)
        self.assertEqual(ramp.update(100, 1000), 50)

    def test_soft_target_uplift_is_bounded_and_does_not_accumulate(self):
        control = GpuTemperatureTarget()
        data = sample(gpu_core_temp=64, gpu_hotspot_temp=84)
        requests = [control.update(data, now)[0] for now in range(0, 122, 2)]
        self.assertEqual(set(requests), {89})
        value, reason = control.update(data, 122)
        self.assertLess(value, 100)
        self.assertIn('target', reason)

    def test_target_band_clears_correction_without_retaining_previous_heat(self):
        control = GpuTemperatureTarget()
        control.update(sample(gpu_hotspot_temp=84), 0)
        self.assertEqual(control.update(sample(), 2)[0], 65)
        self.assertEqual(control.update(sample(gpu_core_temp=50, gpu_hotspot_temp=70,
                                             gpu_memory_temp=70), 4)[0], 49)

    def test_independent_hot_sensor_or_invalid_value_requests_full_without_delay(self):
        for key, limit in (('gpu_core_temp', 75), ('gpu_hotspot_temp', 90), ('gpu_memory_temp', 90)):
            for value in (limit, None, float('nan'), True):
                with self.subTest(key=key, value=value):
                    data = sample(**{key: value})
                    self.assertTrue(emergency_cooling(data))
                    self.assertEqual(GpuTemperatureTarget().update(data, 0)[0], 100)

    def test_reversed_control_clock_requests_full(self):
        control = GpuTemperatureTarget()
        control.update(sample(), 10)
        self.assertEqual(control.update(sample(), 9)[0], 100)


class DriverProfileTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.journal = Path(self.directory.name) / 'recovery.json'
        self.receipt = Path(self.directory.name) / 'profile.json'
        self.adapter = FakeAdapter()
        self.adapter.identity = dict(name='AMD Radeon RX 7900 XT', vendor='1002', device='744c',
                                     subsystem='240c', subvendor='1458')
        self.adapter.temperature_range = [25, 100, 1]
        self.adapter.speed_range = [23, 100, 1]
        self.stamp = 0
        def readings(**_):
            self.stamp += 1
            return dict(sample(), timestamp_ms=self.stamp)
        self.adapter.readings = readings

    def test_preview_has_no_writes_and_application_preserves_zero_rpm_and_original_receipt(self):
        original = self.adapter.snapshot()
        preview = configure(self.adapter, self.journal, self.receipt, guard=Mock())
        self.assertEqual(preview['state'], 'preview')
        self.assertEqual(self.adapter.events, [])
        self.assertFalse(self.receipt.exists())
        report = configure(self.adapter, self.journal, self.receipt, apply=True, guard=Mock())
        self.assertEqual(report['state'], 'applied')
        self.assertEqual(report['original'], original)
        self.assertEqual(self.adapter.state['points'], [[30, 23], [50, 35], [70, 60], [80, 75], [90, 100]])
        self.assertTrue(self.adapter.state['zero_rpm'])
        self.assertFalse(self.journal.exists())

    def test_backup_failure_or_invalid_ranges_prevents_native_mutation(self):
        with patch('tools.configure_gpu_curve.save_receipt', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                configure(self.adapter, self.journal, self.receipt, apply=True, guard=Mock())
        self.assertEqual(self.adapter.events, [])
        self.adapter.temperature_range = [25, 85, 1]
        with self.assertRaises(RuntimeError):
            configure(self.adapter, self.journal, self.receipt, apply=True, guard=Mock())
        self.assertEqual(self.adapter.events, [])

    def test_failed_native_write_restores_and_preserves_error_receipt(self):
        original = self.adapter.snapshot()
        setter = self.adapter.set_points
        def fail_once(points):
            setter(points)
            self.adapter.set_points = setter
            raise RuntimeError('partial driver write')
        self.adapter.set_points = fail_once
        with self.assertRaisesRegex(RuntimeError, 'partial driver write'):
            configure(self.adapter, self.journal, self.receipt, apply=True, guard=Mock())
        self.assertEqual(self.adapter.snapshot(), original)
        self.assertFalse(self.journal.exists())

    def test_crash_after_profile_commit_cannot_restore_obsolete_curve(self):
        from gpu_fans import RecoveryJournal
        with patch.object(RecoveryJournal, 'clear', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                configure(self.adapter, self.journal, self.receipt, apply=True, guard=Mock())
        committed = self.adapter.snapshot()
        self.assertTrue(self.journal.exists())
        self.assertFalse(RecoveryJournal(self.journal, self.adapter.identity).recover(self.adapter))
        self.assertEqual(self.adapter.snapshot(), committed)

    def test_cancel_during_native_write_restores_previous_profile(self):
        original = self.adapter.snapshot()
        setter = self.adapter.set_points
        def cancel_once(points):
            setter(points)
            self.adapter.set_points = setter
            raise KeyboardInterrupt
        self.adapter.set_points = cancel_once
        with self.assertRaises(KeyboardInterrupt):
            configure(self.adapter, self.journal, self.receipt, apply=True, guard=Mock())
        self.assertEqual(self.adapter.snapshot(), original)
        self.assertFalse(self.journal.exists())

    def test_unknown_gpu_or_missing_temperature_cannot_receive_a_profile(self):
        self.adapter.identity['subsystem'] = 'other'
        with self.assertRaises(RuntimeError):
            configure(self.adapter, self.journal, self.receipt, apply=True, guard=Mock())
        self.adapter.identity['subsystem'] = '240c'
        self.adapter.readings = lambda **_: sample(gpu_hotspot_temp=None)
        with self.assertRaises(RuntimeError):
            configure(self.adapter, self.journal, self.receipt, apply=True, guard=Mock())
        self.assertEqual(self.adapter.events, [])

    def test_frozen_timestamp_prevents_profile_write(self):
        from tools.configure_gpu_curve import fresh_temperatures
        from startup_readiness import StartupNotReady
        self.adapter.readings = lambda **_: dict(sample(), timestamp_ms=1)
        with patch('tools.configure_gpu_curve.time.monotonic', side_effect=[0, 0, 0, 0, 7]):
            with self.assertRaises(StartupNotReady):
                fresh_temperatures(self.adapter, Mock())
        self.assertEqual(self.adapter.events, [])

    def test_new_heat_during_backup_preparation_prevents_lower_curve(self):
        reads = [0]
        def readings(**_):
            reads[0] += 1
            return dict(sample(gpu_hotspot_temp=90 if reads[0] >= 3 else 70), timestamp_ms=reads[0])
        self.adapter.readings = readings
        with self.assertRaisesRegex(RuntimeError, 'too hot'):
            configure(self.adapter, self.journal, self.receipt, apply=True, guard=Mock())
        self.assertEqual(self.adapter.events, [])

    def test_backward_or_invalid_timestamp_prevents_profile_write(self):
        from tools.configure_gpu_curve import fresh_temperatures
        for stamps in ((2, 1), (None,), (True,), (float('nan'),)):
            values = iter(stamps)
            self.adapter.readings = lambda **_: dict(sample(), timestamp_ms=next(values))
            with self.assertRaises(RuntimeError):
                fresh_temperatures(self.adapter, Mock())
            self.assertEqual(self.adapter.events, [])


if __name__ == '__main__':
    unittest.main()
