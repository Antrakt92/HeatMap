import unittest
from unittest import mock

import enable_case_fans
from tools import commission_gpu_fans


class ReadinessCommissionTests(unittest.TestCase):
    def test_late_readiness_still_allows_airflow_and_sustained_verification(self):
        for module, verify in ((enable_case_fans, enable_case_fans.verify_worker),
                               (commission_gpu_fans, commission_gpu_fans.verify)):
            with self.subTest(module=module.__name__):
                now = [0.0]
                stopped = [False]
                client = mock.Mock()
                client.stop.side_effect = lambda: stopped.__setitem__(0, True)

                def poll():
                    if stopped[0]:
                        return dict(state='stopped', restore_confirmed=True, restore_errors=[])
                    evidence = (3400 if module is commission_gpu_fans else
                                {'System Fan #1': 1200, 'System Fan #2': 1200})
                    return dict(state='checking') if now[0] < 76 else dict(
                        state='active', time=100 + now[0], verified_full_rpm=evidence)

                client.poll.side_effect = poll
                with (mock.patch.object(module.time, 'monotonic', side_effect=lambda: now[0]),
                      mock.patch.object(module.time, 'sleep', side_effect=lambda delay: now.__setitem__(0, now[0]+delay))):
                    samples = []
                    result = verify(client, samples, duration=8)
                self.assertTrue(result['restore_confirmed'])
                self.assertGreaterEqual(len(samples), 5)
                client.stop.assert_called_once()

    def test_gpu_startup_cause_requires_no_command_and_no_recovery_evidence(self):
        for pending in (False, True, None):
            client = mock.Mock()
            status = dict(state='error', reason='GPU startup timed out', control_attempted=False,
                          baseline=None, recovery_pending=pending, restore_confirmed=False, restore_errors=[])
            client.poll.side_effect = [status, status]
            expected = 'startup timed out' if pending is False else 'restoration not confirmed'
            with self.subTest(pending=pending), self.assertRaisesRegex(RuntimeError, expected):
                commission_gpu_fans.verify(client, [])

    def test_invalid_duration_does_not_start_hardware(self):
        for verify in (enable_case_fans.verify_worker, commission_gpu_fans.verify):
            for value in (True, -1, float('nan'), float('inf'), '10'):
                client = mock.Mock()
                with self.subTest(value=value), self.assertRaises(ValueError):
                    verify(client, [], duration=value)
                client.start.assert_not_called()
