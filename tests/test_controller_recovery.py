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


if __name__ == '__main__':
    unittest.main()
