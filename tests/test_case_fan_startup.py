"""Delayed LHM activation must not turn one busy ISA read into a permanent error."""
import io
import json
import unittest
from unittest import mock

import case_fans as fans
import overlay
from test_case_fans import fixture


class StartupDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.computer, self.controls = fixture()
        self.primary = self.computer.Hardware[0].SubHardware[0]
        self.sensors = list(self.primary.Sensors)
        self.stop = mock.Mock()
        self.stop.is_set.return_value = False
        self.stop.wait.return_value = False
        self.owner = mock.Mock()
        self.owner.is_running.return_value = True
        self.published = []
        self.clock = [100.0]

    def discover(self, read):
        def publish(path, state, **details):
            self.published.append(dict(state=state, **details))
        def wait(delay):
            self.clock[0] += delay
            return False
        def snapshot(computer):
            read(computer)
            return dict(cpu_temp=50, gpu_core_temp=45, gpu_hotspot_temp=60, gpu_memory_temp=60)
        self.stop.wait.side_effect = wait
        with (mock.patch.object(fans, "write_status", side_effect=publish),
              mock.patch.object(fans, "require_hardware_access") as guard,
              mock.patch.object(fans.time, "monotonic", side_effect=lambda: self.clock[0])):
            result = fans.wait_for_controls(self.computer, snapshot, self.stop, self.owner, [100], "unused", timeout=4)
        return result, guard.call_count

    def test_busy_first_read_then_tach_activation_succeeds_without_writes(self):
        def read(_computer):
            self.primary.Sensors = ([s for s in self.sensors if s.SensorType != "Fan"]
                                    if read_mock.call_count == 1 else self.sensors)
        read_mock = mock.Mock(side_effect=read)
        result, checks = self.discover(read_mock)
        self.assertEqual([item[0] for item in result], list(fans.INDEPENDENT_TARGETS))
        self.assertEqual(read_mock.call_count, 2)
        self.assertGreaterEqual(checks, 2)
        self.assertIn("tachometers=0", self.published[0]["reason"])
        self.assertFalse(self.published[0]["control_attempted"])
        for control in self.controls:
            control.SetSoftware.assert_not_called()
            control.SetDefault.assert_not_called()

    def test_null_initial_tach_reading_can_recover(self):
        def read(_computer):
            self.sensors[1].Value = None if read_mock.call_count < 3 else 800
        read_mock = mock.Mock(side_effect=read)
        self.assertEqual(len(self.discover(read_mock)[0]), 2)
        self.assertEqual(read_mock.call_count, 3)

    def test_missing_channel_times_out_without_commands(self):
        self.primary.Sensors = [s for s in self.sensors if s.SensorType != "Fan"]
        read = mock.Mock()
        with self.assertRaisesRegex(fans.StartupNotReady, "timed out after 4s"):
            self.discover(read)
        self.assertEqual(read.call_count, 4)
        self.assertEqual(self.stop.wait.call_count, 4)
        for control in self.controls:
            control.SetSoftware.assert_not_called()

    def test_duplicate_foreign_stopped_and_missing_control_object_never_retry(self):
        for fault in ("duplicate", "identity", "stopped", "control_object"):
            with self.subTest(fault=fault):
                self.setUp()
                if fault == "duplicate":
                    self.primary.Sensors.append(self.sensors[0])
                elif fault == "identity":
                    self.sensors[0].Identifier = "/lpc/other/0/control/1"
                elif fault == "stopped":
                    self.sensors[1].Value = 0
                else:
                    self.sensors[0].Control = None
                read = mock.Mock()
                with self.assertRaises(RuntimeError) as caught:
                    self.discover(read)
                self.assertNotIsInstance(caught.exception, fans.ChannelNotReady)
                self.assertEqual(read.call_count, 1)
                self.stop.wait.assert_not_called()

    def test_cancellation_dead_owner_and_conflict_stop_before_retry(self):
        for cause in ("cancel", "owner", "conflict", "heartbeat"):
            with self.subTest(cause=cause):
                self.setUp()
                self.primary.Sensors = []
                self.stop.is_set.return_value = cause == "cancel"
                self.owner.is_running.return_value = cause != "owner"
                read = mock.Mock()
                with (mock.patch.object(fans, "write_status"),
                      mock.patch.object(fans.time, "monotonic", return_value=120 if cause == "heartbeat" else 100),
                      mock.patch.object(fans, "require_hardware_access", side_effect=RuntimeError("conflict") if cause == "conflict" else None),
                      self.assertRaises(RuntimeError)):
                    fans.wait_for_controls(self.computer, read, self.stop, self.owner, [100], "unused")
                self.assertEqual(read.call_count, 0)
                for control in self.controls:
                    control.SetSoftware.assert_not_called()


class StartupStatusTests(unittest.TestCase):
    def status(self, **fields):
        return dict(profile=fans.PROFILE, state="checking", phase="discovering", control_attempted=False,
                    baseline=[], controlled_channels=[], firmware_channels=[],
                    pid=7, time=100, **fields)

    def test_client_accepts_discovery_but_not_active_with_empty_channels(self):
        for fault in (None, "active", "attempted", "baseline"):
            with self.subTest(fault=fault):
                status = self.status()
                if fault == "active":
                    status.update(state="active", command_pct=100)
                elif fault == "attempted":
                    status["control_attempted"] = True
                elif fault == "baseline":
                    status["baseline"] = [dict(name="System Fan #1")]
                client = fans.FanWorkerClient(".")
                client.started = 90
                client.process = mock.Mock(pid=7, stdin=io.StringIO())
                client.process.poll.return_value = None
                client.status_path = "unused"
                with (mock.patch.object(fans, "open_status_file", return_value=io.StringIO(json.dumps(status))),
                      mock.patch.object(fans.time, "time", return_value=100)):
                    report = client.poll()
                    self.assertEqual(report["state"], "checking" if fault is None else "error")
                    if fault is not None:
                        self.assertEqual(report["reason"], "Missing case fan controller channels")

    def test_explicit_no_takeover_is_distinct_from_unknown_restore(self):
        status = self.status()
        status.update(state="error", reason="Case fan channel not ready")
        self.assertIn("no fan commands sent", overlay._case_fan_advice(status))
        self.assertEqual(overlay._case_fan_owner(status, "System Fan #1"), "FW")
        for fault in ("missing", "attempted", "baseline", "restore"):
            with self.subTest(fault=fault):
                damaged = dict(status)
                if fault == "missing":
                    damaged.pop("control_attempted")
                elif fault == "attempted":
                    damaged["control_attempted"] = True
                elif fault == "baseline":
                    damaged["baseline"] = [dict(name="System Fan #1")]
                else:
                    damaged["restore_errors"] = ["native close failed"]
                self.assertNotIn("no fan commands sent", overlay._case_fan_advice(damaged))
                self.assertEqual(overlay._case_fan_owner(damaged, "System Fan #1"), "?")

    def test_worker_records_no_commands_for_exhausted_startup(self):
        from types import SimpleNamespace as NS
        computer, controls = fixture()
        computer.Hardware[0].SubHardware[0].Sensors = []
        owner = mock.Mock()
        owner.create_time.return_value = 1
        modules = {"clr": mock.Mock(), "LibreHardwareMonitor": mock.Mock(),
                   "LibreHardwareMonitor.Hardware": NS(Computer=lambda: computer)}
        clock = [100.0]
        wait_for_controls = fans.wait_for_controls
        def wait(delay):
            clock[0] += delay
            return False
        with (mock.patch.dict("sys.modules", modules),
              mock.patch.object(fans, "make_shared_computer", return_value=computer) as primed_computer,
              mock.patch.object(overlay, "_is_admin", return_value=True),
              mock.patch.object(overlay, "_runtime_dll_errors", return_value=[]),
              mock.patch.object(overlay, "read_sensors", return_value={}) as read,
              mock.patch.object(fans, "require_hardware_access"),
              mock.patch.object(fans.psutil, "Process", return_value=owner),
              mock.patch.object(fans.threading, "Thread"),
              mock.patch.object(fans.threading.Event, "wait", side_effect=wait),
              mock.patch.object(fans.time, "monotonic", side_effect=lambda: clock[0]),
              mock.patch.object(fans, "wait_for_controls", side_effect=lambda *args, **kwargs:
                                wait_for_controls(*args, **kwargs, timeout=4)),
              mock.patch.object(fans, "write_status") as publish):
            self.assertEqual(fans.worker("unused", 7, 1), 1)
        primed_computer.assert_called_once_with()
        self.assertEqual(read.call_count, 4)
        self.assertFalse(publish.call_args.kwargs["control_attempted"])
        self.assertEqual(publish.call_args.kwargs["baseline"], [])
        computer.Close.assert_called_once_with()
        for control in controls:
            control.SetSoftware.assert_not_called()
            control.SetDefault.assert_not_called()
