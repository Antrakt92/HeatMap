import unittest
from unittest.mock import Mock

from startup_readiness import StartupCancelled, StartupNotReady, wait_for_readiness


class StartupReadinessTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.stop = Mock()
        self.stop.is_set.return_value = False
        self.stop.wait.side_effect = self.advance
        self.check = Mock()
        self.waiting = Mock()

    def advance(self, duration):
        self.now += duration
        return False

    def run_probe(self, probe, **options):
        return wait_for_readiness(probe, self.stop, self.check, self.waiting,
                                  clock=lambda: self.now, **options)

    def test_only_temporary_absence_retries_and_reports_progress(self):
        probe = Mock(side_effect=[StartupNotReady("temperature unavailable"),
                                  StartupNotReady("fan unavailable"), "ready"])
        self.assertEqual(self.run_probe(probe), "ready")
        self.assertEqual(probe.call_count, 3)
        self.waiting.assert_any_call("temperature unavailable", 0, 60, 1)
        self.waiting.assert_any_call("fan unavailable", 1, 59, 2)

    def test_conflict_or_native_error_is_not_retried(self):
        for error in (RuntimeError("ownership conflict"), OSError("native access failed")):
            probe = Mock(side_effect=error)
            with self.assertRaises(type(error)):
                self.run_probe(probe)
            probe.assert_called_once()
        self.stop.wait.assert_not_called()

    def test_deadline_keeps_last_reason_and_limits_attempts(self):
        probe = Mock(side_effect=StartupNotReady("SYS4 unavailable"))
        with self.assertRaisesRegex(StartupNotReady, "timed out after 3s: SYS4 unavailable"):
            self.run_probe(probe, timeout=3)
        self.assertEqual(probe.call_count, 3)
        self.assertEqual(self.now, 3)

    def test_cancellation_and_owner_loss_after_probe_prevent_takeover(self):
        def cancel():
            self.stop.is_set.return_value = True
            return "ready"
        with self.assertRaises(StartupCancelled):
            self.run_probe(cancel)
        self.stop.is_set.return_value = False
        self.check.side_effect = [None, RuntimeError("owner died")]
        with self.assertRaisesRegex(RuntimeError, "owner died"):
            self.run_probe(lambda: "ready")

    def test_timeout_or_clock_reversal_during_probe_rejects_readiness(self):
        for timestamp, error in ((61, StartupNotReady), (-1, RuntimeError)):
            self.now = 0
            def probe():
                self.now = timestamp
                return "ready"
            with self.assertRaises(error):
                self.run_probe(probe)

    def test_stop_during_wait_and_publication_failure_are_terminal(self):
        self.stop.wait.side_effect = lambda _delay: True
        with self.assertRaises(StartupCancelled):
            self.run_probe(Mock(side_effect=StartupNotReady("warming")))
        self.waiting.side_effect = OSError("status unavailable")
        with self.assertRaisesRegex(OSError, "status unavailable"):
            self.run_probe(Mock(side_effect=StartupNotReady("warming")))

    def test_invalid_budgets_do_not_probe(self):
        probe = Mock()
        for budget in (0, -1, True, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                self.run_probe(probe, timeout=budget)
        probe.assert_not_called()


if __name__ == "__main__":
    unittest.main()
