"""Recovery must never turn an unverified shutdown into another takeover."""
import threading
import unittest
from unittest import mock

import overlay


class ControllerRecoveryTests(unittest.TestCase):
    def app(self, **changes):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.running = True
        app.lock = threading.Lock()
        app.config = {'case_fans_enabled': True, 'gpu_fans_enabled': True}
        app._fan_recovery = {}
        status = dict(state='error', reason='Owner heartbeat expired',
                      stop_cause='heartbeat_expired', restore_confirmed=True,
                      restore_errors=[], control_attempted=True)
        status.update(changes)
        worker = mock.Mock()
        worker.poll.return_value = status
        worker.process.poll.return_value = 1
        app.fan_worker = app.gpu_fan_worker = worker
        return app, worker, status

    def poll(self, app, now, attribute='fan_worker', setting='case_fans_enabled'):
        with mock.patch.object(overlay.time, 'monotonic', return_value=now):
            return app._poll_fan_controller(attribute, setting)

    def test_restored_timeout_gets_one_retry_after_ui_recovers(self):
        for attribute, setting in (('fan_worker', 'case_fans_enabled'),
                                   ('gpu_fan_worker', 'gpu_fans_enabled')):
            with self.subTest(attribute=attribute):
                app, worker, status = self.app()
                self.assertEqual(self.poll(app, 0, attribute, setting)['reason'], status['reason'])
                self.poll(app, 1, attribute, setting)
                worker.start.assert_not_called()
                result = self.poll(app, 2, attribute, setting)
                self.assertEqual(result['state'], 'checking')
                worker.start.assert_called_once_with()  # Never accept external GPU settings.
                self.poll(app, 4, attribute, setting)
                self.poll(app, 20, attribute, setting)
                worker.start.assert_called_once()

    def test_worker_first_driver_pause_recovers_after_stable_scope_access(self):
        for attribute, setting, scope, release in (
                ('fan_worker', 'case_fans_enabled', 'independent_case_control', {}),
                ('gpu_fan_worker', 'gpu_fans_enabled', 'gpu_control',
                 dict(control_attempted=False, restore_confirmed=False, recovery_pending=False))):
            with self.subTest(attribute=attribute):
                app, worker, status = self.app(stop_cause='hardware_access_paused',
                                               reason='Driver installation detected', **release)
                worker.b850 = True
                with mock.patch.object(overlay, 'require_hardware_access', return_value='full') as guard:
                    for now in range(0, 15, 2):
                        app._fan_resume_sample_time = now
                        self.poll(app, now, attribute, setting)
                    worker.start.assert_not_called()
                    app._fan_resume_sample_time = 16
                    result = self.poll(app, 16, attribute, setting)
                    self.assertEqual(result['state'], 'checking')
                    self.assertEqual(result['previous_stop'], status)
                    self.poll(app, 18, attribute, setting)
                worker.start.assert_called_once_with()
                self.assertTrue(guard.call_args_list)
                self.assertTrue(all(call == mock.call(scope) for call in guard.call_args_list))

    def test_worker_first_driver_pause_restarts_clear_interval_after_another_block(self):
        app, worker, _ = self.app(stop_cause='hardware_access_paused')
        with mock.patch.object(overlay, 'require_hardware_access', return_value='full') as guard:
            for now in range(0, 31, 2):
                app._fan_resume_sample_time = now
                guard.side_effect = overlay.HardwareAccessConflict('still installing') if now == 14 else None
                self.poll(app, now)
            worker.start.assert_not_called()
            app._fan_resume_sample_time = 32
            self.poll(app, 32)
        worker.start.assert_called_once_with()

    def test_worker_first_driver_pause_requires_release_and_fresh_sensors(self):
        for changes, refresh in (({'restore_confirmed': False}, True),
                                 ({'restore_confirmed': True, 'recovery_pending': True}, True),
                                 ({'settings_conflict': {'external': True}}, True),
                                 ({'status_publication_error': 'failed'}, True), ({}, False)):
            with self.subTest(changes=changes, refresh=refresh):
                app, worker, _ = self.app(stop_cause='hardware_access_paused', **changes)
                with mock.patch.object(overlay, 'require_hardware_access', return_value='full') as guard:
                    for now in range(0, 21, 2):
                        app._fan_resume_sample_time = now if refresh else -20
                        self.poll(app, now)
                guard.assert_not_called()
                worker.start.assert_not_called()

    def test_unsafe_or_unexplained_stops_are_not_retried(self):
        for changes in (
            {'stop_cause': None}, {'stop_cause': 'requested_stop'},
            {'stop_cause': 'owner_lost'}, {'restore_confirmed': False},
            {'restore_confirmed': 1}, {'restore_errors': ['readback failed']},
            {'restore_errors': None}, {'settings_conflict': {'external': True}},
            {'recovery_pending': True}, {'state': 'active'},
        ):
            with self.subTest(changes=changes):
                app, worker, _ = self.app(**changes)
                self.poll(app, 0)
                self.poll(app, 3)
                worker.start.assert_not_called()

    def test_b850_retries_each_released_feedback_failure_after_cooldown(self):
        app, worker, status = self.app(stop_cause=None, feedback_retry=True,
                                       recovery_pending=False, reason="SYS2 unavailable")
        worker.b850 = True
        self.assertEqual(self.poll(app, 0)["retry_seconds"], 30)
        for now in range(2, 30, 2):
            self.poll(app, now)
        worker.start.assert_not_called()
        self.assertEqual(self.poll(app, 30)["state"], "checking")
        worker.start.assert_called_once_with()
        worker.poll.return_value = dict(status, time=100)
        for now in range(32, 64, 2):
            self.poll(app, now)
        self.assertEqual(worker.start.call_count, 2)

    def test_b850_feedback_retry_requires_release_and_matching_worker(self):
        for changes, b850 in (({"recovery_pending": True}, True),
                              ({"restore_confirmed": False}, True),
                              ({"status_publication_error": "failed"}, True),
                              ({}, False)):
            with self.subTest(changes=changes, b850=b850):
                app, worker, status = self.app(stop_cause=None, feedback_retry=True,
                                               recovery_pending=False)
                status.update(changes)
                worker.b850 = b850
                for now in range(0, 36, 2):
                    self.poll(app, now)
                worker.start.assert_not_called()

    def test_disabled_paused_quitting_or_still_restoring_does_not_restart(self):
        for condition in ('disabled', 'paused', 'quitting', 'live'):
            with self.subTest(condition=condition):
                app, worker, _ = self.app()
                self.poll(app, 0)
                if condition == 'disabled':
                    app.config['case_fans_enabled'] = False
                elif condition == 'paused':
                    app._hardware_pause_reason = 'Another controller is running'
                elif condition == 'quitting':
                    app.running = False
                else:
                    worker.process.poll.return_value = None
                self.poll(app, 3)
                worker.start.assert_not_called()

    def test_another_long_ui_pause_requires_new_responsive_interval(self):
        app, worker, _ = self.app()
        self.poll(app, 0)
        self.poll(app, 30)
        worker.start.assert_not_called()
        self.poll(app, 32)
        worker.start.assert_called_once()

    def test_stopped_diagnostics_keep_original_reason(self):
        app, worker, status = self.app(state='stopped', stop_cause=None,
                                      reason='Returned to firmware control')
        result = self.poll(app, 0)
        self.assertEqual(result['state'], 'error')
        self.assertEqual(result['reason'], 'Returned to firmware control')
        self.assertIn('OFF then ON', result['display_reason'])
        self.assertEqual(status['state'], 'stopped')
        worker.start.assert_not_called()

    def test_confirmed_stop_during_hardware_pause_is_not_a_controller_failure(self):
        for attribute, setting in (('fan_worker', 'case_fans_enabled'),
                                   ('gpu_fan_worker', 'gpu_fans_enabled')):
            app, worker, _ = self.app(state='stopped', stop_cause='requested_stop')
            app._hardware_pause_reason = 'Sensors paused: cpuz.exe'
            status = self.poll(app, 0, attribute, setting)
            self.assertEqual(status['state'], 'stopped')
            self.assertNotIn('display_reason', status)
            worker.start.assert_not_called()

    def test_monitor_coexistence_does_not_restart_or_mislabel_fan_workers(self):
        app, worker, _ = self.app(state='stopped', stop_cause='requested_stop',
                                  control_attempted=False, restore_confirmed=False, recovery_pending=False)
        app._monitor_coexistence = True
        status = self.poll(app, 0, 'gpu_fan_worker', 'gpu_fans_enabled')
        self.assertEqual(status['state'], 'stopped')
        self.assertNotIn('display_reason', status)
        worker.start.assert_not_called()

        app, worker, _ = self.app()
        app._monitor_coexistence = True
        self.poll(app, 0, 'gpu_fan_worker', 'gpu_fans_enabled')
        self.poll(app, 3, 'gpu_fan_worker', 'gpu_fans_enabled')
        worker.start.assert_not_called()

    def test_pause_does_not_hide_unconfirmed_or_conflicting_restoration(self):
        for changes in ({'restore_confirmed': False}, {'restore_errors': ['failed']},
                        {'recovery_pending': True}, {'settings_conflict': {'external': True}}):
            app, worker, _ = self.app(state='stopped', **changes)
            app._hardware_pause_reason = 'Sensors paused: cpuz.exe'
            self.assertEqual(self.poll(app, 0)['state'], 'error')
            worker.start.assert_not_called()

    def test_pause_before_first_command_keeps_firmware_state(self):
        app, worker, _ = self.app(state='stopped', control_attempted=False,
                                  restore_confirmed=False, recovery_pending=False)
        app._hardware_pause_reason = 'Sensors paused: cpuz.exe'
        self.assertEqual(self.poll(app, 0)['state'], 'stopped')
        worker.start.assert_not_called()

    def test_pause_with_missing_restore_errors_stays_visible(self):
        app, worker, status = self.app(state='stopped')
        del status['restore_errors']
        app._hardware_pause_reason = 'Sensors paused: cpuz.exe'
        self.assertEqual(self.poll(app, 0)['state'], 'error')
        worker.start.assert_not_called()


    def test_driver_pause_retry_requires_current_process_and_fresh_native_sample(self):
        for blocker in ('paused', 'stale', 'no sample', 'different process',
                        'disabled', 'coexistence', 'live', 'unverified'):
            with self.subTest(blocker=blocker):
                app, worker, status = self.app(state='stopped', stop_cause=None)
                app._fan_pause_stops = {'fan_worker': worker.process}
                app._fan_resume_sample_time = 2
                if blocker == 'paused':
                    app._hardware_pause_reason = 'Installer detected'
                elif blocker == 'stale':
                    app._fan_resume_sample_time = -20
                elif blocker == 'no sample':
                    app._fan_resume_sample_time = None
                elif blocker == 'different process':
                    worker.process = mock.Mock()
                elif blocker == 'disabled':
                    app.config['case_fans_enabled'] = False
                elif blocker == 'coexistence':
                    app._monitor_coexistence = True
                elif blocker == 'live':
                    worker.process.poll.return_value = None
                else:
                    status['restore_confirmed'] = False
                self.poll(app, 2)
                self.poll(app, 4)
                worker.start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
