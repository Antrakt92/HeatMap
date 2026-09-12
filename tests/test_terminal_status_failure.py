"""Failed terminal publication must not discard verified rollback evidence."""
import errno
import json
import os
import tempfile
import unittest
from contextlib import ExitStack, nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

import case_fans
import gpu_fans
import overlay
from test_case_fans import fixture
from test_gpu_fan_startup_audit import Adapter


class TerminalStatusFailureTests(unittest.TestCase):
    def test_compact_snapshot_is_smaller_and_both_clients_accept_real_evidence(self):
        for module, client_type in ((case_fans, case_fans.FanWorkerClient),
                                    (gpu_fans, gpu_fans.GpuWorkerClient)):
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory() as directory:
                path = str(Path(directory) / 'status.json')
                details = dict(profile=module.PROFILE, reason='Original settings restored',
                               restore_confirmed=True, restore_errors=[], control_attempted=True,
                               baseline=[{'large diagnostic': 'x' * 8192}],
                               discovery={'large diagnostic': 'x' * 8192})
                if module is case_fans:
                    details.update(controlled_channels=list(case_fans.INDEPENDENT_TARGETS),
                                   firmware_channels=list(case_fans.TARGETS[2:]))
                else:
                    details.update(recovery_pending=False, settings_conflict=None)
                sizes = []

                def publish(filename, state, **fields):
                    sizes.append(len(json.dumps(fields)))
                    if len(sizes) == 1:
                        raise OSError(errno.ENOSPC, 'No space left on device')
                    case_fans.write_status(filename, state, **fields)

                with patch.object(case_fans.time, 'time', return_value=100):
                    error = case_fans.write_terminal_status(path, 'stopped', publisher=publish, **details)
                    client = client_type(directory)
                    client.started = 99
                    client.status_path = path
                    client.process = Mock(pid=os.getpid())
                    client.process.poll.return_value = 1
                    status = client.poll()
                self.assertIn('No space left', error)
                self.assertEqual(len(sizes), 2)
                self.assertLess(sizes[1], sizes[0] / 10)
                self.assertEqual(status['state'], 'error')
                self.assertTrue(status['restore_confirmed'])
                self.assertIn(details['reason'], status['reason'])
                self.assertNotIn('stop_cause', status)

    def run_controller(self, kind, failure, restore_fails=False):
        module = case_fans if kind == 'case' else gpu_fans
        clock = [100.0]
        owner = Mock()
        owner.create_time.return_value = 1
        owner.is_running.return_value = True
        stop = Mock()
        stop.is_set.side_effect = lambda: clock[0] >= (150 if failure == 'heartbeat' else 108)
        stop.wait.side_effect = lambda seconds: clock.__setitem__(0, clock[0] + seconds)
        reports, attempts = [], []
        reads = [0]

        def read(*_args, **_kwargs):
            reads[0] += 1
            if failure == 'heartbeat' and reads[0] == (3 if kind == 'case' else 5):
                clock[0] += 16
            return dict(cpu_temp=40, gpu_core_temp=72, gpu_hotspot_temp=86,
                        gpu_memory_temp=86, gpu_fan=3000, timestamp_ms=clock[0] * 1000)

        def publish(_path, state, **details):
            report = dict(state=state, **details)
            attempts.append(report)
            if (failure == 'persistent'
                    or failure == 'active_and_terminal' and 'command_pct' in details
                    or 'restore_confirmed' in details and 'status_publication_error' not in details):
                raise OSError(errno.ENOSPC, 'No space left on device')
            reports.append(report)

        with ExitStack() as stack:
            directory = stack.enter_context(tempfile.TemporaryDirectory())
            path = str(Path(directory) / 'status.json')
            stack.enter_context(patch.object(module.threading, 'Event', return_value=stop))
            stack.enter_context(patch.object(module.threading, 'Thread'))
            stack.enter_context(patch.object(module.psutil, 'Process', return_value=owner))
            stack.enter_context(patch.object(module.time, 'monotonic', side_effect=lambda: clock[0]))
            stack.enter_context(patch.object(module.time, 'sleep'))
            stack.enter_context(patch.object(module, 'require_hardware_access'))
            stack.enter_context(patch.object(module, 'write_status', side_effect=publish))
            stack.enter_context(patch.object(module, 'WorkerMutex', return_value=nullcontext()))
            argv = ['worker', '--status', path, '--owner-pid', '7', '--owner-created', '1']
            if kind == 'case':
                computer, controls = fixture()
                if restore_fails:
                    controls[0].SetDefault.side_effect = RuntimeError('restore readback failed')
                stack.enter_context(patch.dict('sys.modules', {'clr': Mock()}))
                stack.enter_context(patch.object(case_fans, 'make_shared_computer', return_value=computer))
                stack.enter_context(patch.object(overlay, '_is_admin', return_value=True))
                stack.enter_context(patch.object(overlay, '_runtime_dll_errors', return_value=[]))
                stack.enter_context(patch.object(overlay, 'read_sensors', side_effect=read))
            else:
                adapter = Adapter(clock)
                adapter.readings = read
                baseline = adapter.snapshot()
                if restore_fails:
                    original = adapter.set_zero_rpm

                    def zero_rpm(enabled):
                        if enabled:
                            raise RuntimeError('restore readback failed')
                        original(enabled)

                    adapter.set_zero_rpm = zero_rpm
                stack.enter_context(patch('amd_gpu_fan.AmdGpuFan', return_value=adapter))
            stack.enter_context(patch.object(module.sys, 'argv', argv))
            result = module.main()
            if kind == 'case':
                computer.Close.assert_called_once()
                if failure == 'persistent':
                    self.assertTrue(all(control.SetDefault.called == control.SetSoftware.called
                                        for control in controls))
                else:
                    self.assertTrue(controls[0].SetSoftware.called)
                    self.assertTrue(controls[0].SetDefault.called)
            else:
                adapter.close.assert_called_once()
                if not restore_fails:
                    self.assertEqual(adapter.snapshot(), baseline)
                if failure == 'persistent':
                    self.assertFalse(adapter.writes)
        return result, attempts, reports

    def test_active_and_terminal_disk_full_preserves_real_restoration_evidence(self):
        for kind in ('case', 'gpu'):
            for restore_fails in (False, True):
                with self.subTest(kind=kind, restore_fails=restore_fails):
                    result, attempts, reports = self.run_controller(kind, 'active_and_terminal', restore_fails)
                    self.assertEqual(result, 1)
                    status = reports[-1]
                    self.assertEqual(status['state'], 'error')
                    self.assertIs(status.get('restore_confirmed'), not restore_fails)
                    self.assertEqual(bool(status['restore_errors']), restore_fails)
                    self.assertTrue(status['control_attempted'])
                    self.assertIn('No space left', status['reason'])
                    self.assertIn('No space left', status['status_publication_error'])
                    self.assertNotIn('stop_cause', status)
                    self.assertEqual(len([item for item in attempts if 'restore_confirmed' in item]), 2)

    def test_terminal_disk_full_after_normal_stop_is_still_an_error(self):
        for kind in ('case', 'gpu'):
            with self.subTest(kind=kind):
                result, _, reports = self.run_controller(kind, 'terminal')
                self.assertEqual(result, 1)
                self.assertEqual(reports[-1]['state'], 'error')
                self.assertTrue(reports[-1]['restore_confirmed'])

    def test_heartbeat_with_terminal_disk_full_cannot_trigger_automatic_retry(self):
        for kind in ('case', 'gpu'):
            with self.subTest(kind=kind):
                result, _, reports = self.run_controller(kind, 'heartbeat')
                self.assertEqual(result, 1)
                self.assertIn('heartbeat expired', reports[-1]['reason'])
                self.assertTrue(reports[-1]['restore_confirmed'])
                self.assertIn('No space left', reports[-1]['status_publication_error'])
                self.assertNotIn('stop_cause', reports[-1])

    def test_permanent_failure_is_bounded_without_outer_minimal_overwrite(self):
        for kind in ('case', 'gpu'):
            with self.subTest(kind=kind):
                result, attempts, reports = self.run_controller(kind, 'persistent')
                self.assertEqual(result, 1)
                self.assertEqual(reports, [])
                terminal = [item for item in attempts if 'restore_confirmed' in item]
                self.assertEqual(len(terminal), 2)
                self.assertIn('status_publication_error', attempts[-1])


if __name__ == '__main__':
    unittest.main()
