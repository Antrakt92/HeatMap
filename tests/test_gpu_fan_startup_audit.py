import copy
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

import gpu_fans


class Adapter:
    def __init__(self, now):
        self.identity = {'name': 'test GPU'}
        self.state = {'points': [[30, 23], [50, 38], [56, 53], [63, 68], [70, 100]], 'zero_rpm': True}
        self.writes = []
        self.now = now
        self.close = Mock()

    def snapshot(self):
        return copy.deepcopy(self.state)

    def set_points(self, points):
        self.writes.append((self.now[0], 'curve', copy.deepcopy(points)))
        self.state['points'] = copy.deepcopy(points)

    def set_zero_rpm(self, enabled):
        self.writes.append((self.now[0], 'zero', enabled))
        self.state['zero_rpm'] = enabled


def sample(**extra):
    return dict(dict(gpu_core_temp=40, gpu_hotspot_temp=50, gpu_memory_temp=60,
                     gpu_fan=0, cpu_temp=100), **extra)


class GpuStartupAuditTests(unittest.TestCase):
    def run_worker(self, values, stop_at=40, commission=False, on_wait=None, stop_on_snapshot=None):
        now = [0]
        adapter = Adapter(now)
        baseline = adapter.snapshot()
        snapshots = [0]
        original_snapshot = adapter.snapshot

        def snapshot():
            snapshots[0] += 1
            if snapshots[0] == stop_on_snapshot:
                now[0] = stop_at
            return original_snapshot()

        adapter.snapshot = snapshot
        reports = []
        stop = Mock()
        stop.is_set.side_effect = lambda: now[0] >= stop_at

        def wait(seconds):
            now[0] += seconds
            if on_wait:
                on_wait(now[0], adapter)

        stop.wait.side_effect = wait
        owner = Mock()
        owner.create_time.return_value = 1
        owner.is_running.return_value = True
        heartbeat = Mock()
        heartbeat.expired.return_value = False

        def readings(strict=False):
            result = values(now[0], adapter)
            return dict(timestamp_ms=now[0] * 1000, **result) if 'timestamp_ms' not in result else result

        adapter.readings = readings
        with ExitStack() as stack:
            directory = stack.enter_context(tempfile.TemporaryDirectory())
            stack.enter_context(patch('amd_gpu_fan.AmdGpuFan', return_value=adapter))
            stack.enter_context(patch.object(gpu_fans.threading, 'Event', return_value=stop))
            stack.enter_context(patch.object(gpu_fans.threading, 'Thread'))
            stack.enter_context(patch.object(gpu_fans, 'OwnerHeartbeat', return_value=heartbeat))
            stack.enter_context(patch.object(gpu_fans.psutil, 'Process', return_value=owner))
            stack.enter_context(patch.object(gpu_fans.time, 'monotonic', side_effect=lambda: now[0]))
            stack.enter_context(patch.object(gpu_fans, 'require_hardware_access'))
            stack.enter_context(patch.object(gpu_fans, 'write_status', side_effect=lambda _path, state, **details:
                reports.append(dict(time=now[0], state=state, **details))))
            result = gpu_fans.worker(str(Path(directory) / 'status.json'), 1, 1, commission=commission)
            journal_exists = (Path(directory) / 'recovery.json').exists()
        adapter.close.assert_called_once()
        return result, adapter, baseline, reports, journal_exists

    def test_cold_zero_rpm_and_hot_cpu_never_write_gpu(self):
        result, adapter, baseline, reports, journal = self.run_worker(lambda *_: sample())
        self.assertEqual(result, 0)
        self.assertEqual(adapter.writes, [])
        self.assertEqual(adapter.snapshot(), baseline)
        self.assertFalse(journal)
        self.assertTrue(any(report['state'] == 'standby' for report in reports))
        self.assertFalse(reports[-1]['control_attempted'])

    def test_gpu_sensors_independently_activate_without_full_speed_pulse(self):
        for key, temperature in gpu_fans.ASSIST_ON.items():
            with self.subTest(key=key):
                result, adapter, baseline, reports, journal = self.run_worker(
                    lambda *_: sample(**{key: temperature, 'gpu_fan': 2000}), stop_at=9)
                expected, _ = gpu_fans.demand(sample(**{key: temperature}))
                commands = [report['command_pct'] for report in reports if report['state'] == 'active']
                self.assertEqual(result, 0)
                self.assertTrue(commands)
                self.assertTrue(all(command == expected for command in commands))
                self.assertEqual(adapter.snapshot(), baseline)
                self.assertFalse(journal)

    def test_full_gpu_heat_still_commands_100(self):
        result, _, _, reports, _ = self.run_worker(lambda *_: sample(gpu_hotspot_temp=90, gpu_fan=3000))
        self.assertEqual(result, 0)
        self.assertTrue(any(report.get('command_pct') == 100 for report in reports))

    def test_cooling_releases_original_curve_and_zero_rpm(self):
        result, adapter, baseline, reports, journal = self.run_worker(
            lambda now, _: sample(gpu_core_temp=70 if now < 7 else 40, gpu_fan=2000))
        self.assertEqual(result, 0)
        self.assertEqual(adapter.snapshot(), baseline)
        self.assertFalse(journal)
        released = [report for report in reports if report['state'] == 'standby' and report['time'] > 7]
        self.assertEqual(released[0]['time'], 17)
        self.assertTrue(reports[-1]['restore_confirmed'])
        self.assertEqual(adapter.writes[-1], (17, 'zero', True))

    def test_new_takeover_preserves_user_curve_changed_during_standby(self):
        modified = []

        def edit(now, adapter):
            if now == 21:
                adapter.state['points'][0][1] = 29
                modified.append(adapter.snapshot())

        result, adapter, _, _, journal = self.run_worker(lambda now, _: sample(
            gpu_core_temp=70 if now < 7 or now >= 25 else 40, gpu_fan=2000), on_wait=edit)
        self.assertEqual(result, 0)
        self.assertEqual(adapter.snapshot(), modified[0])
        self.assertFalse(journal)

    def test_missing_gpu_sensor_during_standby_never_causes_takeover(self):
        result, adapter, _, reports, _ = self.run_worker(lambda now, _: sample(
            gpu_hotspot_temp=50 if now < 5 else None))
        self.assertEqual(result, 0)
        self.assertEqual(adapter.writes, [])
        self.assertTrue(any(report.get('thermal_ready') is False for report in reports))

    def test_stale_standby_temperature_cannot_trigger_assistance(self):
        result, adapter, _, reports, _ = self.run_worker(lambda now, _: sample(
            gpu_hotspot_temp=50 if now < 3 else 110, timestamp_ms=min(now, 1) * 1000))
        self.assertEqual(result, 1)
        self.assertEqual(adapter.writes, [])
        self.assertIn('metrics stopped updating', reports[-1]['reason'])

    def test_missing_sensor_under_owned_hot_gpu_retains_failsafe(self):
        result, adapter, baseline, reports, _ = self.run_worker(lambda now, _: sample(
            gpu_core_temp=70, gpu_hotspot_temp=50 if now < 7 else None, gpu_fan=2000))
        self.assertEqual(result, 0)
        self.assertTrue(any(report.get('command_pct') == 100 for report in reports if report['time'] >= 7))
        self.assertEqual(adapter.snapshot(), baseline)

    def test_zero_rpm_has_spinup_grace_after_actual_thermal_takeover(self):
        result, _, _, reports, _ = self.run_worker(lambda now, _: sample(
            gpu_core_temp=70, gpu_fan=0 if now < 7 else 2000), stop_at=15)
        self.assertEqual(result, 0)
        self.assertTrue(all(report['command_pct'] == 85 for report in reports if report['state'] == 'active'))

    def test_explicit_commissioning_retains_full_airflow_validation(self):
        result, adapter, baseline, reports, _ = self.run_worker(
            lambda *_: sample(gpu_fan=3400), commission=True)
        self.assertEqual(result, 0)
        self.assertEqual(adapter.writes[0][2], gpu_fans.cooling_points(100, baseline['points']))
        self.assertTrue(any(report.get('verified_full_rpm') == 3400 for report in reports))
        self.assertEqual(adapter.snapshot(), baseline)

    def test_external_change_immediately_before_stop_is_preserved(self):
        external = []

        def edit(now, adapter):
            if now >= 9 and not external:
                adapter.state['points'][0][1] = 29
                external.append(adapter.snapshot())

        result, adapter, _, reports, journal = self.run_worker(
            lambda *_: sample(gpu_core_temp=70, gpu_fan=2000), stop_at=9, on_wait=edit)
        self.assertEqual(result, 1)
        self.assertEqual(adapter.snapshot(), external[0])
        self.assertTrue(journal)
        self.assertFalse(reports[-1]['restore_confirmed'])
        self.assertIn('External', reports[-1]['restore_errors'][0])

    def test_stop_during_native_snapshot_prevents_takeover(self):
        for snapshot_number in (1, 2):
            with self.subTest(snapshot_number=snapshot_number):
                result, adapter, _, reports, _ = self.run_worker(
                    lambda *_: sample(gpu_core_temp=70, gpu_fan=2000), stop_on_snapshot=snapshot_number)
                self.assertEqual(result, 0)
                self.assertEqual(adapter.writes, [])
                self.assertFalse(reports[-1]['control_attempted'])

    def test_journal_write_failure_never_claims_control_or_restoration(self):
        with patch.object(gpu_fans, 'replace_status_file', side_effect=OSError('disk full')):
            result, adapter, _, reports, _ = self.run_worker(
                lambda *_: sample(gpu_core_temp=70, gpu_fan=2000))
        self.assertEqual(result, 1)
        self.assertEqual(adapter.writes, [])
        self.assertFalse(reports[-1]['control_attempted'])
        self.assertFalse(reports[-1]['restore_confirmed'])


class GpuAssistPolicyTests(unittest.TestCase):
    def test_partial_sensor_loss_cannot_start_or_end_assistance(self):
        policy = gpu_fans.GpuAssistPolicy()
        self.assertFalse(policy.update(sample(gpu_hotspot_temp=None, gpu_core_temp=90), 0))
        self.assertTrue(policy.update(sample(gpu_core_temp=70), 1))
        self.assertTrue(policy.update(sample(gpu_hotspot_temp=None), 100))

    def test_cooling_hold_resets_after_reheat_or_missing_sensor(self):
        for extra in ({'gpu_hotspot_temp': 76}, {'gpu_memory_temp': None}):
            with self.subTest(extra=extra):
                policy = gpu_fans.GpuAssistPolicy()
                self.assertTrue(policy.update(sample(gpu_core_temp=70), 0))
                self.assertTrue(policy.update(sample(), 1))
                self.assertTrue(policy.update(sample(**extra), 9))
                self.assertTrue(policy.update(sample(), 10))
                self.assertTrue(policy.update(sample(), 19.9))
                self.assertFalse(policy.update(sample(), 20))

    def test_ramp_starts_at_demand_and_preserves_fractional_decay(self):
        ramp = gpu_fans.GpuRamp()
        self.assertEqual(ramp.update(70, 0), 70)
        self.assertEqual(ramp.update(30, 1), 70)
        self.assertEqual(ramp.update(30, 10.9), 70)
        self.assertEqual(ramp.update(30, 11), 70)
        self.assertEqual(ramp.update(30, 11.4), 70)
        self.assertEqual(ramp.update(30, 11.6), 69)
        self.assertEqual(ramp.update(90, 12), 90)

    def test_nonfinite_ramp_input_remains_failsafe(self):
        for target, now in ((None, 1), (70, float('nan')), (True, 1), (70, False)):
            with self.subTest(target=target, now=now):
                self.assertEqual(gpu_fans.GpuRamp().update(target, now), 100)

    def test_ramp_does_not_bank_elapsed_time_before_hold_ends(self):
        ramp = gpu_fans.GpuRamp(initial=100)
        self.assertEqual(ramp.update(30, 0), 100)
        self.assertEqual(ramp.update(30, 9), 100)
        self.assertEqual(ramp.update(30, 10), 100)
        self.assertEqual(ramp.update(30, 10.5), 99)
        self.assertEqual(ramp.update(30, 11), 98)

    def test_assistance_never_lowers_saved_driver_curve(self):
        baselines = (
            [[30, 23], [50, 38], [56, 53], [63, 68], [70, 100]],
            [[25, 30], [40, 90], [55, 60], [95, 80], [100, 100]],
            [[95, 50], [100, 50], [105, 50], [110, 50], [115, 50]],
            [[0, 100], [50, 20], [50, 90], [90, 50], [150, 80]],
        )
        for baseline in baselines:
            for floor in (30, 55, 85, 100):
                with self.subTest(baseline=baseline, floor=floor):
                    points = gpu_fans.cooling_points(floor, baseline)
                    for temperature in range(1, 151):
                        before = gpu_fans.interpolate(temperature, baseline)
                        after = gpu_fans.interpolate(temperature, points)
                        self.assertGreaterEqual(after, before)
                        self.assertGreaterEqual(after, floor)
                        if temperature >= 90:
                            self.assertEqual(after, 100)


if __name__ == '__main__':
    unittest.main()
