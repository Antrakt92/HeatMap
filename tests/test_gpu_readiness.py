import copy
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

import gpu_fans
from amd_gpu_fan import AdlxError, AmdGpuFan, require_single_gpu
from startup_readiness import StartupNotReady


class Adapter:
    def __init__(self):
        self.identity = {'name': 'test GPU'}
        self.state = {'points': [[30, 23], [50, 38], [56, 53], [63, 68], [70, 100]], 'zero_rpm': True}
        self.writes = []
        self.close = Mock()

    def snapshot(self):
        return copy.deepcopy(self.state)

    def set_points(self, points):
        self.writes.append('curve')
        self.state['points'] = copy.deepcopy(points)

    def set_zero_rpm(self, enabled):
        self.writes.append('zero')
        self.state['zero_rpm'] = enabled


class GpuReadinessTests(unittest.TestCase):
    def run_startup(self, mode='ready', stop_at=10, pending=False, invalid=None):
        now = [0]
        adapter = Adapter()
        reports = []
        reads = []
        created = []
        journal = Mock()
        journal.recover.return_value = False
        if mode == 'recovery_conflict':
            journal.recover.side_effect = RuntimeError('GPU settings changed; settings preserved')
        owner = Mock()
        owner.create_time.return_value = 2 if mode == 'wrong_owner' else 1
        owner.is_running.return_value = True
        stop = Mock()
        stop.is_set.side_effect = lambda: now[0] >= stop_at
        stop.wait.side_effect = lambda seconds: now.__setitem__(0, now[0] + seconds)
        heartbeat = Mock()
        heartbeat.expired.side_effect = lambda: mode == 'heartbeat' and now[0] >= 2

        def construct():
            created.append(now[0])
            if mode == 'enumerating' and now[0] < 2:
                raise StartupNotReady('Waiting for AMD GPU enumeration')
            if mode == 'unsupported':
                raise AdlxError('Unsupported GPU fan profile')
            return adapter

        def readings(strict=False):
            reads.append((now[0], strict))
            if strict:
                self.assertEqual(adapter.writes, [])
                journal.recover.assert_not_called()
            if mode == 'native_error':
                raise AdlxError('metrics: unsupported', result=12)
            sample = dict(gpu_core_temp=40, gpu_hotspot_temp=50, gpu_memory_temp=60,
                          gpu_fan=3400, timestamp_ms=now[0] * 1000)
            if mode in ('missing', 'cancel', 'heartbeat', 'conflict') and now[0] < 4:
                sample['gpu_hotspot_temp'] = None
            if mode == 'stale':
                sample['timestamp_ms'] = 1
            if mode == 'backwards':
                sample['timestamp_ms'] = 1000 if now[0] == 0 else 500
            if invalid is not None:
                sample[invalid[0]] = invalid[1]
            return sample

        adapter.readings = readings

        def guard():
            if mode == 'conflict' and now[0] >= 2:
                raise RuntimeError('Competing hardware controller')

        with ExitStack() as stack:
            directory = stack.enter_context(tempfile.TemporaryDirectory())
            if pending:
                (Path(directory) / 'recovery.json').write_text('{}', encoding='utf-8')
            stack.enter_context(patch('amd_gpu_fan.AmdGpuFan', side_effect=construct))
            stack.enter_context(patch.object(gpu_fans, 'RecoveryJournal', return_value=journal))
            stack.enter_context(patch.object(gpu_fans.threading, 'Event', return_value=stop))
            stack.enter_context(patch.object(gpu_fans.threading, 'Thread'))
            stack.enter_context(patch.object(gpu_fans, 'OwnerHeartbeat', return_value=heartbeat))
            stack.enter_context(patch.object(gpu_fans.psutil, 'Process', return_value=owner))
            stack.enter_context(patch.object(gpu_fans.time, 'monotonic', side_effect=lambda: now[0]))
            stack.enter_context(patch.object(gpu_fans, 'require_hardware_access', side_effect=guard))
            stack.enter_context(patch.object(gpu_fans, 'write_status',
                side_effect=lambda _path, state, **details: reports.append(dict(state=state, **details))))
            if mode in ('journal_access', 'journal_access_late'):
                stack.enter_context(patch.object(gpu_fans, 'recovery_journal_exists',
                    side_effect=PermissionError('Journal access denied') if mode == 'journal_access' else
                    [False, PermissionError('Journal access denied')]))
            result = gpu_fans.worker(str(Path(directory) / 'status.json'), 1, 1)
        return result, adapter, reports, journal, reads, created

    def test_complete_fresh_metrics_precede_recovery_and_cold_standby(self):
        result, adapter, reports, journal, reads, created = self.run_startup('missing')
        self.assertEqual(result, 0)
        self.assertEqual(created, [0])
        self.assertEqual(adapter.writes, [])
        self.assertTrue(any(report['state'] == 'standby' for report in reports))
        self.assertEqual([stamp for stamp, strict in reads if strict], [0, 1, 2, 3, 4, 5])
        journal.recover.assert_called_once()
        adapter.close.assert_called_once()
        waiting = [report for report in reports if report.get('phase') == 'waiting']
        self.assertTrue(waiting)
        self.assertTrue(all(report['control_attempted'] is False for report in waiting))
        self.assertTrue(all(report['baseline'] is None for report in waiting))
        self.assertEqual(waiting[0]['remaining_seconds'], 60)

    def test_zero_gpu_enumeration_retries_then_keeps_one_adapter(self):
        result, adapter, _, _, _, created = self.run_startup('enumerating')
        self.assertEqual(result, 0)
        self.assertEqual(created, [0, 1, 2])
        adapter.close.assert_called_once()

    def test_stale_metrics_timeout_without_writes_or_recovery(self):
        result, adapter, reports, journal, _, created = self.run_startup('stale', stop_at=100)
        self.assertEqual(result, 1)
        self.assertIn('timed out', reports[-1]['reason'])
        self.assertEqual(adapter.writes, [])
        self.assertEqual(created, [0])
        journal.recover.assert_not_called()
        adapter.close.assert_called_once()

    def test_cancelled_wait_closes_adapter_without_takeover(self):
        result, adapter, reports, journal, _, _ = self.run_startup('cancel', stop_at=2)
        self.assertEqual(result, 0)
        self.assertEqual(reports[-1]['state'], 'stopped')
        self.assertFalse(reports[-1]['control_attempted'])
        self.assertIs(reports[-1]['recovery_pending'], False)
        self.assertEqual(adapter.writes, [])
        journal.recover.assert_not_called()
        adapter.close.assert_called_once()

    def test_pending_recovery_never_claims_firmware_ownership_while_waiting(self):
        _, adapter, reports, journal, _, _ = self.run_startup('cancel', stop_at=2, pending=True)
        self.assertTrue(all(report['control_attempted'] is None for report in reports))
        self.assertTrue(reports[0]['recovery_pending'])
        self.assertTrue(reports[-1]['recovery_pending'])
        self.assertEqual(adapter.writes, [])
        journal.recover.assert_not_called()

    def test_conflict_heartbeat_and_native_errors_do_not_retry_or_write(self):
        for mode in ('conflict', 'heartbeat', 'native_error', 'unsupported'):
            with self.subTest(mode=mode):
                result, adapter, reports, journal, _, created = self.run_startup(mode)
                self.assertEqual(result, 1)
                self.assertEqual(reports[-1]['state'], 'error')
                self.assertEqual(created, [0])
                self.assertEqual(adapter.writes, [])
                journal.recover.assert_not_called()
                if mode != 'unsupported':
                    adapter.close.assert_called_once()

    def test_waiting_mode_is_compact(self):
        self.assertEqual(gpu_fans.mode_text(dict(state='checking', phase='waiting',
                                                remaining_seconds=58.2)), 'Waiting GPU 59s')

    def test_recovery_conflict_after_readiness_is_terminal_without_new_writes(self):
        result, adapter, reports, journal, _, created = self.run_startup('recovery_conflict', pending=True)
        self.assertEqual(result, 1)
        self.assertEqual(created, [0])
        self.assertEqual(adapter.writes, [])
        self.assertIsNone(reports[-1]['control_attempted'])
        self.assertIn('settings preserved', reports[-1]['reason'])
        journal.recover.assert_called_once()
        adapter.close.assert_called_once()

    def test_invalid_present_temperatures_and_timestamps_fail_without_retry(self):
        for key in (*gpu_fans.CURVES, 'timestamp_ms'):
            for value in (False, '90', float('nan'), float('inf'), -1, 1e20):
                with self.subTest(key=key, value=value):
                    result, adapter, reports, journal, reads, _ = self.run_startup(invalid=(key, value))
                    self.assertEqual(result, 1)
                    self.assertIn('Invalid GPU', reports[-1]['reason'])
                    self.assertEqual(len(reads), 1)
                    self.assertEqual(adapter.writes, [])
                    journal.recover.assert_not_called()
                    adapter.close.assert_called_once()

    def test_backwards_timestamp_fails_without_retry_or_takeover(self):
        result, adapter, reports, journal, reads, _ = self.run_startup('backwards')
        self.assertEqual(result, 1)
        self.assertIn('moved backwards', reports[-1]['reason'])
        self.assertEqual(len(reads), 2)
        self.assertEqual(adapter.writes, [])
        journal.recover.assert_not_called()

    def test_old_journal_is_reported_even_if_owner_identity_fails(self):
        result, adapter, reports, _, _, created = self.run_startup('wrong_owner', pending=True)
        self.assertEqual(result, 1)
        self.assertEqual(created, [])
        self.assertTrue(reports[-1]['recovery_pending'])
        self.assertIsNone(reports[-1]['control_attempted'])
        self.assertEqual(adapter.writes, [])

    def test_unreadable_journal_state_cannot_claim_no_prior_control(self):
        result, adapter, reports, _, _, created = self.run_startup('journal_access')
        self.assertEqual(result, 1)
        self.assertEqual(created, [])
        self.assertIsNone(reports[-1]['recovery_pending'])
        self.assertIsNone(reports[-1]['control_attempted'])
        self.assertEqual(adapter.writes, [])

    def test_journal_access_lost_after_wait_is_unknown_not_absent(self):
        result, adapter, reports, journal, _, created = self.run_startup('journal_access_late')
        self.assertEqual(result, 1)
        self.assertEqual(created, [0])
        self.assertIsNone(reports[-1]['recovery_pending'])
        self.assertIsNone(reports[-1]['control_attempted'])
        self.assertEqual(adapter.writes, [])
        journal.recover.assert_not_called()
        adapter.close.assert_called_once()


class AdlxReadinessClassificationTests(unittest.TestCase):
    def test_cleanup_failure_makes_transient_constructor_failure_terminal(self):
        with patch('amd_gpu_fan.os.name', 'nt'), \
                patch('amd_gpu_fan.c.sizeof', side_effect=StartupNotReady('GPU not enumerated')), \
                patch.object(AmdGpuFan, 'close', side_effect=RuntimeError('release failed')):
            with self.assertRaisesRegex(AdlxError, 'startup cleanup failed'):
                AmdGpuFan()

    def test_only_zero_enumeration_is_retryable(self):
        with self.assertRaises(StartupNotReady):
            require_single_gpu(0)
        self.assertIsNone(require_single_gpu(1))
        with self.assertRaises(AdlxError):
            require_single_gpu(2)

    def test_only_documented_pending_or_inactive_metrics_retry(self):
        adapter = AmdGpuFan.__new__(AmdGpuFan)
        for code in (3, 8, 9, 12, 13, 14, 15, 16, 17):
            with self.subTest(code=code):
                adapter._readings = Mock(side_effect=AdlxError('metrics unavailable', result=code))
                with self.assertRaises(StartupNotReady if code in (13, 14) else AdlxError):
                    adapter.readings(strict=True)
                with self.assertRaises(AdlxError):
                    adapter.readings()


if __name__ == '__main__':
    unittest.main()
