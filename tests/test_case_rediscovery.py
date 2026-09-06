"""An empty LHM LPC topology is fixed only by fresh motherboard discovery."""
from types import SimpleNamespace as NS
import unittest
from unittest import mock

import case_fans
import test_case_readiness as readiness

READY = readiness.READY


class RediscoveryTests(unittest.TestCase):
    advance = readiness.CaseReadinessTests.advance
    wait = readiness.CaseReadinessTests.wait
    assert_no_writes = readiness.CaseReadinessTests.assert_no_writes

    def setUp(self):
        readiness.CaseReadinessTests.setUp(self)
        self.ready_board = self.computer.Hardware[0]
        self.transitions = []
        self.on_enable = lambda: [self.ready_board]
        self.on_disable = lambda: None
        test = self

        class Computer:
            Hardware = [NS(HardwareType='Motherboard', Model='B550_AORUS_PRO_AC', SubHardware=[])]
            enabled = True

            @property
            def IsMotherboardEnabled(self):
                return self.enabled

            @IsMotherboardEnabled.setter
            def IsMotherboardEnabled(self, enabled):
                test.transitions.append(enabled)
                if enabled:
                    self.Hardware = test.on_enable()
                else:
                    test.on_disable()
                    self.Hardware = []
                self.enabled = enabled

        self.computer = Computer()

    def test_empty_boot_topology_is_replaced_and_returns_fresh_controls(self):
        selected = self.wait(lambda _computer: READY)
        self.assertEqual([item[0] for item in selected], list(case_fans.INDEPENDENT_TARGETS))
        self.assertEqual(self.transitions, [False, True])
        self.assertIs(selected[0][1], self.channels[case_fans.INDEPENDENT_TARGETS[0]].control)
        self.assert_no_writes()

    def test_fifth_discovery_can_recover_without_extending_the_deadline(self):
        empty = self.computer.Hardware
        self.on_enable = lambda: [self.ready_board] if len(self.transitions) == 10 else empty
        self.assertEqual(len(self.wait(lambda _computer: READY)), 2)
        self.assertEqual(self.transitions, [False, True] * 5)
        self.assertLess(self.clock[0], 160)
        self.assert_no_writes()

    def test_permanently_empty_topology_is_bounded_even_with_longer_timeout(self):
        empty = self.computer.Hardware
        self.on_enable = lambda: empty
        with self.assertRaisesRegex(case_fans.StartupNotReady, 'timed out'):
            self.wait(lambda _computer: READY, timeout=90)
        self.assertEqual(self.transitions, [False, True] * 5)
        self.assertEqual(self.statuses[-1]['discovery']['motherboard_reopens'], 5)
        self.assertEqual(self.statuses[-1]['discovery']['controller_ids'], [])
        self.assert_no_writes()

    def test_existing_partial_controller_or_missing_reading_is_never_reopened(self):
        for fault in ('secondary_absent', 'primary_absent', 'tach_missing', 'temperature_missing', 'foreign_chip'):
            with self.subTest(fault=fault):
                self.setUp()
                self.computer.Hardware = [self.ready_board]
                data = READY
                if fault == 'secondary_absent':
                    self.ready_board.SubHardware.pop()
                elif fault == 'primary_absent':
                    self.ready_board.SubHardware.pop(0)
                elif fault == 'tach_missing':
                    self.channels[case_fans.INDEPENDENT_TARGETS[0]].tach.Value = None
                elif fault == 'temperature_missing':
                    data = dict(READY, gpu_hotspot_temp=None)
                else:
                    self.ready_board.SubHardware = [NS(Identifier='/lpc/unknown/0', Sensors=[])]
                with self.assertRaises(case_fans.StartupNotReady):
                    self.wait(lambda _computer: data)
                self.assertEqual(self.transitions, [])
                self.assert_no_writes()

    def test_new_topology_is_validated_again_before_returning_controls(self):
        for fault in ('wrong_board', 'duplicate', 'stopped', 'no_control', 'used_six'):
            with self.subTest(fault=fault):
                self.setUp()
                channel = self.channels[case_fans.INDEPENDENT_TARGETS[0]]
                if fault == 'wrong_board':
                    self.ready_board.Model = 'OTHER'
                elif fault == 'duplicate':
                    channel.chip.Sensors.append(channel.sensor)
                elif fault == 'stopped':
                    channel.tach.Value = 0
                elif fault == 'no_control':
                    channel.sensor.Control = None
                else:
                    self.channels[case_fans.SHARED_NAMES[2]].tach.Value = 800
                with self.assertRaises(RuntimeError) as caught:
                    self.wait(lambda _computer: READY)
                self.assertNotIsInstance(caught.exception, case_fans.StartupNotReady)
                self.assertEqual(self.transitions, [False, True])
                self.assert_no_writes()

    def test_native_group_close_or_open_failure_is_terminal(self):
        for phase in ('close', 'open'):
            with self.subTest(phase=phase):
                self.setUp()
                failure = mock.Mock(side_effect=RuntimeError('native group failure'))
                if phase == 'close':
                    self.on_disable = failure
                else:
                    self.on_enable = failure
                with self.assertRaisesRegex(RuntimeError, 'native group failure'):
                    self.wait(lambda _computer: READY)
                self.assertEqual(self.transitions, [False] if phase == 'close' else [False, True])
                failure.assert_called_once_with()
                self.assert_no_writes()

    def test_cancellation_owner_loss_and_heartbeat_loss_during_close_prevent_open(self):
        for fault in ('cancel', 'owner', 'heartbeat', 'deadline'):
            with self.subTest(fault=fault):
                self.setUp()
                def interrupt():
                    if fault == 'cancel':
                        self.stop.is_set.return_value = True
                    elif fault == 'owner':
                        self.owner.is_running.return_value = False
                    elif fault == 'heartbeat':
                        self.heartbeat[0] -= 16
                    else:
                        self.clock[0] += 60
                        self.heartbeat[0] = self.clock[0]
                self.on_disable = interrupt
                with self.assertRaises((case_fans.StartupCancelled, case_fans.StartupNotReady)):
                    self.wait(lambda _computer: READY)
                self.assertEqual(self.transitions, [False])
                self.assert_no_writes()

    def test_cancellation_or_deadline_during_open_rejects_fresh_controls(self):
        for fault in ('cancel', 'deadline'):
            with self.subTest(fault=fault):
                self.setUp()
                def interrupt():
                    if fault == 'cancel':
                        self.stop.is_set.return_value = True
                    else:
                        self.clock[0] += 60
                        self.heartbeat[0] = self.clock[0]
                    return [self.ready_board]
                self.on_enable = interrupt
                with self.assertRaises((case_fans.StartupCancelled, case_fans.StartupNotReady)):
                    self.wait(lambda _computer: READY)
                self.assertEqual(self.transitions, [False, True])
                self.assert_no_writes()

    def test_conflict_between_group_close_and_open_is_terminal(self):
        from hardware_access_guard import HardwareAccessConflict
        def conflict():
            case_fans.require_hardware_access.side_effect = HardwareAccessConflict('competing tool')
        self.on_disable = conflict
        with self.assertRaises(HardwareAccessConflict):
            self.wait(lambda _computer: READY)
        self.assertEqual(self.transitions, [False])
        self.assert_no_writes()

    def test_slow_status_publication_does_not_extend_rediscovery_deadline(self):
        test = self
        class SlowStatuses(list):
            def append(self, status):
                super().append(status)
                if test.clock[0] >= 110:
                    test.clock[0] += 60
                    test.heartbeat[0] = test.clock[0]
        self.statuses = SlowStatuses()
        with self.assertRaisesRegex(case_fans.StartupNotReady, 'timed out'):
            self.wait(lambda _computer: READY)
        self.assertEqual(self.transitions, [])
        self.assert_no_writes()
