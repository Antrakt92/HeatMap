"""Hardware-free failure regressions for the release fan-controller audit."""
import unittest
from unittest import mock

import case_fans
from test_case_fan_fallback import controller_topology
import test_shared_fans


class CaseReleaseAuditTests(unittest.TestCase):
    def shared_session(self):
        backend, bridge, session = test_shared_fans.SharedFanTests().setup_session()
        session.prepare()
        session.apply(100)
        backend.events.clear()
        return backend, bridge, session

    def test_failed_restore_mode_read_still_attempts_firmware_enable(self):
        backend, bridge, session = self.shared_session()
        read_mode = bridge.read_mode
        bridge.read_mode = mock.Mock(side_effect=[RuntimeError('mode read failed'), 1])
        errors = session.restore()
        self.assertIn(('ec', 1), backend.events)
        self.assertEqual(bridge.mode, 1)
        self.assertTrue(errors)
        self.assertFalse(session.restored)
        bridge.read_mode = read_mode

    def test_failed_restore_register_read_still_attempts_firmware_enable(self):
        backend, bridge, session = self.shared_session()
        bridge.mode = 1
        backend.read = mock.Mock(side_effect=RuntimeError('register read failed'))
        self.assertTrue(session.restore())
        self.assertIn(('ec', 1), backend.events)
        self.assertFalse(session.restored)

    def test_shared_manual_mode_drift_is_detected_without_further_commands(self):
        for operation in ('check', 'apply'):
            with self.subTest(operation=operation):
                backend, bridge, session = self.shared_session()
                backend.values[0x15] |= 0x80
                with self.assertRaisesRegex(RuntimeError, 'mode'):
                    session.check() if operation == 'check' else session.apply(70)
                self.assertEqual(backend.events, [])

    def test_shared_restore_exception_does_not_skip_primary_cleanup(self):
        computer, channels = controller_topology()
        shared = mock.Mock()
        session = case_fans.CaseFanSession(case_fans.select_controls(computer), shared)
        session.apply(100)
        shared.restore.side_effect = RuntimeError('bridge failed')
        errors = session.restore()
        self.assertTrue(any('bridge failed' in error for error in errors))
        for name in case_fans.INDEPENDENT_TARGETS:
            channels[name].control.SetDefault.assert_called_once_with()

    def test_nonfinite_control_limits_never_authorize_takeover(self):
        for attribute in ('MinSoftwareValue', 'MaxSoftwareValue'):
            for value in (float('nan'), float('inf'), -float('inf')):
                with self.subTest(attribute=attribute, value=value):
                    computer, channels = controller_topology()
                    setattr(channels[case_fans.INDEPENDENT_TARGETS[0]].control, attribute, value)
                    with self.assertRaisesRegex(RuntimeError, 'control range'):
                        case_fans.select_controls(computer)


if __name__ == '__main__':
    unittest.main()
