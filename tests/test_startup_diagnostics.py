import queue
import threading
import unittest
from unittest import mock

import overlay


class StartupDiagnosticsTests(unittest.TestCase):
    def test_reconciliation_returns_verified_enabled_state_from_one_query(self):
        identity = ("user", ("user",), None)
        with (
            mock.patch.object(overlay, "_resolve_autostart_identity", return_value=identity),
            mock.patch.object(overlay, "_query_autostart_task_definition", return_value=(object(), None)) as query,
            mock.patch.object(overlay, "_classify_autostart_task", return_value=overlay.AUTOSTART_SAFE_CURRENT),
            mock.patch.object(overlay, "enable_autostart") as enable,
        ):
            result = overlay.reconcile_autostart_security()
        self.assertTrue(result.ok)
        self.assertTrue(result.enabled)
        self.assertFalse(result.changed)
        query.assert_called_once_with()
        enable.assert_not_called()

    def test_absence_and_failed_query_do_not_share_enabled_state(self):
        for error, expected in ((None, False), ("RPC unavailable", None)):
            with (
                self.subTest(error=error),
                mock.patch.object(overlay, "_resolve_autostart_identity", return_value=("user", ("user",), None)),
                mock.patch.object(overlay, "_query_autostart_task_definition", return_value=(None, error)),
                mock.patch.object(overlay, "enable_autostart") as enable,
            ):
                result = overlay.reconcile_autostart_security()
            self.assertIs(result.enabled, expected)
            self.assertEqual(result.ok, error is None)
            enable.assert_not_called()

    def test_migration_uses_validated_enable_result(self):
        for success in (True, False):
            with (
                self.subTest(success=success),
                mock.patch.object(overlay, "_resolve_autostart_identity", return_value=("user", ("user",), None)),
                mock.patch.object(overlay, "_query_autostart_task_definition", return_value=(object(), None)),
                mock.patch.object(overlay, "_classify_autostart_task", return_value=overlay.AUTOSTART_LEGACY_UNSAFE),
                mock.patch.object(overlay, "enable_autostart", return_value=(success, "result")) as enable,
            ):
                result = overlay.reconcile_autostart_security()
            self.assertTrue(result.changed)
            self.assertEqual(result.ok, success)
            self.assertIs(result.enabled, True if success else None)
            enable.assert_called_once_with()

    def test_main_passes_verified_state_and_releases_instance_on_startup_exception(self):
        result = mock.Mock(ok=True)
        with (
            mock.patch.object(overlay, "_runtime_dll_errors", return_value=[]),
            mock.patch.object(overlay, "acquire_single_instance", return_value=True),
            mock.patch.object(overlay, "release_single_instance") as release,
            mock.patch.object(overlay, "_is_admin", return_value=True),
            mock.patch.object(overlay, "reconcile_autostart_security", return_value=result),
            mock.patch.object(overlay, "OverlayApp", side_effect=RuntimeError("window failed")) as factory,
        ):
            with self.assertRaisesRegex(RuntimeError, "window failed"):
                overlay.main()
        factory.assert_called_once_with(autostart_result=result)
        release.assert_called_once_with()

    def test_main_releases_instance_when_reconciliation_raises(self):
        with (
            mock.patch.object(overlay, "_runtime_dll_errors", return_value=[]),
            mock.patch.object(overlay, "acquire_single_instance", return_value=True),
            mock.patch.object(overlay, "release_single_instance") as release,
            mock.patch.object(overlay, "_is_admin", return_value=True),
            mock.patch.object(overlay, "reconcile_autostart_security", side_effect=RuntimeError("query failed")),
        ):
            with self.assertRaisesRegex(RuntimeError, "query failed"):
                overlay.main()
        release.assert_called_once_with()

    def diagnostics_app(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.running = True
        app.root = mock.Mock()
        app._stop_event = threading.Event()
        app._set_menu_label = mock.Mock()
        return app

    def test_diagnostics_return_to_ui_before_slow_hardware_open(self):
        app = self.diagnostics_app()
        opened = threading.Event()
        release = threading.Event()
        computer = mock.Mock()

        def initialize():
            opened.set()
            release.wait(3)
            return computer

        with (
            mock.patch.object(overlay, "init_hardware_monitor", side_effect=initialize),
            mock.patch.object(overlay, "read_sensors", return_value={}),
            mock.patch.object(overlay, "build_sensor_diagnostics", return_value="synthetic diagnostics"),
        ):
            app.copy_diagnostics()
            try:
                self.assertTrue(opened.wait(1))
                self.assertFalse(release.is_set())
                app.root.clipboard_clear.assert_not_called()
                app.copy_diagnostics()
                self.assertEqual(app.root.after.call_count, 1)
            finally:
                release.set()
                if hasattr(app, "_diagnostics_thread"):
                    app._diagnostics_thread.join(3)
            app._poll_diagnostics()

        computer.Close.assert_called_once_with()
        app.root.clipboard_append.assert_called_once_with('synthetic diagnostics\nCase fan controller:\n{"state": "off"}')
        self.assertFalse(app._diagnostics_running)

    def test_diagnostics_cancelled_during_open_close_without_sampling(self):
        app = self.diagnostics_app()
        computer = mock.Mock()

        def initialize():
            app.running = False
            app._stop_event.set()
            return computer

        with (
            mock.patch.object(overlay, "init_hardware_monitor", side_effect=initialize),
            mock.patch.object(overlay, "read_sensors") as sample,
        ):
            app.copy_diagnostics()
            if hasattr(app, "_diagnostics_thread"):
                app._diagnostics_thread.join(3)
            app._poll_diagnostics()
        computer.Close.assert_called_once_with()
        sample.assert_not_called()
        app.root.clipboard_clear.assert_not_called()

    def test_diagnostics_error_resets_menu_and_never_clears_clipboard(self):
        app = self.diagnostics_app()
        app._diagnostics_running = True
        app._diagnostics_results = queue.Queue()
        app._diagnostics_results.put((False, "synthetic failure"))
        with mock.patch.object(overlay, "_show_error_message") as show:
            app._poll_diagnostics()
        self.assertFalse(app._diagnostics_running)
        app._set_menu_label.assert_called_with("diagnostics", "Copy diagnostics")
        app.root.clipboard_clear.assert_not_called()
        show.assert_called_once()

    def shutdown_app(self):
        app = self.diagnostics_app()
        app.root.tk.call.return_value = ()
        app._cancel_scheduled_embed = mock.Mock()
        app._saved_pos = (50, 50)
        app.config = {}
        app.peek_enabled = app.alerts_enabled = app.details_enabled = False
        app._GPU_FAN_MAX_RPM = app._CPU_FAN_MAX_RPM = 1000
        app._save_config = mock.Mock()
        app.sensor_thread = mock.Mock()
        app.sensor_thread.is_alive.return_value = False
        return app

    def test_quit_waits_for_diagnostics_worker_to_close_its_monitor(self):
        app = self.shutdown_app()
        opened = threading.Event()
        allow_cleanup = threading.Event()
        closed = threading.Event()
        computer = mock.Mock()
        close_threads = []

        def initialize():
            opened.set()
            allow_cleanup.wait(3)
            return computer

        def close_monitor():
            close_threads.append(threading.get_ident())
            closed.set()

        computer.Close.side_effect = close_monitor
        app.root.destroy.side_effect = lambda: self.assertTrue(closed.is_set())
        with (
            mock.patch.object(overlay, "init_hardware_monitor", side_effect=initialize),
            mock.patch.object(overlay, "read_sensors") as sample,
            mock.patch.object(overlay, "release_single_instance") as release,
        ):
            app.copy_diagnostics()
            worker = app._diagnostics_thread
            join_worker = worker.join

            def finish_worker(timeout):
                allow_cleanup.set()
                join_worker(timeout=timeout)

            try:
                self.assertTrue(opened.wait(1))
                with mock.patch.object(worker, "join", side_effect=finish_worker) as join:
                    app.quit()
                join.assert_called_once()
                self.assertFalse(worker.is_alive())
                self.assertEqual(close_threads, [worker.ident])
                app.root.destroy.assert_called_once_with()
                release.assert_called_once_with()
                sample.assert_not_called()
                app.root.clipboard_clear.assert_not_called()
            finally:
                allow_cleanup.set()
                self.assertTrue(closed.wait(3))

    def test_quit_shares_one_timeout_between_workers(self):
        for sensor_elapsed in (4.0, 6.0):
            with self.subTest(sensor_elapsed=sensor_elapsed):
                app = self.shutdown_app()
                now = [100.0]
                app.sensor_thread = mock.Mock()
                app._diagnostics_thread = mock.Mock()
                app.sensor_thread.is_alive.return_value = True
                app._diagnostics_thread.is_alive.return_value = True

                def finish_sensor(timeout):
                    now[0] += sensor_elapsed

                app.sensor_thread.join.side_effect = finish_sensor
                with (
                    mock.patch.object(overlay.time, "monotonic", side_effect=lambda: now[0]),
                    mock.patch.object(overlay, "release_single_instance") as release,
                    mock.patch.object(overlay, "log"),
                ):
                    app.quit()
                app.sensor_thread.join.assert_called_once_with(timeout=5.0)
                app._diagnostics_thread.join.assert_called_once_with(
                    timeout=max(0.0, 5.0 - sensor_elapsed)
                )
                app.root.destroy.assert_called_once_with()
                release.assert_called_once_with()

    def test_quit_accepts_missing_and_unstarted_workers(self):
        for state in ("missing", "none", "unstarted"):
            with self.subTest(state=state):
                app = self.shutdown_app()
                if state == "missing":
                    del app.sensor_thread
                elif state == "unstarted":
                    app.sensor_thread = threading.Thread(target=lambda: None)
                    app._diagnostics_thread = threading.Thread(target=lambda: None)
                else:
                    app.sensor_thread = None
                    app._diagnostics_thread = None
                with mock.patch.object(overlay, "release_single_instance") as release:
                    app.quit()
                app.root.destroy.assert_called_once_with()
                release.assert_called_once_with()

    def test_diagnostics_thread_start_failure_returns_to_idle(self):
        app = self.diagnostics_app()
        with (
            mock.patch.object(overlay.threading.Thread, "start", side_effect=RuntimeError("cannot start worker")),
            mock.patch.object(overlay, "init_hardware_monitor") as initialize,
            mock.patch.object(overlay, "_show_error_message") as show,
        ):
            app.copy_diagnostics()
            app._poll_diagnostics()
        self.assertFalse(app._diagnostics_running)
        self.assertFalse(app._diagnostics_thread.is_alive())
        app._set_menu_label.assert_called_with("diagnostics", "Copy diagnostics")
        initialize.assert_not_called()
        app.root.clipboard_clear.assert_not_called()
        show.assert_called_once_with(
            "HeatMap Diagnostics", "Failed to copy diagnostics:\ncannot start worker"
        )


if __name__ == "__main__":
    unittest.main()
