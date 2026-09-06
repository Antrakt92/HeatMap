import unittest
from unittest.mock import Mock, patch

import enable_case_fans
from tools import commission_gpu_fans


class CommissionEvidenceTests(unittest.TestCase):
    def verify(self, module, reports, duration=0):
        clock = [0.0]
        stopped = [False]
        snapshots = iter(reports)
        current = [None]
        client = Mock()
        client.stop.side_effect = lambda: stopped.__setitem__(0, True)

        def poll():
            if stopped[0]:
                return dict(state='stopped', restore_confirmed=True, restore_errors=[])
            current[0] = next(snapshots, current[0])
            return current[0]

        client.poll.side_effect = poll
        verify = module.verify if module is commission_gpu_fans else module.verify_worker
        with patch.object(module.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(module.time, 'sleep', side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)):
            return verify(client, [], duration=duration)

    def test_ordinary_active_cannot_be_mistaken_for_full_airflow_proof(self):
        for module in (commission_gpu_fans, enable_case_fans):
            with self.subTest(module=module.__name__), self.assertRaisesRegex(RuntimeError, 'full.airflow'):
                self.verify(module, [dict(state='active', time=100)])

    def test_gpu_rejects_invalid_full_airflow_proof(self):
        for rpm in (None, True, 0, 2400, float('nan'), float('inf'), 10001):
            with self.subTest(rpm=rpm), self.assertRaisesRegex(RuntimeError, 'full.airflow'):
                self.verify(commission_gpu_fans, [dict(state='active', time=100, verified_full_rpm=rpm)])

    def test_gpu_backward_timestamp_cannot_prove_sustained_operation(self):
        reports = [dict(state='active', time=stamp, verified_full_rpm=3400) for stamp in (100, 99)]
        with self.assertRaisesRegex(RuntimeError, 'timestamp'):
            self.verify(commission_gpu_fans, reports, duration=2)

    def test_gpu_fast_reports_do_not_substitute_for_elapsed_sensor_window(self):
        reports = [dict(state='active', time=stamp, verified_full_rpm=3400) for stamp in (100, 100.1)]
        with self.assertRaisesRegex(RuntimeError, 'timed out'):
            self.verify(commission_gpu_fans, reports, duration=2)
