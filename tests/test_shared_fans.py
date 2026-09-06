"""Failure-path and ordering tests; no real hardware is opened."""
from contextlib import nullcontext
from copy import deepcopy
import unittest
from unittest import mock

import case_fans
import pawnio_shared
from shared_fans import SharedFanSession, SHARED_NAMES, REGISTERS, PWM_REGISTERS


class FakeRegisters:
    def __init__(self):
        self.values = dict(zip(REGISTERS, (0x77, 0x80, 0x00, 0x82, 180, 210, 130)))
        self.events = []
        self.fans = [dict(name=n, rpm=r, control_pct=50) for n, r in zip(SHARED_NAMES, (650, 1100, 0))]
        self.fail = None

    def read(self, r):
        return self.values[r]

    def write(self, r, value):
        self.events.append(('register', r, value))
        self.values[r] = value
        if self.fail == r:
            self.fail = None
            raise RuntimeError('partial register write')

    def readings(self):
        return deepcopy(self.fans)


class FakeBridge:
    def __init__(self, events):
        self.mode = 1
        self.events = events
        self.fail = None
    def read_mode(self):
        return self.mode
    def write_mode(self, v):
        self.events.append(('ec', v))
        self.mode = v
        if self.fail == v:
            self.fail = None
            raise RuntimeError('partial EC write')


class SharedFanTests(unittest.TestCase):
    def setup_session(self):
        backend = FakeRegisters()
        bridge = FakeBridge(backend.events)
        session = SharedFanSession(backend, bridge, bus=nullcontext, sleep=lambda _: None)
        return backend, bridge, session

    def test_four_fans_and_empty_six_takeover_without_zero_then_exact_restore(self):
        backend, bridge, session = self.setup_session()
        original = dict(backend.values)
        session.prepare()
        self.assertEqual(backend.events, [])
        session.apply(100)
        self.assertEqual(backend.events[0], ('ec', 0))
        self.assertEqual(backend.events[1:4], [('register', r, 255) for r in PWM_REGISTERS])
        session.apply(60)
        self.assertEqual([backend.values[r] for r in PWM_REGISTERS], [153, 255, 153])
        session.check()
        self.assertEqual(session.restore(), [])
        self.assertEqual(backend.values, original)
        self.assertEqual(bridge.mode, 1)
        self.assertEqual(backend.events[-1], ('ec', 1))
        count = len(backend.events)
        self.assertEqual(session.restore(), [])
        self.assertEqual(len(backend.events), count)
        with self.assertRaises(ValueError):
            session.apply(80)

    def test_refuses_invalid_baselines_without_writes(self):
        for fault in ('busy', 'bits', 'missing', 'stopped', 'extra_fan', 'nan'):
            backend, bridge, session = self.setup_session()
            if fault == 'busy': bridge.mode = 0
            if fault == 'bits': backend.values[0x13] = 6
            if fault == 'missing': backend.fans[0]['rpm'] = None
            if fault == 'stopped': backend.fans[1]['rpm'] = 0
            if fault == 'extra_fan': backend.fans[2]['rpm'] = 500
            if fault == 'nan': backend.fans[0]['rpm'] = float('nan')
            with self.subTest(fault=fault), self.assertRaises(RuntimeError):
                session.prepare()
            self.assertEqual(backend.events, [])

    def test_dynamic_baseline_is_captured_at_takeover(self):
        backend, bridge, session = self.setup_session()
        session.prepare()
        backend.values[0x73] = 175
        session.apply(100)
        self.assertEqual(session.restore(), [])
        self.assertEqual(backend.values[0x73], 175)

    def test_failed_takeover_still_restores(self):
        for failure in ('ec', 0x63, 0x16, 0x73):
            backend, bridge, session = self.setup_session()
            session.prepare()
            original = dict(backend.values)
            if failure == 'ec': bridge.fail = 0
            else: backend.fail = failure
            with self.subTest(failure=failure), self.assertRaises(RuntimeError):
                session.apply(100)
            self.assertTrue(session.touched)
            self.assertEqual(session.restore(), [])
            self.assertEqual(backend.values, original)
            self.assertEqual(bridge.mode, 1)

    def test_failed_register_restore_still_attempts_ec_restore(self):
        backend, bridge, session = self.setup_session()
        session.prepare()
        session.apply(100)
        backend.fail = 0x15
        self.assertTrue(session.restore())
        self.assertEqual(bridge.mode, 1)
        self.assertFalse(session.restored)

    def test_lost_owner_is_detected_before_more_speed_writes(self):
        backend, bridge, session = self.setup_session()
        session.prepare()
        session.apply(100)
        bridge.mode = 1
        before = list(backend.events)
        with self.assertRaises(RuntimeError): session.check()
        with self.assertRaises(RuntimeError): session.apply(60)
        self.assertEqual(backend.events, before)

    def test_invalid_commands_never_write(self):
        backend, bridge, session = self.setup_session()
        session.prepare()
        for value in (None, True, -1, 0, 59, 101, float('nan'), float('inf')):
            with self.subTest(value=value), self.assertRaises(ValueError): session.apply(value)
        self.assertEqual(backend.events, [])

    def test_five_headers_calibrate_only_four_running_fans(self):
        before = [dict(name=n, rpm=r, control_pct=50) for n, r in zip(case_fans.ALL_TARGETS, (800, 800, 650, 800, 0))]
        after = [dict(name=n, rpm=r, control_pct=100) for n, r in zip(case_fans.ALL_TARGETS, (1200, 1200, 1200, 1200, 0))]
        case_fans.verify_full_airflow(before, after)
        self.assertIsNotNone(case_fans.full_rpm_reference({r['name']: r['rpm'] for r in after if r['rpm']}))
        after[2]['rpm'] = 650
        with self.assertRaises(RuntimeError): case_fans.verify_full_airflow(before, after)

    def test_lost_shared_worker_report_does_not_claim_firmware_ownership(self):
        import overlay
        client = case_fans.FanWorkerClient('unused', shared=True)
        with mock.patch.object(client, '_poll_status', return_value=dict(state='error', reason='stale')):
            status = client.poll()
        for name in case_fans.ALL_TARGETS:
            self.assertEqual(overlay._case_fan_owner(status, name), '?')

    def test_mode_lists_four_connected_fans_and_owner_keeps_unused_output(self):
        import overlay
        status = dict(state='active', command_pct=75, controlled_channels=list(case_fans.ALL_TARGETS),
                      unused_channels=[SHARED_NAMES[2]])
        self.assertEqual(overlay._case_fan_mode(status), 'AUTO 75% · SYS 1/2/4/5')
        self.assertEqual(overlay._case_fan_owner(status, SHARED_NAMES[2]), '')

    def test_preacquisition_failure_retains_firmware_evidence(self):
        import overlay
        client = case_fans.FanWorkerClient('unused', shared=True)
        report = dict(state='error', control_attempted=False, baseline=[], controlled_channels=[], restore_errors=[])
        with mock.patch.object(client, '_poll_status', return_value=report):
            status = client.poll()
        self.assertTrue(overlay._case_fan_never_acquired(status))
        self.assertEqual(overlay._case_fan_owner(status, SHARED_NAMES[0]), 'FW')

    def test_corrupt_unused_header_report_does_not_crash_ui(self):
        import overlay
        for unused in (None, 'SYS6', [None], ['CPU Fan']):
            status = dict(state='active', controlled_channels=list(case_fans.ALL_TARGETS), unused_channels=unused)
            self.assertEqual(overlay._case_fan_mode(status), 'ERROR')


class BridgeTransactionTests(unittest.TestCase):
    def test_read_restores_mapping_even_when_ec_access_fails(self):
        bridge = object.__new__(pawnio_shared.SignedEcBridge)
        bridge.original_state = 1
        events = []
        def execute(name, values=(), count=0):
            events.append((name, values))
            if name == 'ioctl_access_superio_mmio': raise RuntimeError('EC read failed')
            if name == 'ioctl_iomem_mmio_get_cur_state': return (2 if len(events) == 3 else 1,)
            return ()
        bridge.execute = execute
        with self.assertRaisesRegex(RuntimeError, 'EC read failed'): bridge.read_mode()
        self.assertEqual(events[-3:], [('ioctl_iomem_mmio_set_state', (-1,)),
            ('ioctl_iomem_mmio_get_cur_state', ()), ('ioctl_unmap_superio_mmio', ())])
        self.assertFalse(any(name == 'ioctl_access_superio_mmio' and args[3] for name, args in events))

    def test_only_known_binary_flag_can_be_written(self):
        bridge = object.__new__(pawnio_shared.SignedEcBridge)
        bridge.execute = mock.Mock()
        for offset, value in ((0x900, 1), (0x948, 0), (0x947, 2), (0x947, True), (0x947, -1)):
            with self.subTest(offset=offset, value=value), self.assertRaises(ValueError): bridge._access(offset, value)
        bridge.execute.assert_not_called()

    def test_live_mode_must_be_binary(self):
        bridge = object.__new__(pawnio_shared.SignedEcBridge)
        for value in (2, 255, -1):
            bridge._access = mock.Mock(return_value=value)
            with self.assertRaises(RuntimeError): bridge.read_mode()

    def test_reviewed_module_matches_manifest(self):
        self.assertEqual(len(pawnio_shared.verified_module()), 51164)
