"""Idle watchdog recovery must distinguish untouched control from failed restore."""
import io
import json
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

import fan_common
import gpu_fans
import overlay
from test_gpu_fan_startup_audit import Adapter


def idle_stop(**changes):
    status = dict(state='error', reason='GPU owner heartbeat expired',
                  stop_cause='heartbeat_expired', control_attempted=False,
                  recovery_pending=False, restore_confirmed=False,
                  restore_errors=[], baseline=None, settings_conflict=None)
    status.update(changes)
    return status


class ReleasedControlEvidenceTests(unittest.TestCase):
    def test_verified_restore_and_explicit_no_write_are_distinct_safe_outcomes(self):
        self.assertTrue(fan_common.fan_control_released(idle_stop()))
        restored = idle_stop(restore_confirmed=True, control_attempted=True)
        del restored['recovery_pending']
        self.assertTrue(fan_common.fan_control_released(restored))
        self.assertIs(idle_stop()['restore_confirmed'], False)

    def test_unknown_malformed_conflicting_or_unpublished_outcomes_are_unsafe(self):
        for changes in ({'state': 'active'}, {'state': 'standby'}, {'state': 'off'},
                        {'restore_confirmed': None}, {'restore_confirmed': 1},
                        {'control_attempted': None}, {'control_attempted': 0},
                        {'recovery_pending': None}, {'recovery_pending': 0},
                        {'recovery_pending': True}, {'settings_conflict': {}},
                        {'settings_conflict': {'external': True}}, {'restore_errors': None},
                        {'restore_errors': ['close failed']}, {'status_publication_error': ''},
                        {'status_publication_error': None}, {'status_publication_error': 'disk full'}):
            with self.subTest(changes=changes):
                self.assertFalse(fan_common.fan_control_released(idle_stop(**changes)))
        for key in ('control_attempted', 'recovery_pending', 'restore_errors', 'restore_confirmed'):
            status = idle_stop()
            del status[key]
            with self.subTest(missing=key):
                self.assertFalse(fan_common.fan_control_released(status))

    def test_restoration_cannot_override_pending_or_publication_failure(self):
        for changes in ({'recovery_pending': True}, {'recovery_pending': 'false'},
                        {'status_publication_error': 'disk full'}, {'settings_conflict': {}}):
            with self.subTest(changes=changes):
                self.assertFalse(fan_common.fan_control_released(idle_stop(restore_confirmed=True, **changes)))


class IdleWatchdogConsumerTests(unittest.TestCase):
    def app(self, status):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.running = True
        app.lock = threading.Lock()
        app.config = {'case_fans_enabled': True, 'gpu_fans_enabled': True}
        app._fan_recovery = {}
        worker = Mock()
        worker.poll.return_value = status
        worker.process.poll.return_value = 1
        app.gpu_fan_worker = worker
        return app, worker

    def poll(self, app, now):
        with patch.object(overlay.time, 'monotonic', return_value=now):
            return app._poll_fan_controller('gpu_fan_worker', 'gpu_fans_enabled')

    def test_idle_no_write_timeout_restarts_once_after_responsive_polling(self):
        stopped = idle_stop()
        app, worker = self.app(stopped)
        self.poll(app, 0)
        self.poll(app, 1)
        worker.start.assert_not_called()
        restarted = self.poll(app, 2)
        self.assertEqual(restarted['state'], 'checking')
        worker.start.assert_called_once_with()
        self.assertEqual(restarted['previous_stop'], stopped)
        self.assertIs(restarted['previous_stop']['restore_confirmed'], False)
        self.poll(app, 5)
        self.poll(app, 20)
        worker.start.assert_called_once_with()

    def test_untouched_timeout_with_uncertain_recovery_never_restarts(self):
        for changes in ({'recovery_pending': None}, {'recovery_pending': True},
                        {'control_attempted': None}, {'control_attempted': True},
                        {'restore_errors': None}, {'restore_errors': ['cleanup failed']},
                        {'settings_conflict': {'external': True}},
                        {'status_publication_error': 'disk full'}):
            with self.subTest(changes=changes):
                app, worker = self.app(idle_stop(**changes))
                self.poll(app, 0)
                self.poll(app, 2)
                worker.start.assert_not_called()

    def test_missing_recovery_evidence_never_restarts(self):
        status = idle_stop()
        del status['recovery_pending']
        app, worker = self.app(status)
        self.poll(app, 0)
        self.poll(app, 2)
        worker.start.assert_not_called()


class IdleWatchdogProducerTests(unittest.TestCase):
    def test_idle_worker_reports_no_commands_without_claiming_restoration(self):
        now = [0.0]
        adapter = Adapter(now)
        original = adapter.snapshot()
        adapter.readings = lambda **_: dict(gpu_core_temp=49, gpu_hotspot_temp=51,
                                           gpu_memory_temp=66, gpu_fan=0, timestamp_ms=now[0] * 1000)
        stop, owner, heartbeat = Mock(), Mock(), Mock()
        stop.is_set.return_value = False
        stop.wait.side_effect = lambda seconds: now.__setitem__(0, now[0] + seconds)
        owner.create_time.return_value = 1
        owner.is_running.return_value = True
        heartbeat.expired.side_effect = lambda: now[0] >= 17
        reports = []
        with ExitStack() as stack:
            directory = stack.enter_context(tempfile.TemporaryDirectory())
            stack.enter_context(patch('amd_gpu_fan.AmdGpuFan', return_value=adapter))
            stack.enter_context(patch.object(gpu_fans.threading, 'Event', return_value=stop))
            stack.enter_context(patch.object(gpu_fans.threading, 'Thread'))
            stack.enter_context(patch.object(gpu_fans.psutil, 'Process', return_value=owner))
            stack.enter_context(patch.object(gpu_fans, 'OwnerHeartbeat', return_value=heartbeat))
            stack.enter_context(patch.object(gpu_fans.time, 'monotonic', side_effect=lambda: now[0]))
            stack.enter_context(patch.object(gpu_fans, 'require_hardware_access'))
            stack.enter_context(patch.object(gpu_fans, 'write_status', side_effect=lambda _path, state, **details:
                                          reports.append(dict(state=state, **details))))
            result = gpu_fans.worker(str(Path(directory) / 'gpu.json'), 7, 1)
        self.assertEqual(result, 1)
        self.assertTrue(any(report['state'] == 'standby' for report in reports))
        self.assertEqual(adapter.writes, [])
        self.assertEqual(adapter.snapshot(), original)
        expected = idle_stop()
        self.assertEqual({key: reports[-1][key] for key in expected}, expected)
        adapter.close.assert_called_once()


class GpuRecoveryEvidenceValidationTests(unittest.TestCase):
    def client_poll(self, **changes):
        report = dict(idle_stop(), profile=gpu_fans.PROFILE, pid=7, time=101)
        report.update(changes)
        client = gpu_fans.GpuWorkerClient('unused')
        client.started = 100
        client.process = Mock(pid=7)
        client.process.poll.return_value = 1
        with patch.object(gpu_fans, 'open_status_file', return_value=io.StringIO(json.dumps(report))), \
                patch.object(gpu_fans.time, 'time', return_value=200):
            return client.poll()

    def test_client_rejects_malformed_nullable_control_evidence(self):
        for key in ('control_attempted', 'recovery_pending'):
            for value in ('false', 0, 1, [], {}):
                with self.subTest(key=key, value=value):
                    status = self.client_poll(**{key: value})
                    self.assertIn('Invalid', status['reason'])
                    self.assertNotIn('stop_cause', status)

    def test_unknown_control_evidence_remains_unknown(self):
        for key in ('control_attempted', 'recovery_pending'):
            with self.subTest(key=key):
                status = self.client_poll(**{key: None})
                self.assertEqual(status['stop_cause'], 'heartbeat_expired')
                self.assertIsNone(status[key])


if __name__ == '__main__':
    unittest.main()
