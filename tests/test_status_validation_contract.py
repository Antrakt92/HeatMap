"""Validator contract lock-in: exact verdicts for crafted status reports.

Covers FanWorkerClient._poll_status and GpuWorkerClient.poll with identical
inputs where semantics overlap, pinning every intentional difference
(command bounds, channel checks, standby, error wording/order). Any validator
refactor must keep this matrix green without touching it.
"""
import io
import json
import os
import unittest
from unittest.mock import Mock, patch

import case_fans
import gpu_fans


def client(module, client_type, exited=False):
    worker = client_type('unused')
    worker.started = 100
    worker.process = Mock(pid=7)
    worker.process.poll.return_value = 1 if exited else None
    worker.process.stdin = Mock()
    return worker


def base(module):
    report = dict(profile=module.PROFILE, pid=7, time=199, state='active',
                  command_pct=100 if module is case_fans else 40,
                  restore_confirmed=True, restore_errors=[], control_attempted=True)
    if module is case_fans:
        report.update(controlled_channels=['System Fan #1', 'System Fan #2'],
                      firmware_channels=['System Fan #4'])
    return report


def poll(module, client_type, report, now=200):
    worker = client(module, client_type)
    payload = io.StringIO(json.dumps(report))
    with (patch.object(module.time, 'time', return_value=now),
          patch.object(module, 'open_status_file', return_value=payload)):
        return worker.poll()


# (mutation, expected_case_reason, expected_gpu_reason); None means valid.
MATRIX = [
    ('valid', {}, None, None),
    ('bad profile', {'profile': 'other'}, 'Invalid case fan controller profile', 'Invalid GPU fan status'),
    ('bad state', {'state': 'flying'}, 'Invalid case fan controller status', 'Invalid GPU fan status fields'),
    ('reason non-str', {'reason': 5}, 'Invalid case fan controller reason', 'Invalid GPU fan status reason'),
    ('bad stop cause', {'stop_cause': 'x'}, 'Invalid case fan controller stop cause', 'Invalid GPU fan stop cause'),
    ('bad restoration', {'restore_confirmed': 'yes'},
     'Invalid case fan restoration report', 'Invalid GPU restoration report'),
    ('bad attempted type', {'control_attempted': 'false'},
     'Invalid case fan restoration report', None),
    ('low command', {'command_pct': 50},
     'Invalid case fan controller command report', None),
    ('tiny command', {'command_pct': 20},
     'Invalid case fan controller command report', 'Invalid GPU fan command report'),
    ('bad channels', {'controlled_channels': ['Nope'], 'firmware_channels': []},
     'Invalid case fan controller channel report', None),
    ('empty channels', {'controlled_channels': [], 'firmware_channels': []},
     'Missing case fan controller channels', None),
    ('standby', {'state': 'standby'},
     'Invalid case fan controller status', 'Invalid GPU fan standby report'),
    ('standby unready', {'state': 'standby', 'thermal_ready': 'eventually'},
     'Invalid case fan controller status', 'Invalid GPU fan standby report'),
    ('stale', {'time': 180},
     'Case fan controller status is stale', 'GPU fan status is stale; restoration unconfirmed'),
    ('future stamp', {'time': 203},
     'Case fan controller status is stale', 'GPU fan status belongs to another launch'),
    ('unknown pid active', {'pid': 99999},
     'Case fan controller status is stale', 'Cannot confirm GPU fan owner'),
    ('foreign pid active', {'pid': os.getpid()},
     'Case fan controller status is stale', 'Unexpected GPU fan owner'),
]


class ValidatorContractTests(unittest.TestCase):
    def test_exited_nonterminal_report_is_an_error(self):
        for module, client_type in ((case_fans, case_fans.FanWorkerClient),
                                    (gpu_fans, gpu_fans.GpuWorkerClient)):
            with self.subTest(module=module.__name__):
                worker = client(module, client_type, exited=True)
                report = dict(base(module), state='checking')
                payload = io.StringIO(json.dumps(report))
                with (patch.object(module.time, 'time', return_value=200),
                      patch.object(module, 'open_status_file', return_value=payload)):
                    status = worker.poll()
                self.assertEqual(status.get('state'), 'error')
                self.assertIn('exited', status.get('reason', ''))

    def test_matrix(self):
        for module, client_type in ((case_fans, case_fans.FanWorkerClient),
                                    (gpu_fans, gpu_fans.GpuWorkerClient)):
            for name, mutation, case_reason, gpu_reason in MATRIX:
                if name == 'valid':
                    continue
                with self.subTest(module=module.__name__, case=name):
                    report = dict(base(module), **mutation)
                    status = poll(module, client_type, report)
                    expected = case_reason if module is case_fans else gpu_reason
                    if expected is None:
                        self.assertNotEqual(status.get('state'), 'error', name)
                    else:
                        self.assertEqual(status.get('state'), 'error', name)
                        self.assertIn(expected, status.get('reason', ''), name)

    def test_valid_reports_pass(self):
        for module, client_type in ((case_fans, case_fans.FanWorkerClient),
                                    (gpu_fans, gpu_fans.GpuWorkerClient)):
            with self.subTest(module=module.__name__):
                status = poll(module, client_type, base(module))
                self.assertEqual(status.get('state'), 'active')

    def test_backward_clock_jump_rejects_old_report(self):
        for module, client_type, expected in (
                (case_fans, case_fans.FanWorkerClient, 'Case fan controller status is stale'),
                (gpu_fans, gpu_fans.GpuWorkerClient, 'GPU fan status belongs to another launch')):
            with self.subTest(module=module.__name__):
                worker = client(module, client_type)
                report = dict(base(module), time=199)
                payload = io.StringIO(json.dumps(report))
                with (patch.object(module.time, 'time', return_value=100),
                      patch.object(module, 'open_status_file', return_value=payload)):
                    status = worker.poll()
                self.assertEqual(status.get('state'), 'error')
                self.assertIn(expected, status.get('reason', ''))


if __name__ == '__main__':
    unittest.main()
