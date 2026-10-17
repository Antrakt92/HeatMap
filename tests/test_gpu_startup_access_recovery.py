"""A transient access check before opening ADLX must not latch cooling forever."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import gpu_fans
import overlay
from hardware_access_guard import HardwareAccessConflict
import test_controller_recovery
import test_gpu_fan_startup_audit


class GpuStartupAccessRecoveryTests(unittest.TestCase):
    app = test_controller_recovery.ControllerRecoveryTests.app
    poll = test_controller_recovery.ControllerRecoveryTests.poll
    def blocked(self, **changes):
        values = dict(stop_cause=None, startup_access_blocked=True,
                      control_attempted=False, recovery_pending=False,
                      restore_confirmed=False, baseline=None)
        values.update(changes)
        return self.app(**values)

    def test_access_failure_before_open_is_explicit_and_never_constructs_adapter(self):
        owner = mock.Mock()
        owner.create_time.return_value = 1
        reports = []
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(gpu_fans.threading, 'Thread'), \
                mock.patch.object(gpu_fans.psutil, 'Process', return_value=owner), \
                mock.patch.object(gpu_fans, 'OwnerHeartbeat') as heartbeat, \
                mock.patch.object(gpu_fans, 'require_hardware_access',
                                  side_effect=HardwareAccessConflict('pnputil.exe')), \
                mock.patch('amd_gpu_fan.AmdGpuFan') as adapter, \
                mock.patch.object(gpu_fans, 'write_status', side_effect=lambda path, state, **kw:
                                  reports.append(dict(state=state, **kw))):
            heartbeat.return_value.expired.return_value = False
            self.assertEqual(gpu_fans.worker(str(Path(directory) / 'gpu.json'), 7, 1), 1)
        adapter.assert_not_called()
        self.assertIs(reports[-1].get('startup_access_blocked'), True)
        self.assertIs(reports[-1]['control_attempted'], False)
        self.assertIs(reports[-1]['recovery_pending'], False)

    def test_cleared_startup_conflict_retries_once_and_retains_original_reason(self):
        app, worker, original = self.blocked(reason='pnputil.exe')
        with mock.patch.object(overlay, 'require_hardware_access', return_value='full') as guard:
            self.poll(app, 0, 'gpu_fan_worker', 'gpu_fans_enabled')
            result = self.poll(app, 2, 'gpu_fan_worker', 'gpu_fans_enabled')
            self.assertEqual(result['state'], 'checking')
            self.assertEqual(result['previous_stop'], original)
            self.poll(app, 5, 'gpu_fan_worker', 'gpu_fans_enabled')
        worker.start.assert_called_once_with()
        guard.assert_called_with('gpu_control')

    def test_conflict_after_native_open_is_not_marked_retryable(self):
        calls = [0]

        def guard():
            calls[0] += 1
            if calls[0] > 1:
                raise HardwareAccessConflict('pnputil.exe')

        harness = test_gpu_fan_startup_audit.GpuStartupAuditTests()
        result, adapter, _, reports, _ = harness.run_worker(
            lambda *_: test_gpu_fan_startup_audit.sample(), access_guard=guard)
        self.assertEqual(result, 1)
        self.assertNotIn('startup_access_blocked', reports[-1])
        self.assertEqual(adapter.writes, [])

    def test_constructor_failure_is_not_untouched_startup(self):
        owner = mock.Mock()
        owner.create_time.return_value = 1
        reports = []
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(gpu_fans.threading, 'Thread'), \
                mock.patch.object(gpu_fans.psutil, 'Process', return_value=owner), \
                mock.patch.object(gpu_fans, 'OwnerHeartbeat') as heartbeat, \
                mock.patch.object(gpu_fans, 'require_hardware_access'), \
                mock.patch('amd_gpu_fan.AmdGpuFan',
                           side_effect=HardwareAccessConflict('native initialization failed')), \
                mock.patch.object(gpu_fans, 'write_status', side_effect=lambda path, state, **kw:
                                  reports.append(dict(state=state, **kw))):
            heartbeat.return_value.expired.return_value = False
            self.assertEqual(gpu_fans.worker(str(Path(directory) / 'gpu.json'), 7, 1), 1)
        self.assertNotIn('startup_access_blocked', reports[-1])

    def test_case_worker_cannot_use_gpu_startup_retry(self):
        app, worker, _ = self.blocked()
        with mock.patch.object(overlay, 'require_hardware_access', return_value='full'):
            self.poll(app, 0)
            self.poll(app, 2)
        worker.start.assert_not_called()

    def test_malformed_access_evidence_is_rejected(self):
        for value in (1, 'true', None, []):
            self.assertEqual(gpu_fans._gpu_extra_late({'startup_access_blocked': value}),
                             'Invalid GPU startup access report')

    def test_conflict_must_stay_clear_for_two_seconds(self):
        app, worker, _ = self.blocked()
        with mock.patch.object(overlay, 'require_hardware_access', side_effect=[
                'full', HardwareAccessConflict('pnputil.exe'), 'full', 'full']):
            for now in (0, 2, 3):
                self.poll(app, now, 'gpu_fan_worker', 'gpu_fans_enabled')
            worker.start.assert_not_called()
            self.poll(app, 5, 'gpu_fan_worker', 'gpu_fans_enabled')
        worker.start.assert_called_once_with()

    def test_unknown_or_touched_startup_failure_never_retries(self):
        for changes in ({'startup_access_blocked': False}, {'startup_access_blocked': 1},
                        {'control_attempted': True, 'restore_confirmed': True},
                        {'recovery_pending': True}, {'baseline': {}},
                        {'restore_errors': ['close failed']}, {'settings_conflict': {}},
                        {'status_publication_error': 'disk full'}):
            with self.subTest(changes=changes):
                app, worker, _ = self.blocked(**changes)
                with mock.patch.object(overlay, 'require_hardware_access', return_value='full'):
                    self.poll(app, 0, 'gpu_fan_worker', 'gpu_fans_enabled')
                    self.poll(app, 2, 'gpu_fan_worker', 'gpu_fans_enabled')
                worker.start.assert_not_called()
