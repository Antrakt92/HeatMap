"""Watchdog expiry during native reads is a distinct, verified terminal cause."""
import tempfile
import io
import json
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

import case_fans
import gpu_fans
import overlay
from test_case_fans import fixture
from test_gpu_fan_startup_audit import Adapter


class ControllerHeartbeatTests(unittest.TestCase):
    def test_clients_reject_unknown_malformed_or_nonterminal_stop_causes(self):
        for module, client_type in ((case_fans, case_fans.FanWorkerClient),
                                    (gpu_fans, gpu_fans.GpuWorkerClient)):
            for cause, state in ((None, 'error'), ([], 'error'), ({}, 'error'),
                                 (True, 'error'), (1, 'error'), ('requested_stop', 'error'),
                                 ('heartbeat_expired', 'stopped'), ('heartbeat_expired', 'error')):
                with self.subTest(module=module.__name__, cause=cause, state=state):
                    client = client_type('unused')
                    client.started = 100
                    client.process = Mock(pid=7)
                    client.process.poll.return_value = 1
                    report = dict(profile=module.PROFILE, pid=7, time=101, state=state,
                                  stop_cause=cause, restore_confirmed=True, restore_errors=[])
                    with (patch.object(module.time, 'time', return_value=200),
                          patch.object(module, 'open_status_file',
                                       return_value=io.StringIO(json.dumps(report)))):
                        status = client.poll()
                    if cause == 'heartbeat_expired' and state == 'error':
                        self.assertEqual(status['stop_cause'], cause)
                        self.assertTrue(status['restore_confirmed'])
                    else:
                        self.assertIn('Invalid', status['reason'])
                        self.assertNotIn('stop_cause', status)
                        self.assertIsNot(status.get('restore_confirmed'), True)

    def run_worker(self, kind, *, stage='read', cancellation=None):
        clock = [100.0]
        cancelled = [False]
        owner = Mock()
        owner.create_time.return_value = 1
        owner.is_running.return_value = True
        stop = Mock()
        stop.is_set.side_effect = lambda: cancelled[0] or clock[0] > 150
        stop.wait.side_effect = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
        reports = []
        reads = [0]

        def interrupt():
            clock[0] += 16
            if cancellation == 'stop':
                cancelled[0] = True
            elif cancellation == 'owner':
                owner.is_running.return_value = False

        def values():
            return dict(cpu_temp=40, gpu_core_temp=72, gpu_hotspot_temp=86,
                        gpu_memory_temp=86, gpu_fan=3000, timestamp_ms=clock[0] * 1000)

        def read(*_args, strict=False):
            reads[0] += 1
            # The first GPU loop repeats the last readiness timestamp. Allow
            # another fresh sample and command before interrupting the next read.
            target = 1 if stage == 'startup' else 3 if kind == 'case' else 5
            if reads[0] == target:
                interrupt()
            return values()

        module = case_fans if kind == 'case' else gpu_fans
        with ExitStack() as stack:
            directory = stack.enter_context(tempfile.TemporaryDirectory())
            stack.enter_context(patch.object(module.threading, 'Event', return_value=stop))
            stack.enter_context(patch.object(module.threading, 'Thread'))
            stack.enter_context(patch.object(module.psutil, 'Process', return_value=owner))
            stack.enter_context(patch.object(module.time, 'monotonic', side_effect=lambda: clock[0]))
            stack.enter_context(patch.object(module, 'require_hardware_access'))
            stack.enter_context(patch.object(module, 'write_status', side_effect=lambda _path, state, **details:
                                          reports.append(dict(state=state, **details))))
            if kind == 'case':
                computer, controls = fixture()
                stack.enter_context(patch.dict('sys.modules', {'clr': Mock()}))
                stack.enter_context(patch.object(case_fans, 'make_shared_computer', return_value=computer))
                stack.enter_context(patch.object(overlay, '_is_admin', return_value=True))
                stack.enter_context(patch.object(overlay, '_runtime_dll_errors', return_value=[]))
                stack.enter_context(patch.object(overlay, 'read_sensors', side_effect=read))
                result = case_fans.worker(str(Path(directory) / 'case.json'), 7, 1,
                                         full_rpm={name: 1200 for name in case_fans.INDEPENDENT_TARGETS})
                self.assertTrue(all(control.SetSoftware.called for control in controls[:2])
                                if stage != 'startup' else
                                all(not control.SetSoftware.called for control in controls))
                computer.Close.assert_called_once()
            else:
                adapter = Adapter(clock)
                baseline = adapter.snapshot()
                adapter.readings = read
                stack.enter_context(patch('amd_gpu_fan.AmdGpuFan', return_value=adapter))
                result = gpu_fans.worker(str(Path(directory) / 'gpu.json'), 7, 1)
                self.assertEqual(adapter.snapshot(), baseline)
                self.assertEqual(bool(adapter.writes), stage != 'startup')
                adapter.close.assert_called_once()
        self.assertFalse(reports[-1]['restore_errors'])
        if stage != 'startup':
            self.assertTrue(reports[-1]['restore_confirmed'])
        return result, reports[-1]

    def test_expiry_during_native_read_reports_timeout_after_verified_restoration(self):
        for kind in ('case', 'gpu'):
            with self.subTest(kind=kind):
                result, status = self.run_worker(kind)
                self.assertEqual(result, 1)
                self.assertEqual(status['state'], 'error')
                self.assertEqual(status.get('stop_cause'), 'heartbeat_expired')
                self.assertIn('heartbeat expired', status['reason'])

    def test_startup_expiry_never_writes_and_preserves_timeout_cause(self):
        for kind in ('case', 'gpu'):
            with self.subTest(kind=kind):
                result, status = self.run_worker(kind, stage='startup')
                self.assertEqual(result, 1)
                self.assertEqual(status.get('stop_cause'), 'heartbeat_expired')
                self.assertFalse(status['restore_confirmed'])

    def test_stop_and_dead_owner_take_priority_over_an_expired_heartbeat(self):
        for kind in ('case', 'gpu'):
            for cancellation in ('stop', 'owner'):
                with self.subTest(kind=kind, cancellation=cancellation):
                    result, status = self.run_worker(kind, cancellation=cancellation)
                    self.assertEqual(result, 0)
                    self.assertEqual(status['state'], 'stopped')
                    self.assertNotEqual(status.get('stop_cause'), 'heartbeat_expired')


if __name__ == '__main__':
    unittest.main()
