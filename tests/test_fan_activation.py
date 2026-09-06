import unittest
from unittest import mock

import enable_case_fans as activation

FULL_RPM = {"System Fan #1": 1200, "System Fan #2": 1200}


class ActivationTests(unittest.TestCase):
    def test_enable_requires_actual_samples_then_verified_restore(self):
        client = mock.Mock()
        client.poll.side_effect = [dict(state="active", time=100, verified_full_rpm=FULL_RPM, fans=[dict(rpm=1200)]), dict(state="stopped", restore_errors=[], restore_confirmed=True)]
        samples = []
        activation.verify_worker(client, samples, duration=0)
        self.assertEqual(len(samples), 1)
        client.stop.assert_called_once()
        client.process.wait.assert_called_once_with(timeout=20)

    def test_failure_still_stops_and_checks_restoration(self):
        client = mock.Mock()
        client.poll.side_effect = [dict(state="error", reason="tachometer"), dict(state="error", restore_errors=[], restore_confirmed=True)]
        with self.assertRaisesRegex(RuntimeError, "tachometer"):
            activation.verify_worker(client, [])
        client.stop.assert_called_once()
        self.assertEqual(client.poll.call_count, 2)

    def test_unverified_restore_blocks_activation(self):
        client = mock.Mock()
        client.poll.side_effect = [dict(state="active", time=100), dict(state="error", restore_errors=["bus timeout"])]
        with self.assertRaisesRegex(RuntimeError, "restore not confirmed"):
            activation.verify_worker(client, [], duration=0)

    def test_startup_refusal_preserves_cause_only_with_explicit_no_command_evidence(self):
        for attempted in (False, True, None):
            with self.subTest(attempted=attempted):
                client = mock.Mock()
                status = dict(state="error", reason="Case fan channel not ready: SYS1",
                              control_attempted=attempted, baseline=[], controlled_channels=[],
                              restore_confirmed=False, restore_errors=[])
                client.poll.side_effect = [status, status]
                expected = "Case fan channel not ready: SYS1" if attempted is False else "restore not confirmed"
                with self.assertRaisesRegex(RuntimeError, expected):
                    activation.verify_worker(client, [])
                client.stop.assert_called_once()

    def test_close_only_targets_exact_overlay_script(self):
        unrelated = mock.Mock(pid=20, info=dict(name="pythonw.exe", cmdline=["pythonw.exe", "other-overlay.py"]))
        with mock.patch.object(activation.psutil, "process_iter", return_value=[unrelated]), \
             mock.patch.object(activation.ctypes, "WinDLL") as native:
            activation.close_previous_overlay()
        native.assert_not_called()

    def verify_reports(self, reports, samples):
        clock = [0.0]
        client = mock.Mock()
        stopped = [False]
        snapshots = iter(reports)
        current = [None]

        def poll():
            if stopped[0]:
                return dict(state='stopped', restore_confirmed=True, restore_errors=[])
            current[0] = next(snapshots, current[0])
            return dict(current[0], verified_full_rpm=FULL_RPM)

        client.poll.side_effect = poll
        client.stop.side_effect = lambda: stopped.__setitem__(0, True)
        with (mock.patch.object(activation.time, 'monotonic', side_effect=lambda: clock[0]),
              mock.patch.object(activation.time, 'sleep', side_effect=lambda delay: clock.__setitem__(0, clock[0] + delay))):
            return activation.verify_worker(client, samples, duration=6)

    def test_cached_active_report_cannot_prove_sustained_operation(self):
        samples = []
        with self.assertRaisesRegex(RuntimeError, 'timed out'):
            self.verify_reports([dict(state='active', time=100)], samples)
        self.assertEqual(len(samples), 1)

    def test_advancing_reports_succeed_and_cached_duplicates_are_not_samples(self):
        samples = []
        result = self.verify_reports([dict(state='active', time=stamp) for stamp in (100, 100, 102, 104, 106)], samples)
        self.assertTrue(result['restore_confirmed'])
        self.assertEqual([sample['time'] for sample in samples], [100, 102, 104, 106])

    def test_invalid_or_backward_timestamp_cannot_prove_operation(self):
        for stamp in (None, True, float('nan'), float('inf'), 99):
            with self.subTest(stamp=stamp), self.assertRaisesRegex(RuntimeError, 'timestamp'):
                self.verify_reports([dict(state='active', time=100), dict(state='active', time=stamp)], [])


if __name__ == "__main__":
    unittest.main()
