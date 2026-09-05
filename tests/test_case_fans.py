import io
import json
import os
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest import mock

import case_fans as fans


def fixture():
    sensors = []
    controls = []
    for name in (*fans.TARGETS, "CPU Fan", "System Fan #5 / Pump", "System Fan #6 / Pump"):
        sensor = NS(Name=name, SensorType="Control", Value=100 if "Pump" in name else None)
        control = mock.Mock(MinSoftwareValue=0, MaxSoftwareValue=100)
        control.SetSoftware.side_effect = lambda value, s=sensor: setattr(s, "Value", value)
        original = sensor.Value
        control.SetDefault.side_effect = lambda s=sensor, v=original: setattr(s, "Value", v)
        sensor.Control = control
        controls.append(control)
        sensors.extend([sensor, NS(Name=name, SensorType="Fan", Value=800)])
    sub = NS(Sensors=sensors, Close=mock.Mock(), Update=mock.Mock())
    board = NS(HardwareType="Motherboard", Model="B550_AORUS_PRO_AC", SubHardware=[sub])
    return NS(Hardware=[board], Open=mock.Mock(), Close=mock.Mock()), controls


class CaseFanTests(unittest.TestCase):
    def test_exact_headers_only_and_restore_original_values(self):
        computer, controls = fixture()
        session = fans.CaseFanSession(fans.select_controls(computer))
        before = session.readings()
        session.apply(100)
        for c in controls[:3]:
            c.SetSoftware.assert_called_once_with(100)
        for c in controls[3:]:
            c.SetSoftware.assert_not_called()
        self.assertEqual(session.restore(), [])
        self.assertEqual(fans.verify_restore(before, session.readings()), [])
        self.assertEqual(session.restore(), [])

    def test_wrong_board_missing_duplicate_stopped_and_shared_curve_rejected(self):
        for fault in ("model", "missing", "duplicate", "stopped", "shared"):
            computer, _ = fixture()
            board = computer.Hardware[0]
            sensors = board.SubHardware[0].Sensors
            if fault == "model":
                board.Model = "B550_AORUS_ELITE"
            elif fault == "missing":
                sensors.pop(0)
            elif fault == "duplicate":
                sensors.append(sensors[0])
            elif fault == "stopped":
                sensors[1].Value = 0
            else:
                sensors[-2].Value = None
            with self.subTest(fault=fault), self.assertRaises(RuntimeError):
                fans.select_controls(computer)

    def test_pythonnet_interface_proxy_uses_concrete_board_model(self):
        computer, _ = fixture()
        board = computer.Hardware[0]
        computer.Hardware[0] = NS(HardwareType="Motherboard", SubHardware=board.SubHardware,
                                  __implementation__=board)
        self.assertEqual(len(fans.select_controls(computer)), 3)

    def test_partial_native_write_restores_even_the_channel_that_raised(self):
        computer, controls = fixture()
        session = fans.CaseFanSession(fans.select_controls(computer))
        controls[1].SetSoftware.side_effect = RuntimeError("partial native failure")
        with self.assertRaises(RuntimeError):
            session.apply(100)
        self.assertEqual(session.restore(), [])
        controls[0].SetDefault.assert_called_once()
        controls[1].SetDefault.assert_called_once()
        controls[2].SetDefault.assert_not_called()

    def test_restore_failure_retains_channel_for_retry(self):
        computer, controls = fixture()
        session = fans.CaseFanSession(fans.select_controls(computer))
        session.apply(100)
        controls[0].SetDefault.side_effect = [RuntimeError("bus"), None]
        self.assertEqual(len(session.restore()), 1)
        self.assertEqual(len(session.touched), 1)
        self.assertEqual(session.restore(), [])

    def test_invalid_commands_cannot_touch_hardware(self):
        computer, controls = fixture()
        session = fans.CaseFanSession(fans.select_controls(computer))
        for bad in (0, 59, 101, float("nan"), True, None):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                session.apply(bad)
        for c in controls:
            c.SetSoftware.assert_not_called()

    def test_command_readback_alone_is_not_success(self):
        baseline = [dict(name="SYS1", rpm=800, control_pct=None)]
        for rpm, pct in ((800, 100), (1200, None), (0, 100)):
            with self.subTest(rpm=rpm, pct=pct), self.assertRaises(RuntimeError):
                fans.verify_full_airflow(baseline, [dict(name="SYS1", rpm=rpm, control_pct=pct)])
        fans.verify_full_airflow(baseline, [dict(name="SYS1", rpm=1200, control_pct=100)])

    def test_silent_restore_failure_detected(self):
        self.assertTrue(fans.verify_restore([dict(name="SYS1", control_pct=None)], [dict(control_pct=100)]))
        self.assertTrue(fans.verify_restore([dict(name="SYS1", control_pct=59)], [dict(control_pct=100)]))

    def test_client_rejects_dead_stale_wrong_pid_and_future_active_status(self):
        with tempfile.TemporaryDirectory() as directory:
            client = fans.FanWorkerClient(directory)
            client.status_path = os.path.join(directory, "state.json")
            client.started = 100
            client.process = mock.Mock(pid=7, stdin=io.StringIO())
            for pid, stamp, exitcode in ((7, 100, 1), (7, 80, None), (8, 100, None), (7, 110, None)):
                with open(client.status_path, "w") as stream:
                    json.dump(dict(pid=pid, time=stamp, state="active"), stream)
                client.process.poll.return_value = exitcode
                with mock.patch.object(fans.time, "time", return_value=100):
                    self.assertEqual(client.poll()["state"], "error")

    def test_stop_uses_eof_without_killing_controller(self):
        client = fans.FanWorkerClient(".")
        client.process = mock.Mock(stdin=io.StringIO())
        client.stop()
        self.assertTrue(client.process.stdin.closed)
        client.process.kill.assert_not_called()
        client.process.terminate.assert_not_called()

    def test_worker_restores_after_read_failure_and_after_owner_dies(self):
        import overlay
        for failure in (True, False):
            computer, controls = fixture()
            owner = mock.Mock()
            owner.create_time.return_value = 1
            owner.is_running.side_effect = [True, True, False] if not failure else [True, True, True]
            modules = {"clr": mock.Mock(), "LibreHardwareMonitor": mock.Mock(),
                       "LibreHardwareMonitor.Hardware": NS(Computer=lambda: computer)}
            with tempfile.TemporaryDirectory() as directory, mock.patch.dict("sys.modules", modules), \
                 mock.patch.object(overlay, "_is_admin", return_value=True), \
                 mock.patch.object(overlay, "_runtime_dll_errors", return_value=[]), \
                 mock.patch.object(fans.psutil, "process_iter", return_value=[]), \
                 mock.patch.object(fans.psutil, "Process", return_value=owner), \
                 mock.patch.object(fans.threading, "Thread"), \
                 mock.patch.object(overlay, "read_sensors", side_effect=[{}, RuntimeError("read failed")] if failure else [{}, {}]):
                path = os.path.join(directory, "status.json")
                result = fans.worker(path, 7, 1)
                self.assertEqual(result, 1 if failure else 0)
                for control in controls[:3]:
                    control.SetDefault.assert_called_once()
                computer.Close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
