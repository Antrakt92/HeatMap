"""Startup readiness waits are read-only and preserve terminal safety failures."""
import io
import json
import unittest
import threading
from unittest import mock

import case_fans
import shared_fans
from test_case_fan_fallback import controller_topology


READY = dict(cpu_temp=50, gpu_core_temp=45, gpu_hotspot_temp=60, gpu_memory_temp=60)


class CaseReadinessTests(unittest.TestCase):
    def setUp(self):
        self.computer, self.channels = controller_topology()
        self.channels[shared_fans.SHARED_NAMES[2]].tach.Value = 0
        self.clock = [100.0]
        self.heartbeat = [100.0]
        self.stop = mock.Mock()
        self.stop.is_set.return_value = False
        self.owner = mock.Mock()
        self.owner.is_running.return_value = True
        self.statuses = []
        self.stop.wait.side_effect = self.advance

    def advance(self, seconds):
        self.clock[0] += seconds
        self.heartbeat[0] = self.clock[0]
        return False

    def wait(self, read, shared=True, timeout=60):
        with (mock.patch.object(case_fans.time, 'monotonic', side_effect=lambda: self.clock[0]),
              mock.patch.object(case_fans, 'require_hardware_access'),
              mock.patch.object(case_fans, 'write_status', side_effect=lambda path, state, **details:
                                self.statuses.append(dict(state=state, **details)))):
            return case_fans.wait_for_controls(self.computer, read, self.stop, self.owner,
                                              self.heartbeat, 'unused', shared=shared, timeout=timeout)

    def assert_no_writes(self):
        for channel in self.channels.values():
            channel.control.SetSoftware.assert_not_called()
            channel.control.SetDefault.assert_not_called()

    def test_secondary_controller_can_appear_later_than_previous_fixed_retry_window(self):
        board = self.computer.Hardware[0]
        secondary = board.SubHardware.pop()
        def read(_computer):
            if self.clock[0] >= 108 and secondary not in board.SubHardware:
                board.SubHardware.append(secondary)
            return READY
        selected = self.wait(read)
        self.assertEqual(len(selected), 2)
        self.assertEqual(len(self.statuses), 8)
        self.assertTrue(all(status['phase'] == 'waiting' and status['control_attempted'] is False
                            and status['baseline'] == [] and status['controlled_channels'] == []
                            and status['firmware_channels'] == [] for status in self.statuses))
        self.assertEqual([status['remaining_seconds'] for status in self.statuses], list(range(60, 52, -1)))
        self.assert_no_writes()

    def test_missing_shared_tach_and_temperatures_can_recover_before_any_takeover(self):
        tach = self.channels[shared_fans.SHARED_NAMES[0]].tach
        def read(_computer):
            tach.Value = None if self.clock[0] < 102 else 900
            return dict(READY, gpu_hotspot_temp=None if self.clock[0] < 104 else 65)
        self.assertEqual(len(self.wait(read)), 2)
        self.assertIn('shared channel reading', self.statuses[0]['reason'])
        self.assertIn('Hotspot temperature', self.statuses[-1]['reason'])
        self.assert_no_writes()

    def test_unknown_firmware_duty_does_not_require_software_control_to_discover(self):
        for channel in self.channels.values():
            channel.sensor.Value = None
        self.assertEqual(len(self.wait(lambda _computer: READY)), 2)
        self.assertEqual(self.statuses, [])
        self.assert_no_writes()

    def test_each_required_missing_temperature_times_out_without_commands(self):
        for key in READY:
            with self.subTest(key=key):
                self.setUp()
                with self.assertRaisesRegex(case_fans.StartupNotReady, 'timed out'):
                    self.wait(lambda _computer: dict(READY, **{key: None}), timeout=3)
                self.assertEqual(len(self.statuses), 3)
                self.assert_no_writes()

    def test_invalid_temperature_is_terminal_instead_of_waited_for(self):
        for value in (0, True, '50', 151, float('nan'), float('inf')):
            with self.subTest(value=value):
                self.setUp()
                with self.assertRaisesRegex(RuntimeError, 'Invalid CPU temperature'):
                    self.wait(lambda _computer: dict(READY, cpu_temp=value))
                self.assertEqual(self.statuses, [])
                self.stop.wait.assert_not_called()
                self.assert_no_writes()

    def test_secondary_invalid_identity_duplicate_stopped_and_control_object_are_terminal(self):
        for fault in ('foreign', 'renamed', 'duplicate', 'stopped', 'control', 'used_six'):
            with self.subTest(fault=fault):
                self.setUp()
                channel = self.channels[shared_fans.SHARED_NAMES[0]]
                if fault == 'foreign': channel.sensor.Identifier = '/lpc/other/0/control/2'
                elif fault == 'renamed': channel.sensor.Name = 'Other Fan'
                elif fault == 'duplicate': channel.chip.Sensors.append(channel.sensor)
                elif fault == 'stopped': channel.tach.Value = 0
                elif fault == 'control': channel.sensor.Control = None
                elif fault == 'used_six': self.channels[shared_fans.SHARED_NAMES[2]].tach.Value = 900
                with self.assertRaises(RuntimeError) as caught:
                    self.wait(lambda _computer: READY)
                self.assertNotIsInstance(caught.exception, case_fans.StartupNotReady)
                self.stop.wait.assert_not_called()
                self.assert_no_writes()

    def test_owner_cancellation_after_ready_probe_still_prevents_takeover(self):
        def read(_computer):
            self.owner.is_running.return_value = False
            return READY
        with self.assertRaises(case_fans.StartupCancelled):
            self.wait(read)
        self.assert_no_writes()

    def test_malformed_or_nonfinite_tach_is_terminal_on_both_controllers(self):
        for name in (case_fans.INDEPENDENT_TARGETS[0], shared_fans.SHARED_NAMES[0]):
            for rpm in (True, '900', float('nan'), float('inf')):
                with self.subTest(name=name, rpm=rpm):
                    self.setUp()
                    self.channels[name].tach.Value = rpm
                    with self.assertRaises(RuntimeError) as caught:
                        self.wait(lambda _computer: READY)
                    self.assertNotIsInstance(caught.exception, case_fans.StartupNotReady)
                    self.stop.wait.assert_not_called()
                    self.assert_no_writes()

    def test_client_accepts_waiting_with_explicit_no_takeover_evidence(self):
        client = case_fans.FanWorkerClient('.', shared=True)
        client.started = 90
        client.process = mock.Mock(pid=7, stdin=io.StringIO())
        client.process.poll.return_value = None
        client.status_path = 'unused'
        status = dict(profile=case_fans.PROFILE, state='checking', phase='waiting', control_attempted=False,
                      baseline=[], controlled_channels=[], firmware_channels=[],
                      pid=7, time=100, remaining_seconds=45)
        with (mock.patch.object(case_fans, 'open_status_file', return_value=io.StringIO(json.dumps(status))),
              mock.patch.object(case_fans.time, 'time', return_value=100)):
            self.assertEqual(client.poll(), status)

    def test_normal_cancellation_during_ready_probe_reports_stopped_without_commands(self):
        import overlay
        from test_case_fans import fixture
        computer, controls = fixture()
        stop = threading.Event()
        owner = mock.Mock()
        owner.create_time.return_value = 1
        def read(_computer):
            stop.set()
            return READY
        with (mock.patch.dict('sys.modules', {'clr': mock.Mock()}),
              mock.patch.object(case_fans, 'make_shared_computer', return_value=computer),
              mock.patch.object(case_fans, 'require_hardware_access'),
              mock.patch.object(case_fans.psutil, 'Process', return_value=owner),
              mock.patch.object(case_fans.threading, 'Thread'),
              mock.patch.object(case_fans.threading, 'Event', return_value=stop),
              mock.patch.object(case_fans, 'write_status') as publish,
              mock.patch.object(overlay, '_is_admin', return_value=True),
              mock.patch.object(overlay, '_runtime_dll_errors', return_value=[]),
              mock.patch.object(overlay, 'read_sensors', side_effect=read)):
            self.assertEqual(case_fans.worker('unused', 7, 1), 0)
        self.assertEqual(publish.call_args.args[1], 'stopped')
        self.assertIs(publish.call_args.kwargs['control_attempted'], False)
        self.assertEqual(publish.call_args.kwargs['baseline'], [])
        self.assertEqual(publish.call_args.kwargs['controlled_channels'], [])
        for control in controls:
            control.SetSoftware.assert_not_called()
            control.SetDefault.assert_not_called()
        computer.Close.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
