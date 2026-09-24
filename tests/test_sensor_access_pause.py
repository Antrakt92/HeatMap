import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import overlay
import hardware_access_guard
from hardware_access_guard import HardwareAccessConflict
from test_sensor_lifecycle import sensor_app


class SensorAccessPauseTests(unittest.TestCase):
    def test_monitor_arriving_between_poll_and_open_does_not_latch_pause(self):
        app = sensor_app(2)
        shared = mock.Mock()
        app.fan_worker = mock.Mock(process=None)
        app.gpu_fan_worker = mock.Mock(process=None)
        with mock.patch.object(overlay, 'require_hardware_access',
                               side_effect=['full', 'shared', 'shared', 'shared']), \
             mock.patch.object(overlay, 'init_hardware_monitor',
                               side_effect=[HardwareAccessConflict('hwinfo64.exe'), shared]) as initialize, \
             mock.patch.object(overlay, 'read_sensors', return_value={'cpu_temp': 50}) as read, \
             mock.patch.object(overlay, '_read_volume_usage', return_value={'volumes': []}), \
             mock.patch.object(app, '_cache_sensor_diagnostics'), \
             mock.patch.object(overlay.psutil, 'cpu_percent'):
            app.sensor_loop()
        self.assertEqual(initialize.call_args_list, [mock.call(), mock.call(coexistence=True)])
        self.assertEqual(read.call_count, 2)
        self.assertFalse(getattr(app, '_hardware_pause_reason', None))
        app.fan_worker.stop.assert_called_once_with()
        app.gpu_fan_worker.stop.assert_called_once_with()

    def test_monitor_arrivals_and_exits_keep_reading_with_one_handback(self):
        full, shared = mock.Mock(), mock.Mock()
        app = sensor_app(6, full)
        app.fan_worker = mock.Mock(process=None)
        app.gpu_fan_worker = mock.Mock(process=None)
        # HWiNFO arrives, other tools join, then all exit. Never resume control
        # automatically or churn the monitor when the set of tools changes.
        inventories = [[], ['hwinfo64.exe'],
                       ['hwinfo64.exe', 'amd ryzen master.exe', 'gcc.exe'],
                       ['cpuz.exe', 'fancontrol.exe'], [], []]
        with mock.patch.object(hardware_access_guard, 'hardware_conflicts', side_effect=inventories), \
             mock.patch.object(overlay, 'init_hardware_monitor', return_value=shared) as initialize, \
             mock.patch.object(overlay, 'read_sensors', return_value={'cpu_temp': 50}) as read, \
             mock.patch.object(overlay, '_read_volume_usage', return_value={'volumes': []}), \
             mock.patch.object(app, '_cache_sensor_diagnostics'), \
             mock.patch.object(overlay.psutil, 'cpu_percent'):
            app.sensor_loop()
        initialize.assert_called_once_with(coexistence=True)
        self.assertEqual(read.call_count, 6)
        self.assertEqual(app.sensor_data['cpu_temp'], 50)
        self.assertFalse(getattr(app, '_hardware_pause_reason', None))
        app.fan_worker.stop.assert_called_once_with()
        app.gpu_fan_worker.stop.assert_called_once_with()
        self.assertTrue(app._monitor_coexistence)
        full.Close.assert_called_once_with()
        shared.Close.assert_called_once_with()

    def test_new_board_startup_backs_up_and_disables_old_case_profile(self):
        with tempfile.TemporaryDirectory() as folder:
            config_path = Path(folder) / 'overlay_config.json'
            log_path = Path(folder) / 'HeatMap' / 'HeatMap.log'
            original = {'case_fans_enabled': True, 'case_fans_shared_enabled': True,
                        'case_fan_full_rpm': {'System Fan #1': 1200, 'System Fan #2': 1200,
                                              'System Fan #4': 1200, 'System Fan #5 / Pump': 1200},
                        'gpu_fans_enabled': True}
            config_path.write_text(json.dumps(original), encoding='utf-8')
            with (mock.patch.object(overlay, 'CONFIG_PATH', str(config_path)),
                  mock.patch.object(overlay, 'LOG_PATH', str(log_path)),
                  mock.patch.object(overlay, '_supported_case_fan_board', return_value=False),
                  mock.patch.object(overlay, 'is_pawnio_driver_installed', return_value=True),
                  mock.patch.object(overlay, 'require_hardware_access', return_value='full'),
                  mock.patch.object(overlay.tk, 'Tk', side_effect=RuntimeError('window reached'))):
                with self.assertRaisesRegex(RuntimeError, 'window reached'):
                    overlay.OverlayApp()
            saved = json.loads(config_path.read_text(encoding='utf-8'))
            self.assertFalse(saved['case_fans_enabled'])
            self.assertFalse(saved['case_fans_shared_enabled'])
            self.assertNotIn('case_fan_full_rpm', saved)
            self.assertTrue(saved['gpu_fans_enabled'])
            backups = list(log_path.parent.glob('config-before-board-change-*.json'))
            self.assertEqual(len(backups), 1)
            self.assertEqual(json.loads(backups[0].read_text(encoding='utf-8')), original)

    def test_old_case_profile_is_retired_on_new_board(self):
        config = {'case_fans_enabled': True, 'case_fans_shared_enabled': True,
                  'case_fan_full_rpm': {'System Fan #1': 1200}, 'gpu_fans_enabled': True}
        with mock.patch.object(overlay, '_supported_case_fan_board', return_value=False):
            self.assertTrue(overlay._retire_unsupported_case_fan_profile(config))
        self.assertFalse(config['case_fans_enabled'])
        self.assertFalse(config['case_fans_shared_enabled'])
        self.assertNotIn('case_fan_full_rpm', config)
        self.assertTrue(config['gpu_fans_enabled'])

    def test_unknown_board_disables_case_fans_but_preserves_calibration(self):
        config = {'case_fans_enabled': True, 'case_fan_full_rpm': {'System Fan #1': 1200}}
        with mock.patch.object(overlay, '_supported_case_fan_board', return_value=None):
            self.assertTrue(overlay._retire_unsupported_case_fan_profile(config))
        self.assertFalse(config['case_fans_enabled'])
        self.assertIn('case_fan_full_rpm', config)

    def test_gcc_arrival_reopens_monitor_and_stops_fan_control(self):
        full = mock.Mock()
        shared = mock.Mock()
        app = sensor_app(3, full)
        app.fan_worker = mock.Mock()
        app.gpu_fan_worker = mock.Mock()
        app.fan_worker.process = None
        app.gpu_fan_worker.process = None
        with mock.patch.object(overlay, 'require_hardware_access', side_effect=['full', 'shared', 'shared']), \
             mock.patch.object(overlay, 'init_hardware_monitor', return_value=shared) as initialize, \
             mock.patch.object(overlay, 'read_sensors', return_value={'cpu_temp': None}) as read, \
             mock.patch.object(overlay, '_read_volume_usage', return_value={'volumes': []}), \
             mock.patch.object(app, '_cache_sensor_diagnostics'), \
             mock.patch.object(overlay.psutil, 'cpu_percent'):
            app.sensor_loop()
        initialize.assert_called_once_with(coexistence=True)
        self.assertEqual(read.call_count, 3)
        self.assertEqual(read.call_args_list[-1].args, (shared,))
        full.Close.assert_called_once_with()
        shared.Close.assert_called_once_with()
        app.fan_worker.stop.assert_called_once_with()
        app.gpu_fan_worker.stop.assert_called_once_with()
        self.assertTrue(app._monitor_coexistence)

    def test_gcc_at_startup_keeps_sensor_monitoring_available(self):
        app = sensor_app(2)
        shared = mock.Mock()
        app._monitor_coexistence = True
        with mock.patch.object(overlay, 'require_hardware_access', return_value='shared'), \
             mock.patch.object(overlay, 'init_hardware_monitor', return_value=shared) as initialize, \
             mock.patch.object(overlay, 'read_sensors', return_value={'cpu_temp': None}) as read, \
             mock.patch.object(overlay, '_read_volume_usage', return_value={'volumes': []}), \
             mock.patch.object(app, '_cache_sensor_diagnostics'), \
             mock.patch.object(overlay.psutil, 'cpu_percent'):
            app.sensor_loop()
        initialize.assert_called_once_with(coexistence=True)
        self.assertEqual([call.args[0] for call in read.call_args_list], [shared, shared])

    def test_ryzen_master_arrival_reopens_monitor_and_stops_fan_control(self):
        full = mock.Mock()
        shared = mock.Mock()
        app = sensor_app(3, full)
        app.fan_worker = mock.Mock()
        app.gpu_fan_worker = mock.Mock()
        app.fan_worker.process = None
        app.gpu_fan_worker.process = None
        with mock.patch.object(overlay, 'require_hardware_access', side_effect=['full', 'shared', 'full']), \
             mock.patch.object(overlay, 'init_hardware_monitor', return_value=shared) as initialize, \
             mock.patch.object(overlay, 'read_sensors', return_value={'cpu_temp': None}) as read, \
             mock.patch.object(overlay, '_read_volume_usage', return_value={'volumes': []}), \
             mock.patch.object(app, '_cache_sensor_diagnostics'), \
             mock.patch.object(overlay.psutil, 'cpu_percent'):
            app.sensor_loop()
        initialize.assert_called_once_with(coexistence=True)
        self.assertEqual(read.call_count, 3)
        self.assertEqual(read.call_args_list[-1].args, (shared,))
        full.Close.assert_called_once_with()
        shared.Close.assert_called_once_with()
        app.fan_worker.stop.assert_called_once_with()
        app.gpu_fan_worker.stop.assert_called_once_with()
        self.assertTrue(app._monitor_coexistence)

    def test_ryzen_master_at_startup_keeps_sensor_monitoring_available(self):
        app = sensor_app(2)
        shared = mock.Mock()
        app._monitor_coexistence = True
        with mock.patch.object(overlay, 'require_hardware_access', return_value='shared'), \
             mock.patch.object(overlay, 'init_hardware_monitor', return_value=shared) as initialize, \
             mock.patch.object(overlay, 'read_sensors', return_value={'cpu_temp': None}) as read, \
             mock.patch.object(overlay, '_read_volume_usage', return_value={'volumes': []}), \
             mock.patch.object(app, '_cache_sensor_diagnostics'), \
             mock.patch.object(overlay.psutil, 'cpu_percent'):
            app.sensor_loop()
        initialize.assert_called_once_with(coexistence=True)
        self.assertEqual([call.args[0] for call in read.call_args_list], [shared, shared])

    def test_gcc_waits_for_confirmed_fan_handback(self):
        app = sensor_app(1)
        events = []
        for name in ('gpu_fan_worker', 'fan_worker'):
            worker = mock.Mock()
            worker.stop.side_effect = lambda name=name: events.append('stop ' + name)
            worker.process.wait.side_effect = lambda timeout, name=name: events.append('wait ' + name)
            worker.poll.return_value = {'state': 'stopped', 'restore_errors': [],
                                        'restore_confirmed': True, 'recovery_pending': False}
            setattr(app, name, worker)
        app._stop_fan_workers_for_monitor()
        self.assertEqual(events, ['stop gpu_fan_worker', 'stop fan_worker',
                                  'wait gpu_fan_worker', 'wait fan_worker'])

    def test_gcc_does_not_resume_sensors_after_unconfirmed_handback(self):
        app = sensor_app(1)
        app.gpu_fan_worker = mock.Mock()
        app.gpu_fan_worker.poll.return_value = {'state': 'error', 'restore_errors': [],
                                                'control_attempted': True,
                                                'restore_confirmed': False}
        app.fan_worker = mock.Mock(process=None)
        with self.assertRaisesRegex(HardwareAccessConflict, 'restoration could not be confirmed'):
            app._stop_fan_workers_for_monitor()

    def test_confirmed_pause_shows_cause_without_toggle_instructions(self):
        from test_overlay_helpers import _update_ui_app, _FakeLabel

        app = _update_ui_app()
        reason = 'Sensors paused: Close cpuz.exe, then restart HeatMap'
        app._hardware_pause_reason = reason
        app.sensor_data = {'error': reason}
        app.config = {'case_fans_enabled': True, 'gpu_fans_enabled': True}
        app.rows['gpu_fan_control'] = _FakeLabel()
        app.health_label = mock.Mock()
        for attribute in ('fan_worker', 'gpu_fan_worker'):
            worker = mock.Mock()
            worker.poll.return_value = dict(state='stopped', restore_confirmed=True,
                                           restore_errors=[], control_attempted=True)
            setattr(app, attribute, worker)
        with mock.patch.object(app, '_fit_content'), \
             mock.patch.object(app, '_clamp_saved_position_to_visible_screen'):
            app.update_ui()
        self.assertEqual(app.health_messages, [reason])
        self.assertEqual(app.rows['gpu_fan_control'].options['text'], 'Driver curve')
        app.fan_worker.start.assert_not_called()
        app.gpu_fan_worker.start.assert_not_called()

    def test_diagnostics_failure_does_not_reopen_healthy_monitor(self):
        computer = mock.Mock()
        app = sensor_app(5, computer)
        with mock.patch.object(overlay, "require_hardware_access"), \
             mock.patch.object(overlay, "init_hardware_monitor") as initialize, \
             mock.patch.object(overlay, "read_sensors", return_value={"cpu_temp": 50}) as read, \
             mock.patch.object(overlay, "_read_volume_usage", return_value={"volumes": [], "volume_errors": []}), \
             mock.patch.object(app, "_cache_sensor_diagnostics", side_effect=ValueError("format failed")), \
             mock.patch.object(overlay.psutil, "cpu_percent"), \
             mock.patch.object(overlay, "log"):
            app.sensor_loop()
        self.assertEqual(read.call_count, 5)
        initialize.assert_not_called()
        self.assertEqual(app.sensor_data, {"cpu_temp": 50, "volumes": [], "volume_errors": []})
        computer.Close.assert_called_once_with()

    def test_pause_layout_fits_final_conflict_explanation_before_returning(self):
        from test_overlay_helpers import _update_ui_app

        app = _update_ui_app()
        reason = "Sensors paused: Close hwinfo64.exe and cpuz.exe, then restart HeatMap"
        app._hardware_pause_reason = reason
        app.sensor_data = {"error": reason}
        panel = ["Fresh sensor data unavailable"]
        fitted = []

        def set_panel(messages, _severity):
            panel[:] = messages

        with mock.patch.object(app, "_show_sensor_error", side_effect=lambda **_kwargs: fitted.append(tuple(panel))), \
             mock.patch.object(app, "_set_health_panel", side_effect=set_panel), \
             mock.patch.object(app, "_fit_content", side_effect=lambda: fitted.append(tuple(panel))), \
             mock.patch.object(app, "_clamp_saved_position_to_visible_screen") as clamp:
            app.update_ui()

        self.assertEqual(fitted[-1], (reason,))
        clamp.assert_called_once_with(persist=False)

    def test_pause_preserves_unconfirmed_fan_restore_warning(self):
        from test_overlay_helpers import _update_ui_app

        app = _update_ui_app()
        reason = "Sensors paused: Close cpuz.exe, then restart HeatMap"
        app._hardware_pause_reason = reason
        app.sensor_data = {"error": reason}
        app.fan_worker = mock.Mock()
        app.fan_worker.poll.return_value = {
            "state": "error", "restore_errors": ["System Fan #1: restore failed"],
            "restore_confirmed": False,
        }
        with mock.patch.object(app, "_show_sensor_error"), \
             mock.patch.object(app, "_set_health_panel") as panel, \
             mock.patch.object(app, "_fit_content"), \
             mock.patch.object(app, "_clamp_saved_position_to_visible_screen"):
            app.update_ui()

        messages, severity = panel.call_args.args
        self.assertIn("restore unconfirmed", messages[0])
        self.assertIn("restart Windows", messages[0])
        self.assertIn(reason, messages)
        self.assertEqual(severity, 2)

    def test_conflict_latch_waits_for_concurrent_start_then_stop_wins(self):
        import threading

        app = sensor_app(1)
        app.config = {"case_fans_enabled": False}
        app.fan_worker = mock.Mock(process=None)
        app._save_config = mock.Mock()
        app._set_menu_label = mock.Mock()
        entered_start = threading.Event()
        release_start = threading.Event()
        attempted_pause = threading.Event()
        sensor_finished = threading.Event()
        sequence = []
        errors = []

        def start():
            entered_start.set()
            if not release_start.wait(2):
                raise AssertionError("Concurrent start was not released")
            sequence.append("start")

        def deny_hardware(*_args):
            attempted_pause.set()
            raise HardwareAccessConflict("cpuz.exe")

        def run(action, finished=None):
            try:
                action()
            except Exception as exc:
                errors.append(exc)
            finally:
                if finished is not None:
                    finished.set()

        app.fan_worker.start.side_effect = start
        app.fan_worker.stop.side_effect = lambda: sequence.append("stop")
        ui = threading.Thread(target=run, args=(app.toggle_case_fans,), daemon=True)
        sensor = threading.Thread(target=run, args=(app.sensor_loop, sensor_finished), daemon=True)
        with mock.patch.object(overlay, "_supported_case_fan_board", return_value=True), \
             mock.patch.object(overlay, "require_hardware_access", side_effect=deny_hardware), \
             mock.patch.object(overlay.psutil, "cpu_percent"), mock.patch.object(overlay, "log"):
            try:
                ui.start()
                self.assertTrue(entered_start.wait(2))
                sensor.start()
                self.assertTrue(attempted_pause.wait(2))
                self.assertFalse(sensor_finished.wait(0.05))
            finally:
                release_start.set()
                ui.join(2)
                if sensor.ident is not None:
                    sensor.join(2)

        self.assertFalse(ui.is_alive())
        self.assertFalse(sensor.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(sequence, ["start", "stop"])
        self.assertIn("cpuz.exe", app._hardware_pause_reason)
        app.fan_worker.stop.assert_called_once_with()

    def test_conflict_before_open_latches_without_hardware_access(self):
        app = sensor_app(5)
        app.fan_worker = mock.Mock()
        with mock.patch.object(overlay, "require_hardware_access", side_effect=HardwareAccessConflict("cpuz.exe")), \
             mock.patch.object(overlay, "init_hardware_monitor") as initialize, \
             mock.patch.object(overlay, "read_sensors") as read, \
             mock.patch.object(overlay.psutil, "cpu_percent"):
            app.sensor_loop()
        initialize.assert_not_called()
        read.assert_not_called()
        app.fan_worker.stop.assert_called_once_with()
        self.assertIn("cpuz.exe", app.sensor_data["error"])
        self.assertIn("cpuz.exe", app._hardware_pause_reason)

    def test_arriving_conflict_stops_reads_and_closes_owned_monitor_once(self):
        computer = mock.Mock()
        app = sensor_app(5, computer)
        app.fan_worker = mock.Mock()
        with mock.patch.object(overlay, "require_hardware_access", side_effect=[None, HardwareAccessConflict("hwinfo64.exe")]), \
             mock.patch.object(overlay, "read_sensors", return_value={"cpu_temp": 50}) as read, \
             mock.patch.object(app, "_cache_sensor_diagnostics"), \
             mock.patch.object(overlay.psutil, "cpu_percent"):
            app.sensor_loop()
        read.assert_called_once()
        computer.Close.assert_called_once_with()
        app.fan_worker.stop.assert_called_once_with()
        self.assertIsNone(app.computer)
        self.assertIn("hwinfo64.exe", app._hardware_pause_reason)

    def test_paused_monitor_cannot_restart_fans_from_menu(self):
        app = sensor_app(1)
        app.config = {"case_fans_enabled": False}
        app._hardware_pause_reason = "Close CPU-Z and restart HeatMap"
        app.fan_worker = mock.Mock()
        app._set_health_panel = mock.Mock()
        app.toggle_case_fans()
        app.fan_worker.start.assert_not_called()
        self.assertFalse(app.config["case_fans_enabled"])

    def test_direct_initialization_propagates_access_conflict(self):
        with mock.patch.object(overlay, "require_hardware_access", side_effect=HardwareAccessConflict("blocked")), \
             mock.patch.object(overlay, "_close_hardware_monitor") as close:
            with self.assertRaises(HardwareAccessConflict):
                overlay.init_hardware_monitor()
        close.assert_not_called()


if __name__ == "__main__":
    unittest.main()
