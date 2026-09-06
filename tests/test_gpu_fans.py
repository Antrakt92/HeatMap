import copy
import io
import json
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from contextlib import ExitStack

from gpu_fans import CURVES, GpuRamp, GpuSession, GpuWorkerClient, PROFILE, RecoveryJournal, cooling_points, demand
import gpu_fans


class FakeAdapter:
    def __init__(self):
        self.state = {'points': [[30, 23], [50, 38], [56, 53], [63, 68], [70, 100]], 'zero_rpm': True}
        self.events = []
        self.fail_curve = False
        self.fail_zero = False

    def snapshot(self):
        return copy.deepcopy(self.state)

    def set_points(self, points):
        self.events.append('curve')
        self.state['points'] = copy.deepcopy(points)
        if self.fail_curve:
            raise RuntimeError('partial native curve write')

    def set_zero_rpm(self, enabled):
        self.events.append('zero')
        self.state['zero_rpm'] = enabled
        if self.fail_zero:
            raise RuntimeError('partial native Zero RPM write')


class GpuPolicyTests(unittest.TestCase):
    def sample(self, **extra):
        return dict(dict.fromkeys(CURVES, 40), **extra)

    def test_hotspot_90_full_even_with_cold_core(self):
        self.assertEqual(demand(self.sample(gpu_hotspot_temp=90)), (100, 'Hotspot'))

    def test_each_sensor_independently_requests_full(self):
        for key, temperature in (('gpu_core_temp', 75), ('gpu_hotspot_temp', 90), ('gpu_memory_temp', 90)):
            with self.subTest(key=key):
                self.assertEqual(demand(self.sample(**{key: temperature}))[0], 100)

    def test_uses_normalized_demand_not_hottest_raw_sensor(self):
        self.assertEqual(demand(self.sample(gpu_core_temp=70, gpu_hotspot_temp=72, gpu_memory_temp=75)), (85, 'Core'))

    def test_bad_or_missing_sensor_never_lowers_airflow(self):
        for key in CURVES:
            for value in (None, False, float('nan'), float('inf'), '90', -10, 0, 1e300):
                with self.subTest(key=key, value=value):
                    self.assertEqual(demand(self.sample(**{key: value}))[0], 100)

    def test_curves_are_monotone(self):
        for key in CURVES:
            commands = [demand(self.sample(**{key: value}))[0] for value in range(1, 151)]
            self.assertEqual(commands, sorted(commands))
            self.assertEqual(commands[0], 30)

    def test_ramp_rises_immediately_and_restarts_cooling_hold(self):
        ramp = GpuRamp()
        self.assertEqual(ramp.update(30, 0), 100)
        self.assertEqual(ramp.update(30, 9), 100)
        self.assertEqual(ramp.update(30, 11), 96)
        self.assertEqual(ramp.update(100, 12), 100)
        self.assertEqual(ramp.update(30, 13), 100)
        self.assertEqual(ramp.update(30, 22), 100)

    def test_long_pause_cannot_drop_speed_abruptly(self):
        ramp = GpuRamp()
        ramp.update(30, 0)
        self.assertEqual(ramp.update(30, 1000), 96)
        self.assertEqual(ramp.update(30, 1), 100)

    def test_driver_curve_retains_full_speed_at_90(self):
        for command in range(30, 101):
            points = cooling_points(command)
            self.assertEqual(points[-1], [90, 100])
            self.assertTrue(all(speed >= command for _, speed in points))

    def test_invalid_command_rejected(self):
        for value in (None, True, 29, 101, 70.5, float('nan')):
            with self.assertRaises(ValueError):
                cooling_points(value)


class GpuSessionTests(unittest.TestCase):
    def test_roundtrip_restores_custom_curve_and_zero_rpm(self):
        adapter = FakeAdapter()
        before = adapter.snapshot()
        session = GpuSession(adapter)
        session.apply(100)
        session.apply(50)
        self.assertFalse(adapter.state['zero_rpm'])
        self.assertEqual(session.restore(), [])
        self.assertEqual(adapter.snapshot(), before)
        self.assertEqual(session.restore(), [])

    def test_partial_takeover_remains_in_restore_scope(self):
        adapter = FakeAdapter()
        before = adapter.snapshot()
        session = GpuSession(adapter)
        adapter.fail_zero = True
        with self.assertRaises(RuntimeError):
            session.apply(100)
        self.assertTrue(session.touched)
        adapter.fail_zero = False
        self.assertEqual(session.restore(), [])
        self.assertEqual(adapter.snapshot(), before)

    def test_curve_restore_failure_cannot_skip_zero_rpm(self):
        adapter = FakeAdapter()
        session = GpuSession(adapter)
        session.apply(100)
        adapter.fail_curve = True
        self.assertTrue(session.restore())
        self.assertEqual(adapter.events[-2:], ['curve', 'zero'])
        self.assertTrue(session.touched)
        adapter.fail_curve = False
        self.assertEqual(session.restore(), [])

    def test_external_change_is_detected_before_next_write(self):
        adapter = FakeAdapter()
        session = GpuSession(adapter)
        session.apply(100)
        adapter.state['zero_rpm'] = True
        before = list(adapter.events)
        with self.assertRaisesRegex(RuntimeError, 'outside HeatMap'):
            session.apply(50)
        self.assertEqual(adapter.events, before)
        external = adapter.snapshot()
        self.assertIn('External', session.restore()[0])
        self.assertEqual(adapter.snapshot(), external)
        self.assertEqual(adapter.events, before)

    def test_silent_write_failure_is_not_active_control(self):
        adapter = FakeAdapter()
        session = GpuSession(adapter)
        adapter.set_points = lambda _: None
        with self.assertRaises(RuntimeError):
            session.apply(100)
        self.assertIsNone(session.command)
        self.assertFalse(session.ownership_lost)
        self.assertEqual(session.restore(), [])


class GpuRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'recovery.json'
        self.journal = RecoveryJournal(self.path, {'name': 'test GPU', 'subsystem': 'one'})
        self.adapter = FakeAdapter()
        self.baseline = self.adapter.snapshot()

    def test_restart_recovers_original_baseline_after_several_commands(self):
        session = GpuSession(self.adapter, self.journal)
        session.apply(100)
        session.apply(50)
        self.assertTrue(self.path.exists())
        self.assertTrue(self.journal.recover(self.adapter))
        self.assertEqual(self.adapter.snapshot(), self.baseline)
        self.assertFalse(self.path.exists())

    def test_partial_native_write_is_recoverable(self):
        session = GpuSession(self.adapter, self.journal)
        self.adapter.fail_curve = True
        with self.assertRaises(RuntimeError):
            session.apply(100)
        self.adapter.fail_curve = False
        self.assertTrue(self.journal.recover(self.adapter))
        self.assertEqual(self.adapter.snapshot(), self.baseline)

    def test_partial_restore_keeps_journal_and_can_recover(self):
        session = GpuSession(self.adapter, self.journal)
        session.apply(100)
        self.adapter.set_zero_rpm = Mock(side_effect=RuntimeError('driver temporarily busy'))
        self.assertTrue(session.restore())
        self.assertTrue(self.path.exists())
        self.adapter.set_zero_rpm = FakeAdapter.set_zero_rpm.__get__(self.adapter)
        self.assertTrue(self.journal.recover(self.adapter))
        self.assertEqual(self.adapter.snapshot(), self.baseline)

    def test_external_curve_or_different_gpu_never_gets_overwritten(self):
        for different_gpu in (True, False):
            with self.subTest(different_gpu=different_gpu):
                session = GpuSession(self.adapter, self.journal)
                session.apply(100)
                journal = self.journal
                if different_gpu:
                    journal = RecoveryJournal(self.path, {'name': 'another GPU'})
                else:
                    self.adapter.state['points'][0][1] = 99
                before = self.adapter.snapshot()
                events = list(self.adapter.events)
                with self.assertRaisesRegex(RuntimeError, 'settings preserved'):
                    journal.recover(self.adapter)
                self.assertEqual(self.adapter.snapshot(), before)
                self.assertEqual(self.adapter.events, events)
                self.adapter = FakeAdapter()

    def test_journal_failure_happens_before_native_write(self):
        session = GpuSession(self.adapter, self.journal)
        with patch('gpu_fans.replace_status_file', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                session.apply(100)
        self.assertFalse(session.touched)
        self.assertEqual(self.adapter.events, [])
        self.assertEqual(list(self.path.parent.iterdir()), [])

    def test_already_restored_journal_is_removed_without_hardware_writes(self):
        self.journal.before_write(self.baseline, self.baseline, self.baseline)
        self.assertFalse(self.journal.recover(self.adapter))
        self.assertEqual(self.adapter.events, [])
        self.assertFalse(self.path.exists())

    def test_malformed_journal_never_writes_hardware(self):
        for payload in ('broken', '{}', 'null', '[]'):
            self.path.write_text(payload, encoding='utf-8')
            with self.assertRaises(RuntimeError):
                self.journal.recover(self.adapter)
            self.assertEqual(self.adapter.events, [])


class AdlxLifetimeTests(unittest.TestCase):
    def test_cleanup_keeps_initialization_error_on_python_without_exception_notes(self):
        from amd_gpu_fan import AmdGpuFan, AdlxError

        class ErrorWithoutNotes(AdlxError):
            add_note = None

        with patch('amd_gpu_fan.AdlxError', ErrorWithoutNotes), \
                patch('amd_gpu_fan.os.name', 'posix'), \
                patch.object(AmdGpuFan, 'close', side_effect=RuntimeError('cleanup failed')):
            with self.assertRaisesRegex(ErrorWithoutNotes, '64-bit Windows'):
                AmdGpuFan()

    def test_release_failure_does_not_skip_other_references_or_termination(self):
        from amd_gpu_fan import AmdGpuFan, AdlxError
        adapter = AmdGpuFan.__new__(AmdGpuFan)
        first, last = Mock(), Mock()
        last.close.side_effect = RuntimeError('release failed')
        adapter.owned = [first, last]
        adapter.initialized = True
        adapter.system = Mock()
        adapter.dll = Mock()
        adapter.dll.ADLXTerminate.return_value = 0
        with self.assertRaisesRegex(AdlxError, 'release failed'):
            adapter.close()
        first.close.assert_called_once()
        adapter.dll.ADLXTerminate.assert_called_once()
        self.assertIsNone(adapter.system)
        adapter.close()
        first.close.assert_called_once()
        last.close.assert_called_once()
        adapter.dll.ADLXTerminate.assert_called_once()


class GpuClientTests(unittest.TestCase):
    def client(self, status, exited=False):
        client = GpuWorkerClient('.')
        client.started = time.time() - 2
        client.process = Mock(pid=123)
        client.process.poll.return_value = 0 if exited else None
        client.status_path = 'unused'
        stream = Mock()
        stream.__enter__ = Mock(return_value=stream)
        stream.__exit__ = Mock(return_value=False)
        stream.read.return_value = json.dumps(status)
        return client, stream

    def status(self, **extra):
        return dict(dict(profile=PROFILE, state='active', time=time.time(), pid=123, command_pct=40), **extra)

    def test_40_percent_is_valid(self):
        client, stream = self.client(self.status())
        with patch('gpu_fans.open_status_file', return_value=stream):
            self.assertEqual(client.poll()['state'], 'active')

    def test_dead_or_stale_owner_is_not_reported_active(self):
        for status, exited in ((self.status(), True), (self.status(time=time.time()-30), False)):
            client, stream = self.client(status, exited)
            with patch('gpu_fans.open_status_file', return_value=stream):
                self.assertEqual(client.poll()['state'], 'error')

    def test_corrupt_shapes_are_errors(self):
        for status in (None, [], self.status(command_pct=101), self.status(pid=True), self.status(pid=-1),
                       self.status(profile='case'), self.status(restore_confirmed='yes'),
                       self.status(restore_errors='none'), self.status(reason=[])):
            client, stream = self.client(status)
            with patch('gpu_fans.open_status_file', return_value=stream):
                self.assertEqual(client.poll()['state'], 'error')

    def test_terminal_restore_evidence_does_not_expire(self):
        status = self.status(state='stopped', time=time.time()-100, restore_confirmed=True)
        client, stream = self.client(status, True)
        client.started = time.time()-200
        with patch('gpu_fans.open_status_file', return_value=stream):
            self.assertTrue(client.poll()['restore_confirmed'])


class GpuWorkerTests(unittest.TestCase):
    def run_worker(self, failure=None, stop_at=40, heartbeat=False):
        adapter = FakeAdapter()
        baseline = adapter.snapshot()
        adapter.identity = {'name': 'test GPU'}
        adapter.close = Mock()
        now = [0]
        stop = Mock()
        stop.is_set.side_effect = lambda: now[0] >= stop_at
        stop.wait.side_effect = lambda seconds: now.__setitem__(0, now[0] + seconds)
        reports = []
        owner = Mock()
        owner.create_time.return_value = 1
        owner.is_running.return_value = True

        def readings():
            if failure == 'read' and now[0] >= 6:
                raise RuntimeError('driver read failed')
            return dict(gpu_core_temp=40, gpu_hotspot_temp=50, gpu_memory_temp=60,
                        gpu_fan=3400 if adapter.state['points'][0][1] == 100 else 2000,
                        timestamp_ms=now[0]*1000 if failure != 'stale' else 10)

        def publish(_path, state, **details):
            if failure == 'status' and state == 'checking' and now[0] >= 6:
                raise OSError('status write failed')
            reports.append(dict(state=state, **details))

        adapter.readings = readings
        with ExitStack() as stack:
            directory = stack.enter_context(tempfile.TemporaryDirectory())
            stack.enter_context(patch('amd_gpu_fan.AmdGpuFan', return_value=adapter))
            stack.enter_context(patch.object(gpu_fans.threading, 'Event', return_value=stop))
            stack.enter_context(patch.object(gpu_fans.threading, 'Thread'))
            stack.enter_context(patch.object(gpu_fans.psutil, 'Process', return_value=owner))
            stack.enter_context(patch.object(gpu_fans.time, 'monotonic', side_effect=lambda: now[0]))
            stack.enter_context(patch.object(gpu_fans, 'require_hardware_access'))
            stack.enter_context(patch.object(gpu_fans, 'write_status', side_effect=publish))
            if heartbeat:
                simulated_heartbeat = Mock()
                simulated_heartbeat.expired.return_value = False
                stack.enter_context(patch.object(gpu_fans, 'OwnerHeartbeat', return_value=simulated_heartbeat))
            result = gpu_fans.worker(str(Path(directory) / 'status.json'), 1, 1)
        self.assertEqual(adapter.snapshot(), baseline)
        self.assertTrue(reports[-1]['restore_confirmed'])
        self.assertFalse(reports[-1]['restore_errors'])
        adapter.close.assert_called_once()
        return result, reports

    def test_heartbeat_expiry_restores_without_ui(self):
        result, reports = self.run_worker()
        self.assertEqual(result, 1)
        self.assertIn('heartbeat', reports[-1]['reason'])

    def test_read_and_publication_failures_restore(self):
        for fault in ('read', 'status'):
            with self.subTest(fault=fault):
                result, reports = self.run_worker(fault)
                self.assertEqual(result, 1)
                self.assertEqual(reports[-1]['state'], 'error')
                self.assertIn('driver read' if fault == 'read' else 'status write', reports[-1]['reason'])

    def test_normal_stop_restores(self):
        result, reports = self.run_worker(stop_at=8)
        self.assertEqual(result, 0)
        self.assertEqual(reports[-1]['state'], 'stopped')

    def test_active_owner_completes_full_airflow_check_and_lowers_speed(self):
        result, reports = self.run_worker(heartbeat=True)
        self.assertEqual(result, 0)
        active = [report for report in reports if report['state'] == 'active']
        self.assertTrue(active)
        self.assertTrue(all(report['verified_full_rpm'] == 3400 for report in active))
        self.assertLess(active[-1]['command_pct'], 100)

    def test_stale_metrics_restore_even_with_fresh_owner_heartbeat(self):
        result, reports = self.run_worker(failure='stale', heartbeat=True)
        self.assertEqual(result, 1)
        self.assertIn('metrics stopped updating', reports[-1]['reason'])
        stale = [report for report in reports if report.get('readings', {}).get('gpu_core_temp', 40) is None]
        self.assertTrue(stale)
        self.assertTrue(all(report['command_pct'] == 100 for report in stale))

    def test_heartbeat_input_renews_deadline_and_eof_requests_stop(self):
        stop = Mock()
        with patch('gpu_fans.time.monotonic', side_effect=[0, 10, 24, 26]):
            heartbeat = gpu_fans.OwnerHeartbeat(stop)
            with patch('gpu_fans.sys.stdin', io.StringIO('ignored\nalive\n')):
                heartbeat.listen()
            self.assertFalse(heartbeat.expired())
            self.assertTrue(heartbeat.expired())
        stop.set.assert_called_once()


class GpuCommissionTests(unittest.TestCase):
    def verify_reports(self, repeated):
        from tools.commission_gpu_fans import verify
        now = [0]
        client = Mock()
        stopped = [False]
        client.stop.side_effect = lambda: stopped.__setitem__(0, True)
        client.poll.side_effect = lambda: ({'state': 'stopped', 'restore_confirmed': True}
            if stopped[0] else {'state': 'active', 'time': 100 if repeated else 100 + now[0]})
        with patch('tools.commission_gpu_fans.time.monotonic', side_effect=lambda: now[0]), \
                patch('tools.commission_gpu_fans.time.sleep', side_effect=lambda seconds: now.__setitem__(0, now[0] + seconds)):
            result = verify(client, [])
        client.stop.assert_called_once()
        return result

    def test_fresh_reports_can_verify(self):
        self.assertTrue(self.verify_reports(False)['restore_confirmed'])

    def test_repeated_cached_report_cannot_verify(self):
        with self.assertRaisesRegex(RuntimeError, 'timed out'):
            self.verify_reports(True)


class GpuUiTests(unittest.TestCase):
    def test_config_requires_boolean_enable_flag(self):
        import overlay
        for value in (True, False):
            config, errors = overlay._normalize_config({'gpu_fans_enabled': value}, overlay._default_config())
            self.assertNotIn('gpu_fans_enabled', errors)
            self.assertEqual(config['gpu_fans_enabled'], value)
        _, errors = overlay._normalize_config({'gpu_fans_enabled': 'yes'}, overlay._default_config())
        self.assertIn('gpu_fans_enabled', errors)

    def test_gpu_failure_survives_other_health_updates(self):
        import overlay
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app._gpu_fan_status = dict(state='error', reason='Restore unconfirmed')
        app._set_health_panel(['CPU warm'], 1)
        self.assertEqual(app.health_messages, ['GPU fans: Restore unconfirmed', 'CPU warm'])

    def test_rapid_reenable_waits_for_restore(self):
        import overlay
        import threading
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.config = {'gpu_fans_enabled': False}
        app.lock = threading.Lock()
        app.gpu_fan_worker = Mock()
        app.gpu_fan_worker.process.poll.return_value = None
        app.gpu_fan_worker.process.stdin.closed = True
        app._save_config = Mock()
        app._set_menu_label = Mock()
        app.toggle_gpu_fans()
        app.gpu_fan_worker.start.assert_not_called()
        app._save_config.assert_not_called()
        self.assertIn('restoration', app.health_messages[0])


if __name__ == '__main__':
    unittest.main()
