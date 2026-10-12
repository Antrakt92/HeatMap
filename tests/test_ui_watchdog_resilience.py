"""UI failures and Scheduler waits must not silently end fan-owner polling."""
import threading
import unittest
from unittest import mock

import overlay
from test_sensor_lifecycle import sensor_app


class UiWatchdogResilienceTests(unittest.TestCase):
    def app(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.running = True
        app._stop_event = threading.Event()
        app.root = mock.Mock()
        app.config = {}
        app._set_menu_label = mock.Mock()
        app._fan_control_paused = mock.Mock(return_value=False)
        return app

    def test_transient_render_failure_reschedules_owner_polling(self):
        app = self.app()
        app.gpu_fan_worker = mock.Mock()
        app._poll_fan_controller = mock.Mock(side_effect=OSError('temporary status failure'))
        app.update_ui()
        app.root.after.assert_called_once_with(500, app.update_ui)

    def test_shutdown_during_failed_refresh_does_not_schedule_more_callbacks(self):
        app = self.app()
        app.gpu_fan_worker = mock.Mock()
        def fail(*_args):
            app.running = False
            raise OSError('window shutting down')
        app._poll_fan_controller = mock.Mock(side_effect=fail)
        app.update_ui()
        app.root.after.assert_not_called()

    def test_manual_scheduler_queries_run_off_ui_thread(self):
        app = self.app()
        entered, release = threading.Event(), threading.Event()
        query_threads = []
        main_thread = threading.get_ident()
        def query():
            query_threads.append(threading.get_ident())
            if len(query_threads) == 1:
                entered.set()
                release.wait(.2)
                return False
            return True
        with mock.patch.object(overlay, 'is_autostart_enabled', side_effect=query), mock.patch.object(
                overlay, 'enable_autostart', return_value=(True, 'enabled')):
            try:
                app.toggle_autostart()
                self.assertTrue(entered.wait(1))
                self.assertTrue(app._autostart_pending)
                self.assertTrue(all(identity != main_thread for identity in query_threads))
                app.toggle_autostart()
            finally:
                release.set()
                thread = getattr(app, '_autostart_thread', None)
                if thread is not None:
                    thread.join(3)
            app._poll_autostart_check()
        self.assertEqual(len(query_threads), 2)
        self.assertFalse(app._autostart_pending)
        app._set_menu_label.assert_called_with('autostart', 'Autostart: ON (UAC)')

    def test_scheduler_action_requires_matching_readback(self):
        app = self.app()
        with mock.patch.object(overlay, 'is_autostart_enabled', return_value=False), mock.patch.object(
                overlay, 'enable_autostart', return_value=(True, 'enabled')):
            app.toggle_autostart()
            thread = getattr(app, '_autostart_thread', None)
            if thread is not None:
                thread.join(3)
                app._poll_autostart_check()
        self.assertIn('not confirmed', app._autostart_warning)
        app._set_menu_label.assert_called_with('autostart', 'Autostart: ERROR')

    def test_storage_owner_pause_does_not_refresh_old_sample_age(self):
        app = sensor_app(1)
        app.config = {'storage_temperatures_enabled': True}
        app._storage_snapshot = {}
        sample = dict(disk_number=2, name='SSD', temp=40, sample_time=100)
        with (mock.patch.object(overlay, 'physical_disk_numbers', return_value={2}),
              mock.patch.object(overlay, 'probe_disk', return_value=sample),
              mock.patch.object(overlay, 'require_hardware_access'),
              mock.patch.object(overlay.time, 'monotonic', return_value=200)):
            app.storage_loop()
        self.assertEqual(app._storage_snapshot[2][0], 100)
        rendered = overlay.storage_snapshot_data(app._storage_snapshot, {2}, 200)
        self.assertIsNone(rendered['disks'][0]['temp'])
        self.assertIn('expired', rendered['storage_errors'][0])

    def test_broken_gpu_label_cannot_starve_case_owner(self):
        app = self.app()
        app.gpu_fan_worker = app.fan_worker = mock.Mock()
        app._poll_fan_controller = mock.Mock(return_value={'state': 'off'})
        app.rows = {'gpu_fan_control': mock.Mock()}
        app.rows['gpu_fan_control'].config.side_effect = RuntimeError('temporary Tk rendering failure')
        for _ in range(4):
            app.update_ui()
        self.assertEqual(app._poll_fan_controller.call_args_list, [
            call for _ in range(4) for call in (
                mock.call('gpu_fan_worker', 'gpu_fans_enabled'),
                mock.call('fan_worker', 'case_fans_enabled'))])


if __name__ == '__main__':
    unittest.main()
