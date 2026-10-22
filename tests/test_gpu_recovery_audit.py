"""Interrupted GPU ownership is restored independently of thermal readiness."""
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

import gpu_fans
from test_gpu_fan_startup_audit import Adapter, sample


class InterruptedGpuRecoveryTests(unittest.TestCase):
    def run_interrupted(self, values, mutate_journal=None):
        now = [0]
        adapter = Adapter(now)
        baseline = adapter.snapshot()
        reports = []
        stop = Mock()
        stop.is_set.side_effect = lambda: now[0] >= 65
        stop.wait.side_effect = lambda seconds: now.__setitem__(0, now[0] + seconds)
        owner = Mock()
        owner.create_time.return_value = 1
        owner.is_running.return_value = True
        heartbeat = Mock()
        heartbeat.expired.return_value = False
        adapter.readings = lambda **_kw: values(now[0])
        with ExitStack() as stack:
            directory = stack.enter_context(tempfile.TemporaryDirectory())
            journal_path = Path(directory) / 'recovery.json'
            journal = gpu_fans.RecoveryJournal(journal_path, adapter.identity)
            interrupted = gpu_fans.GpuSession(adapter, journal)
            interrupted.apply(85)
            retained = adapter.snapshot()
            adapter.writes.clear()
            if mutate_journal:
                mutate_journal(journal_path)
            stack.enter_context(patch('amd_gpu_fan.AmdGpuFan', return_value=adapter))
            stack.enter_context(patch.object(gpu_fans.threading, 'Event', return_value=stop))
            stack.enter_context(patch.object(gpu_fans.threading, 'Thread'))
            stack.enter_context(patch.object(gpu_fans, 'OwnerHeartbeat', return_value=heartbeat))
            stack.enter_context(patch.object(gpu_fans.psutil, 'Process', return_value=owner))
            stack.enter_context(patch.object(gpu_fans.time, 'monotonic', side_effect=lambda: now[0]))
            stack.enter_context(patch.object(gpu_fans, 'require_hardware_access'))
            stack.enter_context(patch.object(gpu_fans, 'write_status', side_effect=lambda _path, state, **kw:
                reports.append(dict(time=now[0], state=state, **kw))))
            result = gpu_fans.worker(str(Path(directory) / 'status.json'), 1, 1)
            pending = journal_path.exists()
        adapter.close.assert_called_once()
        return result, adapter, baseline, retained, reports, pending

    def test_missing_thermal_sensor_cannot_prevent_interrupted_restoration(self):
        result, adapter, baseline, _, reports, pending = self.run_interrupted(
            lambda now: dict(sample(gpu_memory_temp=None), timestamp_ms=now * 1000))
        self.assertEqual(result, 1)
        self.assertEqual(adapter.snapshot(), baseline)
        self.assertFalse(pending)
        self.assertEqual(adapter.writes, [(0, 'curve', baseline['points']), (0, 'zero', baseline['zero_rpm'])])
        self.assertTrue(reports[-1]['restore_confirmed'])
        self.assertFalse(reports[-1]['recovery_pending'])
        self.assertIn('Waiting for GPU Memory', reports[-1]['reason'])

    def test_frozen_hot_metrics_restore_without_authorizing_new_takeover(self):
        result, adapter, baseline, _, reports, pending = self.run_interrupted(
            lambda _now: dict(sample(gpu_core_temp=95), timestamp_ms=1))
        self.assertEqual(result, 1)
        self.assertEqual(adapter.snapshot(), baseline)
        self.assertFalse(pending)
        self.assertEqual(len(adapter.writes), 2)
        self.assertFalse(any(report['state'] == 'active' for report in reports))

    def test_new_takeover_still_requires_two_complete_advancing_samples(self):
        result, adapter, baseline, _, reports, pending = self.run_interrupted(
            lambda now: dict(sample(gpu_core_temp=70, gpu_memory_temp=None if now < 3 else 60,
                                    gpu_fan=2000), timestamp_ms=now * 1000))
        self.assertEqual(result, 0)
        self.assertEqual(adapter.snapshot(), baseline)
        self.assertFalse(pending)
        self.assertEqual(adapter.writes[:2], [(0, 'curve', baseline['points']), (0, 'zero', baseline['zero_rpm'])])
        active = [report for report in reports if report['state'] == 'active']
        self.assertTrue(active)
        # Readiness accepts t=3/4; the first runtime read repeats t=4 and
        # earns no cooling decision until the advancing sample at t=6.
        self.assertEqual(active[0]['time'], 6)
        self.assertTrue(all(stamp >= 6 for stamp, *_ in adapter.writes[2:]))

    def test_foreign_journal_preserves_hardware_and_recovery_evidence(self):
        def foreign(path):
            import json
            payload = json.loads(path.read_text())
            payload['gpu'] = {'name': 'different GPU'}
            path.write_text(json.dumps(payload))
        result, adapter, _, retained, reports, pending = self.run_interrupted(
            lambda now: dict(sample(gpu_memory_temp=None), timestamp_ms=now * 1000), foreign)
        self.assertEqual(result, 1)
        self.assertEqual(adapter.snapshot(), retained)
        self.assertEqual(adapter.writes, [])
        self.assertTrue(pending)
        self.assertFalse(reports[-1]['restore_confirmed'])
        self.assertIn('invalid', reports[-1]['reason'])
