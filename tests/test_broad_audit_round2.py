"""Fault injection for disposable probes and installer verification."""
import json
from types import SimpleNamespace
import unittest
from unittest import mock

import setup
import storage_probe
import volume_probe


class ProbeSetupCleanupTests(unittest.TestCase):
    def test_receive_close_failure_cannot_skip_child_disposal(self):
        context = mock.Mock()
        receive, send, process = mock.Mock(), mock.Mock(), mock.Mock()
        context.Pipe.return_value = receive, send
        context.Process.return_value = process
        receive.close.side_effect = OSError('receive handle failed to close')
        process.is_alive.side_effect = [True, False]
        with (mock.patch.object(storage_probe.multiprocessing, 'get_context', return_value=context),
              self.assertLogs(storage_probe.log, level='WARNING') as logged):
            result = storage_probe.probe_disk(2, timeout=0)
        self.assertIn('timed out', result['error'])
        send.close.assert_called()
        process.terminate.assert_called_once()
        self.assertEqual(process.join.call_count, 2)
        process.close.assert_called_once()
        self.assertIn('receive pipe', ' '.join(logged.output))

    def test_send_close_failure_cannot_skip_child_disposal(self):
        context = mock.Mock()
        receive, send, process = mock.Mock(), mock.Mock(), mock.Mock()
        context.Pipe.return_value = receive, send
        context.Process.return_value = process
        send.close.side_effect = OSError('send handle failed to close')
        process.is_alive.side_effect = [True, False]
        with (mock.patch.object(storage_probe.multiprocessing, 'get_context', return_value=context),
              self.assertLogs(storage_probe.log, level='WARNING') as logged):
            result = storage_probe.probe_disk(2, timeout=0)
        self.assertIn('send handle failed to close', result['error'])
        receive.close.assert_called_once()
        process.terminate.assert_called_once()
        self.assertEqual(process.join.call_count, 2)
        process.close.assert_called_once()
        self.assertIn('send pipe', ' '.join(logged.output))

    def test_constructed_process_is_closed_after_start_failure(self):
        context = mock.Mock()
        receive, send, process = mock.Mock(), mock.Mock(), mock.Mock()
        context.Pipe.return_value = receive, send
        context.Process.return_value = process
        process.start.side_effect = OSError('native process failed to start')
        with mock.patch.object(storage_probe.multiprocessing, 'get_context', return_value=context):
            result = storage_probe.probe_disk(2)
        self.assertIn('native process failed to start', result['error'])
        receive.close.assert_called_once()
        send.close.assert_called_once()
        process.close.assert_called_once()
        process.terminate.assert_not_called()

    def test_process_construction_failure_closes_both_pipe_ends(self):
        context = mock.Mock()
        receive, send = mock.Mock(), mock.Mock()
        context.Pipe.return_value = receive, send
        context.Process.side_effect = OSError('process resources unavailable')
        with mock.patch.object(storage_probe.multiprocessing, 'get_context', return_value=context):
            result = storage_probe.probe_disk(2)
        self.assertEqual(result['disk_number'], 2)
        self.assertIn('process resources unavailable', result['error'])
        receive.close.assert_called_once()
        send.close.assert_called_once()

    def test_pipe_creation_failure_returns_a_volume_error(self):
        context = mock.Mock()
        context.Pipe.side_effect = OSError('pipe resources unavailable')
        with mock.patch.object(storage_probe.multiprocessing, 'get_context', return_value=context):
            result = volume_probe.probe_volume('C:\\')
        self.assertIn('pipe resources unavailable', result['error'])
        context.Process.assert_not_called()


class AuthenticodeReplyTests(unittest.TestCase):
    def test_non_object_signature_reply_is_a_verification_failure(self):
        expected = {'status': 'Valid', 'subject': 'Expected signer', 'thumbprint': 'A' * 40}
        for reply in (None, [], 'unexpected text', 0):
            with self.subTest(reply=reply):
                run = mock.Mock(return_value=SimpleNamespace(
                    returncode=0, stdout=json.dumps(reply), stderr=''))
                messages = setup._verify_authenticode('unused-installer.exe', expected, run)
                self.assertTrue(messages)
                self.assertIn('invalid', ' '.join(messages).lower())


if __name__ == '__main__':
    unittest.main()
