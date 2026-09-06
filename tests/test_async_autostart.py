import queue
import threading
import unittest
from unittest import mock

import overlay


class AsyncAutostartTests(unittest.TestCase):
    def app(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.running = True
        app._stop_event = threading.Event()
        app.root = mock.Mock()
        app._set_menu_label = mock.Mock()
        return app

    def test_slow_reconciliation_does_not_block_ui_and_worker_never_calls_tk(self):
        app = self.app()
        entered, finish = threading.Event(), threading.Event()
        main_thread = threading.get_ident()
        ui_threads = []
        app.root.after.side_effect = lambda *_args: ui_threads.append(threading.get_ident())
        app._set_menu_label.side_effect = lambda *_args: ui_threads.append(threading.get_ident())
        def check():
            entered.set()
            finish.wait(3)
            return overlay.AutostartReconcileResult(False, True, "current", True)
        with mock.patch.object(overlay, "_is_admin", return_value=True), mock.patch.object(overlay, "reconcile_autostart_security", side_effect=check):
            try:
                app.start_autostart_check()
                self.assertTrue(entered.wait(1))
                self.assertTrue(app._autostart_pending)
                self.assertFalse(app._autostart_thread.daemon)
                app.root.after.assert_called()
                self.assertTrue(app._autostart_thread.is_alive())
            finally:
                finish.set()
                app._autostart_thread.join(3)
            app._poll_autostart_check()
        self.assertFalse(app._autostart_pending)
        self.assertEqual(app._autostart_warning, "")
        self.assertTrue(all(identity == main_thread for identity in ui_threads))

    def test_failure_is_nonmodal_warning_and_background_exception_is_captured(self):
        app = self.app()
        with mock.patch.object(overlay, "_is_admin", return_value=True), mock.patch.object(overlay, "reconcile_autostart_security", side_effect=RuntimeError("RPC failed")), mock.patch.object(overlay, "_show_error_message") as modal:
            app.start_autostart_check()
            app._autostart_thread.join(3)
            app._poll_autostart_check()
        self.assertIn("RPC failed", app._autostart_warning)
        self.assertFalse(app._autostart_pending)
        modal.assert_not_called()
        app._set_menu_label.assert_called_with("autostart", "Autostart: ERROR")

    def test_nonadmin_only_inspects_and_never_migrates(self):
        app = self.app()
        with mock.patch.object(overlay, "_is_admin", return_value=False), mock.patch.object(overlay, "reconcile_autostart_security") as reconcile, mock.patch.object(overlay, "is_autostart_enabled", return_value=False) as inspect:
            app.start_autostart_check()
            app._autostart_thread.join(3)
            app._poll_autostart_check()
        reconcile.assert_not_called()
        inspect.assert_called_once_with()
        app._set_menu_label.assert_called_with("autostart", "Autostart: OFF")

    def test_pending_toggle_and_repeated_start_do_not_race_migration(self):
        app = self.app()
        app._autostart_pending = True
        app._autostart_thread = mock.Mock()
        with mock.patch.object(overlay, "is_autostart_enabled") as inspect, mock.patch.object(overlay.threading, "Thread") as thread:
            app.toggle_autostart()
            app.start_autostart_check()
        inspect.assert_not_called()
        thread.assert_not_called()

    def test_poll_after_quit_does_not_touch_tk_or_reschedule(self):
        app = self.app()
        app.running = False
        app._autostart_results = queue.Queue()
        app._poll_autostart_check()
        app.root.after.assert_not_called()
        app._set_menu_label.assert_not_called()

    def test_stop_before_thread_runs_skips_task_changes(self):
        app = self.app()
        app._stop_event.set()
        with mock.patch.object(overlay, "reconcile_autostart_security") as reconcile:
            app.start_autostart_check()
            app._autostart_thread.join(3)
        reconcile.assert_not_called()

    def test_background_worker_does_not_retain_tk_owner(self):
        app = self.app()
        with mock.patch.object(overlay.threading, "Thread") as factory:
            app.start_autostart_check()
        worker = factory.call_args.kwargs["target"]
        captured = [cell.cell_contents for cell in worker.__closure__]
        self.assertTrue(all(value is not app and value is not app.root for value in captured))
        self.assertEqual(set(worker.__code__.co_freevars), {"results", "stop_event"})

    def test_successful_manual_repair_clears_previous_startup_warning(self):
        app = self.app()
        app._autostart_pending = False
        app._autostart_warning = "Autostart: previous failure"
        with mock.patch.object(overlay, "is_autostart_enabled", side_effect=[False, True]), mock.patch.object(overlay, "enable_autostart", return_value=(True, "enabled")):
            app.toggle_autostart()
        self.assertEqual(app._autostart_warning, "")


if __name__ == "__main__":
    unittest.main()
