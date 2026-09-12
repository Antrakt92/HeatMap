import unittest
from unittest import mock

import overlay
from hardware_access_guard import HardwareAccessConflict
from test_sensor_lifecycle import sensor_app


class SensorAccessPauseTests(unittest.TestCase):
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
        from test_overlay_helpers import _FakeLabel, _update_ui_app

        app = _update_ui_app()
        reason = "Sensors paused: Close cpuz.exe, then restart HeatMap"
        app._hardware_pause_reason = reason
        app.sensor_data = {"error": reason}
        app.rows["case_fan_control"] = _FakeLabel()
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

        def deny_hardware():
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
        with mock.patch.object(overlay, "require_hardware_access", side_effect=deny_hardware), \
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
