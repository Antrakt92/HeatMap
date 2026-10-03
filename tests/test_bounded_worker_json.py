"""Corrupt worker files must not supply partial evidence or break UI polling."""
import io
import json
import unittest
from unittest import mock

import fan_common
import case_fans
import gpu_fans
import shared_fans
from test_status_validation_contract import client, base


class BoundedWorkerJsonTests(unittest.TestCase):
    def test_exact_size_document_still_parses(self):
        payload = '{}' + ' ' * 65534
        self.assertEqual(fan_common.load_json_payload('unused', lambda path: io.StringIO(payload)), {})

    def test_corrupt_status_can_only_reuse_a_verified_report_until_it_expires(self):
        for module, kind in ((case_fans, case_fans.FanWorkerClient), (gpu_fans, gpu_fans.GpuWorkerClient)):
            for now, expected in ((200, 'active'), (210, 'error')):
                with self.subTest(module=module.__name__, now=now):
                    worker = client(module, kind)
                    worker.last_status = base(module)
                    payload = '{}' + ' ' * 65534 + 'tail'
                    with (mock.patch.object(module, 'open_status_file', return_value=io.StringIO(payload)),
                          mock.patch.object(module.time, 'time', return_value=now)):
                        self.assertEqual(worker.poll()['state'], expected)

    def test_corrupt_recovery_file_prevents_all_hardware_calls_and_keeps_journal(self):
        import tempfile
        from pathlib import Path
        for nesting in (False, True):
            with self.subTest(nesting=nesting), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'recovery.json'
                payload = ('[' * 20000 + '0' + ']' * 20000 if nesting else '{}' + ' ' * 65534 + 'tail')
                path.write_text(payload, encoding='utf-8')
                adapter, backend, bridge, bus = mock.Mock(), mock.Mock(), mock.Mock(), mock.Mock()
                gpu = gpu_fans.RecoveryJournal(path, {})
                shared = shared_fans.SharedRecoveryJournal(path)
                for recover in (lambda: gpu.recover(adapter), lambda: shared.recover(backend, bridge, bus)):
                    with self.assertRaisesRegex(RuntimeError, 'unreadable; settings preserved'):
                        recover()
                self.assertEqual(adapter.mock_calls, [])
                self.assertEqual(backend.mock_calls, [])
                self.assertEqual(bridge.mock_calls, [])
                bus.assert_not_called()
                self.assertEqual(path.read_text(encoding='utf-8'), payload)

    def test_journal_rejects_valid_prefix_with_data_beyond_limit(self):
        payload = '{}' + ' ' * 65534 + 'corrupt tail'
        with self.assertRaises(ValueError):
            fan_common.load_json_payload('unused', lambda path: io.StringIO(payload))

    def test_deeply_nested_journal_is_a_parse_error(self):
        payload = '[' * 20000 + '0' + ']' * 20000
        with self.assertRaises(ValueError):
            fan_common.load_json_payload('unused', lambda path: io.StringIO(payload))

    def test_corrupt_status_never_certifies_restoration_and_poll_does_not_raise(self):
        for module, kind in ((case_fans, case_fans.FanWorkerClient), (gpu_fans, gpu_fans.GpuWorkerClient)):
            for corruption in ('oversize', 'nesting'):
                with self.subTest(module=module.__name__, corruption=corruption):
                    worker = client(module, kind, exited=True)
                    report = dict(base(module), state='stopped', restore_confirmed=True, restore_errors=[])
                    prefix = json.dumps(report)
                    payload = (prefix + ' ' * (65536 - len(prefix)) + 'corrupt tail'
                               if corruption == 'oversize' else '[' * 20000 + '0' + ']' * 20000)
                    with (mock.patch.object(module, 'open_status_file', return_value=io.StringIO(payload)),
                          mock.patch.object(module.time, 'time', return_value=200)):
                        status = worker.poll()
                    self.assertEqual(status['state'], 'error')
                    self.assertIsNot(status.get('restore_confirmed'), True)


if __name__ == '__main__':
    unittest.main()
