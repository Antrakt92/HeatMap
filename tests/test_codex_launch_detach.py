import unittest
from unittest import mock

import psutil

import codex_detach


class CodexDetachTests(unittest.TestCase):
    def test_codex_in_ancestor_chain_is_detected(self):
        codex = mock.Mock()
        codex.name.return_value = "codex.exe"
        codex.parent.return_value = None
        intermediate = mock.Mock()
        intermediate.name.return_value = "python.exe"
        intermediate.parent.return_value = codex
        child = mock.Mock()
        child.parent.return_value = intermediate
        self.assertTrue(codex_detach.launched_from_codex(child))

    def test_unrelated_parent_and_process_disappearance_are_not_misclassified(self):
        explorer = mock.Mock()
        explorer.name.return_value = "explorer.exe"
        explorer.parent.return_value = None
        child = mock.Mock()
        child.parent.return_value = explorer
        self.assertFalse(codex_detach.launched_from_codex(child))
        child.parent.side_effect = psutil.NoSuchProcess(100)
        self.assertFalse(codex_detach.launched_from_codex(child))

    def test_codex_launch_uses_verified_task_and_does_not_change_preference(self):
        result = mock.Mock(returncode=0)
        with (
            mock.patch.object(codex_detach, "launched_from_codex", return_value=True),
            mock.patch("overlay.is_autostart_enabled", return_value=True),
            mock.patch("overlay._run_task_powershell", return_value=(result, None)) as run,
        ):
            self.assertEqual(codex_detach.relay_to_scheduled_task(), 1)
        self.assertIn("Start-ScheduledTask", run.call_args.args[0])

    def test_missing_or_failed_task_never_falls_back_to_codex_child_launch(self):
        with (
            mock.patch.object(codex_detach, "launched_from_codex", return_value=True),
            mock.patch("overlay.is_autostart_enabled", return_value=False),
        ):
            self.assertEqual(codex_detach.relay_to_scheduled_task(), 2)
        with (
            mock.patch.object(codex_detach, "launched_from_codex", return_value=True),
            mock.patch("overlay.is_autostart_enabled", return_value=True),
            mock.patch("overlay._run_task_powershell", return_value=(None, "RPC unavailable")),
        ):
            self.assertEqual(codex_detach.relay_to_scheduled_task(), 2)

    def test_unexpected_crash_is_neither_relay_nor_launch(self):
        with mock.patch.object(codex_detach, "relay_to_scheduled_task",
                               side_effect=RuntimeError("synthetic ancestry failure")):
            self.assertEqual(codex_detach.main(), 3)


if __name__ == "__main__":
    unittest.main()
