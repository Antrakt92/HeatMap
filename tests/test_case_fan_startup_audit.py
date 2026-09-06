"""Normal launches reuse calibration without a fresh full-speed fan sweep."""
from contextlib import nullcontext
import threading
import unittest
from unittest import mock

import case_fans
import overlay
import shared_fans
from test_case_fans import fixture
from test_shared_fans import FakeBridge, FakeRegisters


COOL = dict(cpu_temp=40, gpu_core_temp=40, gpu_hotspot_temp=60, gpu_memory_temp=60)


class CaseFanStartupAuditTests(unittest.TestCase):
    def run_worker(self, reference, snapshots=None, mutate=None, commission=False,
                   cancellation=None):
        computer, controls = fixture()
        stop = threading.Event()
        clock = [100.0]
        owner = mock.Mock()
        owner.create_time.return_value = 1
        owner.is_running.return_value = True
        reports = []
        reads = [0]
        fan_reads = [0]
        original_readings = case_fans.CaseFanSession.readings

        def cancel():
            if cancellation.endswith('stop'):
                stop.set()
            elif cancellation.endswith('expired'):
                clock[0] += 16
            else:
                owner.is_running.return_value = False

        def readings(session):
            fan_reads[0] += 1
            result = original_readings(session)
            if cancellation and cancellation.startswith('read_') and fan_reads[0] == 2:
                cancel()
            return result

        def hardware_guard():
            if cancellation and cancellation.startswith('guard_') and fan_reads[0] > 0:
                cancel()

        def read(_computer):
            reads[0] += 1
            if mutate is not None:
                mutate(computer, reads[0])
            source = snapshots or [COOL]
            return dict(source[min(reads[0] - 1, len(source) - 1)])

        def wait(seconds):
            clock[0] += seconds
            if clock[0] >= 104:
                stop.set()
            return stop.is_set()

        with (mock.patch.dict('sys.modules', {'clr': mock.Mock()}),
              mock.patch.object(case_fans, 'make_shared_computer', return_value=computer),
              mock.patch.object(case_fans, 'require_hardware_access', side_effect=hardware_guard),
              mock.patch.object(case_fans.CaseFanSession, 'readings', readings),
              mock.patch.object(case_fans.psutil, 'Process', return_value=owner),
              mock.patch.object(case_fans.threading, 'Thread'),
              mock.patch.object(case_fans.threading, 'Event', return_value=stop),
              mock.patch.object(stop, 'wait', side_effect=wait),
              mock.patch.object(case_fans.time, 'monotonic', side_effect=lambda: clock[0]),
              mock.patch.object(case_fans, 'write_status', side_effect=lambda path, state, **details:
                                reports.append(dict(state=state, **details))),
              mock.patch.object(overlay, '_is_admin', return_value=True),
              mock.patch.object(overlay, '_runtime_dll_errors', return_value=[]),
              mock.patch.object(overlay, 'read_sensors', side_effect=read)):
            result = case_fans.worker('unused', 7, 1, full_rpm=reference, commission=commission)
        return result, controls, reports

    def test_calibrated_cool_restart_starts_at_current_demand_without_full_sweep(self):
        reference = {name: 1200 for name in case_fans.INDEPENDENT_TARGETS}
        result, controls, reports = self.run_worker(reference)
        self.assertEqual(result, 0)
        for control in controls[:2]:
            self.assertEqual(control.SetSoftware.call_args_list, [mock.call(60.0)])
            control.SetDefault.assert_called_once()
        active = [report for report in reports if report['state'] == 'active']
        self.assertEqual(reports[0]['state'], 'checking')
        self.assertEqual(reports[0]['command_pct'], 60)
        self.assertTrue(active)
        self.assertTrue(all(report['verified_full_rpm'] is None for report in active))

    def test_unknown_or_other_profile_still_requires_full_airflow_validation(self):
        for reference in (None, {}, {name: 1200 for name in case_fans.ALL_TARGETS[:-1]},
                          {name: 1200 for name in case_fans.TARGETS}):
            with self.subTest(reference=reference):
                result, controls, reports = self.run_worker(reference)
                self.assertEqual(result, 0)
                for control in controls[:2]:
                    control.SetSoftware.assert_called_once_with(100.0)
                checking = [report for report in reports if report['state'] == 'checking']
                self.assertTrue(checking)
                self.assertEqual(checking[0]['reason'], 'Checking full airflow')

    def test_explicit_commission_repeats_full_airflow_even_with_cached_reference(self):
        reference = {name: 1200 for name in case_fans.INDEPENDENT_TARGETS}
        result, controls, reports = self.run_worker(reference, commission=True)
        self.assertEqual(result, 0)
        for control in controls[:2]:
            control.SetSoftware.assert_called_once_with(100.0)
        self.assertFalse(any(report['state'] == 'active' for report in reports))

    def test_client_transports_commission_only_when_explicitly_requested(self):
        for commission in (False, True):
            client = case_fans.FanWorkerClient('.', commission=commission)
            with (mock.patch.object(case_fans.os, 'makedirs'),
                  mock.patch.object(case_fans.subprocess, 'Popen') as launch):
                client.start()
            self.assertEqual('--commission' in launch.call_args.args[0], commission)

    def test_calibrated_restart_still_goes_full_for_new_heat_or_missing_sensor(self):
        reference = {name: 1200 for name in case_fans.INDEPENDENT_TARGETS}
        for key, value in (('cpu_temp', 85), ('gpu_hotspot_temp', 100),
                           ('gpu_memory_temp', None)):
            with self.subTest(key=key):
                result, controls, _reports = self.run_worker(reference, [COOL, dict(COOL, **{key: value})])
                self.assertEqual(result, 0)
                for control in controls[:2]:
                    control.SetSoftware.assert_called_once_with(100.0)

    def test_cached_calibration_does_not_bypass_command_feedback_conflicts(self):
        reference = {name: 1200 for name in case_fans.INDEPENDENT_TARGETS}

        def conflict(computer, count):
            if count == 3:
                computer.Hardware[0].SubHardware[0].Sensors[0].Value = 90

        result, controls, reports = self.run_worker(reference, mutate=conflict)
        self.assertEqual(result, 1)
        self.assertIn('readback differs', reports[-1]['reason'])
        for control in controls[:2]:
            control.SetDefault.assert_called_once()

    def test_profile_matching_requires_all_and_only_connected_fans(self):
        reference = {name: 1200 for name in case_fans.ALL_TARGETS[:-1]}
        self.assertEqual(case_fans.commissioned_rpm_reference(reference, shared=True), reference)
        self.assertIsNone(case_fans.commissioned_rpm_reference(reference))
        reference[case_fans.ALL_TARGETS[-1]] = 1200
        self.assertIsNone(case_fans.commissioned_rpm_reference(reference, shared=True))

    def test_slow_fan_read_cannot_write_after_owner_stop_or_expiry(self):
        reference = {name: 1200 for name in case_fans.INDEPENDENT_TARGETS}
        for cancellation in ('read_stop', 'read_expired', 'read_owner'):
            with self.subTest(cancellation=cancellation):
                result, controls, reports = self.run_worker(reference, cancellation=cancellation)
                self.assertEqual(result, 0)
                self.assertFalse(reports[-1]['control_attempted'])
                for control in controls:
                    control.SetSoftware.assert_not_called()

    def test_slow_hardware_guard_cannot_begin_full_sweep_after_owner_stop(self):
        for cancellation in ('guard_stop', 'guard_expired', 'guard_owner'):
            with self.subTest(cancellation=cancellation):
                result, controls, reports = self.run_worker(None, cancellation=cancellation)
                self.assertEqual(result, 0)
                self.assertFalse(reports[-1]['control_attempted'])
                for control in controls:
                    control.SetSoftware.assert_not_called()

    def test_shared_activation_primes_requested_nonzero_duty_before_manual_mode(self):
        backend = FakeRegisters()
        bridge = FakeBridge(backend.events)
        original = dict(backend.values)
        session = shared_fans.SharedFanSession(backend, bridge, bus=nullcontext, sleep=lambda _: None)
        session.prepare()
        session.apply(60)
        self.assertEqual(backend.events[:4], [('ec', 0), ('register', 0x63, 153),
                                            ('register', 0x6B, 255), ('register', 0x73, 153)])
        self.assertFalse(any(event[1] in (0x63, 0x73) and event[2] in (0, 255)
                             for event in backend.events if event[0] == 'register'))
        self.assertEqual(session.restore(), [])
        self.assertEqual(backend.values, original)


if __name__ == '__main__':
    unittest.main()
