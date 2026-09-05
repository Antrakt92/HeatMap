import unittest
from unittest import mock

import enable_case_fans as activation


class ActivationTests(unittest.TestCase):
    def test_enable_requires_actual_samples_then_verified_restore(self):
        client = mock.Mock()
        client.poll.side_effect = [dict(state="active", fans=[dict(rpm=1200)]), dict(state="stopped", restore_errors=[])]
        samples = []
        activation.verify_worker(client, samples, duration=0)
        self.assertEqual(len(samples), 1)
        client.stop.assert_called_once()
        client.process.wait.assert_called_once_with(timeout=20)

    def test_failure_still_stops_and_checks_restoration(self):
        client = mock.Mock()
        client.poll.side_effect = [dict(state="error", reason="tachometer"), dict(state="stopped", restore_errors=[])]
        with self.assertRaisesRegex(RuntimeError, "tachometer"):
            activation.verify_worker(client, [])
        client.stop.assert_called_once()
        self.assertEqual(client.poll.call_count, 2)

    def test_unverified_restore_blocks_activation(self):
        client = mock.Mock()
        client.poll.side_effect = [dict(state="active"), dict(state="error", restore_errors=["bus timeout"])]
        with self.assertRaisesRegex(RuntimeError, "restore not confirmed"):
            activation.verify_worker(client, [], duration=0)

    def test_close_only_targets_exact_overlay_script(self):
        unrelated = mock.Mock(pid=20, info=dict(name="pythonw.exe", cmdline=["pythonw.exe", "other-overlay.py"]))
        with mock.patch.object(activation.psutil, "process_iter", return_value=[unrelated]), \
             mock.patch.object(activation.ctypes, "WinDLL") as native:
            activation.close_previous_overlay()
        native.assert_not_called()


if __name__ == "__main__":
    unittest.main()
