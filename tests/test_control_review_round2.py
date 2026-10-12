"""Independent regressions from the second thermal-control review."""
import math
import json
import tempfile
import unittest
from collections import deque
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

import gpu_fans
from gpu_fans import (BALANCED_DRIVER_POINTS, GpuRamp, GpuTemperatureTarget, RecoveryJournal,
                      cooling_points, emergency_cooling)
from thermal_policy import interpolate
import test_gpu_fan_startup_audit
from test_gpu_fans import FakeAdapter
from tools.configure_gpu_curve import configure


def temperatures(core=60, hotspot=80, memory=80):
    return dict(gpu_core_temp=core, gpu_hotspot_temp=hotspot, gpu_memory_temp=memory)


class ControlClockSafetyTests(unittest.TestCase):
    def test_reversed_time_cannot_spend_a_negative_acceleration_budget(self):
        ramp = GpuRamp(initial=80)
        self.assertEqual(ramp.update(80, 10), 80)
        self.assertEqual(ramp.update(100, 9), 100)
        self.assertEqual(ramp.update(100, 9), 100)

    def test_feedback_clock_fault_stays_full_through_the_command_ramp(self):
        data = temperatures()
        feedback, ramp = GpuTemperatureTarget(), GpuRamp(initial=80)
        requested, _ = feedback.update(data, 10)
        ramp.update(requested, 10)
        requested, reason = feedback.update(data, 9)
        self.assertEqual(requested, 100)
        self.assertIn('clock', reason.lower())
        self.assertEqual(ramp.update(requested, 9), 100)


class BoundedTargetCorrectionTests(unittest.TestCase):
    def test_moderate_constant_heat_does_not_integrate_into_full_speed(self):
        feedback = GpuTemperatureTarget()
        requests = [feedback.update(temperatures(64, 80, 75), now)[0]
                    for now in range(0, 122, 2)]
        self.assertGreaterEqual(min(requests), 65)
        self.assertLessEqual(max(requests), 75)
        self.assertEqual(requests[0], requests[-1])

    def test_past_emergency_cannot_latch_target_band_at_full_speed(self):
        feedback = GpuTemperatureTarget()
        self.assertEqual(feedback.update(temperatures(hotspot=90), 0)[0], 100)
        requests = [feedback.update(temperatures(), now)[0] for now in range(2, 122, 2)]
        self.assertEqual(set(requests), {65})

    def test_same_current_heat_has_same_request_after_different_history(self):
        fresh, heated = GpuTemperatureTarget(), GpuTemperatureTarget()
        for now in range(0, 122, 2):
            heated.update(temperatures(64, 80, 75), now)
        self.assertEqual(heated.update(temperatures(), 122)[0],
                         fresh.update(temperatures(), 122)[0])

    def test_clock_failure_clears_after_an_advancing_valid_observation(self):
        feedback = GpuTemperatureTarget()
        feedback.update(temperatures(), 10)
        self.assertEqual(feedback.update(temperatures(), 9)[0], 100)
        self.assertEqual(feedback.update(temperatures(), 11)[0], 65)

    def test_repeated_time_cannot_accumulate_extra_thermal_request(self):
        feedback = GpuTemperatureTarget()
        expected, _ = feedback.update(temperatures(64, 80, 75), 10)
        for _ in range(100):
            self.assertEqual(feedback.update(temperatures(64, 80, 75), 10)[0], expected)

    def test_emergency_clearance_keeps_the_existing_ramp_cooling_hold(self):
        feedback, ramp = GpuTemperatureTarget(), GpuRamp()
        hot = temperatures(hotspot=90)
        request, _ = feedback.update(hot, 0)
        self.assertEqual(ramp.update(request, 0, emergency=emergency_cooling(hot)), 100)
        cool = temperatures()
        for now in range(2, 13, 2):
            request, _ = feedback.update(cool, now)
            self.assertEqual(request, 65)
            self.assertEqual(ramp.update(request, now), 100)
        request, _ = feedback.update(cool, 14)
        self.assertEqual(ramp.update(request, 14), 96)

    def test_delayed_synthetic_plant_can_settle_without_large_speed_cycles(self):
        # This is a deterministic stress model, not this board's measured transfer
        # function: a 60-second thermal time constant and 30-second cooling delay.
        # It exposes controller memory/overshoot without claiming hardware tuning.
        feedback, ramp = GpuTemperatureTarget(), GpuRamp()
        hotspot = 74.0
        pending_cooling = deque([65] * 16)
        baseline = [list(point) for point in BALANCED_DRIVER_POINTS]
        settled = []
        for now in range(0, 1802, 2):
            data = temperatures(hotspot - 20, hotspot, hotspot - 3)
            request, _ = feedback.update(data, now)
            command = ramp.update(request, now, emergency=emergency_cooling(data))
            duty = interpolate(data['gpu_core_temp'], cooling_points(command, baseline))
            pending_cooling.append(duty)
            equilibrium = 105 - 0.3 * pending_cooling.popleft()
            hotspot += (equilibrium - hotspot) * (1 - math.exp(-2 / 60))
            if now >= 1200:
                settled.append((hotspot, duty))
        temperatures_seen, duty_seen = zip(*settled)
        self.assertLess(max(temperatures_seen), 85)
        self.assertLess(max(duty_seen) - min(duty_seen), 10)
        self.assertLess(max(duty_seen), 100)


class WorkerTargetCorrectionTests(unittest.TestCase):
    def test_warm_then_target_band_worker_does_not_latch_full_speed(self):
        def readings(now, adapter):
            if now == 0:
                adapter.state['points'] = [list(point) for point in BALANCED_DRIVER_POINTS]
            data = temperatures(64, 80, 75) if now < 120 else temperatures()
            return dict(data, gpu_fan=2000)

        result, adapter, _, reports, journal = test_gpu_fan_startup_audit.GpuStartupAuditTests().run_worker(
            readings, stop_at=245)
        active = [report for report in reports if report['state'] == 'active']
        self.assertEqual(result, 0)
        self.assertTrue(active)
        self.assertLessEqual(max(report['command_pct'] for report in active), 75)
        self.assertEqual(active[-1]['command_pct'], 65)
        self.assertEqual(adapter.state['points'], [list(point) for point in BALANCED_DRIVER_POINTS])
        self.assertTrue(adapter.state['zero_rpm'])
        self.assertTrue(reports[-1]['restore_confirmed'])
        self.assertFalse(journal)

    def test_repeated_metrics_restart_command_cooling_evidence(self):
        def readings(now, _adapter):
            data = temperatures(hotspot=90) if now < 7 else temperatures()
            timestamp = now * 1000 if now < 7 or now >= 13 else 5000
            return dict(data, gpu_fan=2000, timestamp_ms=timestamp)

        result, _, _, reports, _ = test_gpu_fan_startup_audit.GpuStartupAuditTests().run_worker(
            readings, stop_at=30)
        active = [report for report in reports if report['state'] == 'active']
        self.assertEqual(result, 0)
        cooling = [report for report in active if report['time'] >= 7]
        self.assertTrue(all(report['command_pct'] == 100 for report in cooling if report['time'] <= 23))
        first_decrease = next((report['time'] for report in cooling if report['command_pct'] < 100), None)
        self.assertEqual(first_decrease, 25)


class ProfileCommitBoundaryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.journal = Path(directory.name) / 'recovery.json'
        self.receipt = Path(directory.name) / 'profile.json'
        self.adapter = FakeAdapter()
        self.adapter.identity = dict(name='AMD Radeon RX 7900 XT', vendor='1002', device='744c',
                                     subsystem='240c', subvendor='1458')
        self.adapter.temperature_range = [25, 100, 1]
        self.adapter.speed_range = [23, 100, 1]
        self.stamp = 0

        def readings(**_):
            self.stamp += 1
            return dict(temperatures(), timestamp_ms=self.stamp)

        self.adapter.readings = readings
        self.expected = {'points': [list(point) for point in BALANCED_DRIVER_POINTS], 'zero_rpm': True}

    def interrupt_after_durable_commit(self, exception, *, cleanup_failure=False):
        original_write = RecoveryJournal.before_write

        def interrupted(journal, baseline, before, after):
            original_write(journal, baseline, before, after)
            if baseline == before == after == self.expected:
                raise exception('after durable commit')

        with ExitStack() as stack:
            stack.enter_context(patch.object(RecoveryJournal, 'before_write', interrupted))
            if cleanup_failure:
                stack.enter_context(patch.object(RecoveryJournal, 'clear', side_effect=OSError('cleanup denied')))
            with self.assertRaisesRegex(exception, 'after durable commit'):
                configure(self.adapter, self.journal, self.receipt, apply=True, guard=Mock())

    def test_interrupt_after_durable_commit_keeps_the_new_profile(self):
        self.interrupt_after_durable_commit(KeyboardInterrupt)
        self.assertEqual(self.adapter.snapshot(), self.expected)
        report = json.loads(self.receipt.read_text(encoding='utf-8'))
        self.assertTrue(report['state'].startswith('applied'), report['state'])
        self.assertEqual(report['restore_errors'], [])
        self.assertFalse(RecoveryJournal(self.journal, self.adapter.identity).recover(self.adapter))
        self.assertEqual(self.adapter.snapshot(), self.expected)

    def test_commit_cleanup_error_cannot_restore_an_obsolete_profile(self):
        self.interrupt_after_durable_commit(OSError)
        self.assertEqual(self.adapter.snapshot(), self.expected)
        self.assertFalse(RecoveryJournal(self.journal, self.adapter.identity).recover(self.adapter))
        self.assertEqual(self.adapter.snapshot(), self.expected)

    def test_failed_journal_cleanup_cannot_poison_committed_recovery(self):
        self.interrupt_after_durable_commit(KeyboardInterrupt, cleanup_failure=True)
        self.assertTrue(self.journal.exists())
        self.assertEqual(self.adapter.snapshot(), self.expected)
        self.assertFalse(RecoveryJournal(self.journal, self.adapter.identity).recover(self.adapter))
        self.assertEqual(self.adapter.snapshot(), self.expected)

    def test_failure_before_durable_rebase_still_restores_the_original(self):
        original = self.adapter.snapshot()
        original_write = RecoveryJournal.before_write

        def unavailable(journal, baseline, before, after):
            if baseline == before == after == self.expected:
                raise OSError('before durable commit')
            return original_write(journal, baseline, before, after)

        with patch.object(RecoveryJournal, 'before_write', unavailable):
            with self.assertRaisesRegex(OSError, 'before durable commit'):
                configure(self.adapter, self.journal, self.receipt, apply=True, guard=Mock())
        self.assertEqual(self.adapter.snapshot(), original)
        report = json.loads(self.receipt.read_text(encoding='utf-8'))
        self.assertEqual(report['state'], 'error')
        self.assertEqual(report['restore_errors'], [])
        self.assertFalse(self.journal.exists())

    def test_unrecognized_commit_record_preserves_settings_and_evidence(self):
        original = self.adapter.snapshot()
        original_write = RecoveryJournal.before_write
        unknown = dict(profile='foreign-profile', version=1, gpu={'name': 'foreign GPU'},
                       baseline=original, known_states=[self.expected])

        def unrecognized(journal, baseline, before, after):
            if baseline == before == after == self.expected:
                journal.path.write_text(json.dumps(unknown), encoding='utf-8')
                raise OSError('unrecognized commit journal')
            return original_write(journal, baseline, before, after)

        with patch.object(RecoveryJournal, 'before_write', unrecognized):
            with self.assertRaisesRegex(OSError, 'unrecognized commit journal'):
                configure(self.adapter, self.journal, self.receipt, apply=True, guard=Mock())
        self.assertEqual(self.adapter.snapshot(), self.expected)
        self.assertTrue(self.journal.exists())
        self.assertEqual(json.loads(self.journal.read_text(encoding='utf-8')), unknown)
        report = json.loads(self.receipt.read_text(encoding='utf-8'))
        self.assertTrue(report['restore_errors'])

    def test_second_interrupt_during_commit_reconciliation_preserves_evidence(self):
        original_write = RecoveryJournal.before_write
        original_read = gpu_fans.fan_common.load_json_payload
        committing = [False]

        def interrupted(journal, baseline, before, after):
            original_write(journal, baseline, before, after)
            if baseline == before == after == self.expected:
                committing[0] = True
                raise KeyboardInterrupt('after durable commit')

        def interrupted_read(path, opener):
            if committing[0]:
                raise KeyboardInterrupt('during journal reconciliation')
            return original_read(path, opener)

        with patch.object(RecoveryJournal, 'before_write', interrupted), \
                patch.object(gpu_fans.fan_common, 'load_json_payload', interrupted_read):
            with self.assertRaisesRegex(KeyboardInterrupt, 'during journal reconciliation'):
                configure(self.adapter, self.journal, self.receipt, apply=True, guard=Mock())
        self.assertEqual(self.adapter.snapshot(), self.expected)
        self.assertTrue(self.journal.exists())
        report = json.loads(self.receipt.read_text(encoding='utf-8'))
        self.assertTrue(report['restore_errors'])


if __name__ == '__main__':
    unittest.main()
