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
        app.lock = threading.Lock()
        app.sensor_data = {"cpu_temp": 58}
        return app

    def test_diagnostics_return_to_ui_before_slow_formatting_without_hardware(self):
        app = self.diagnostics_app()
        opened = threading.Event()
        release = threading.Event()
        def format_snapshot(computer, data):
            self.assertIsNone(computer)
            self.assertEqual(data, {"cpu_temp": 58})
            opened.set()
            release.wait(3)
            return "synthetic diagnostics"

        with (
            mock.patch.object(overlay, "init_hardware_monitor") as initialize,
            mock.patch.object(overlay, "read_sensors") as sample,
            mock.patch.object(overlay, "_close_hardware_monitor") as close,
            mock.patch.object(overlay, "build_sensor_diagnostics", side_effect=format_snapshot),
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

        initialize.assert_not_called()
        sample.assert_not_called()
        close.assert_not_called()
        self.assertIn("synthetic diagnostics", app.root.clipboard_append.call_args.args[0])
        self.assertIn("not yet cached", app.root.clipboard_append.call_args.args[0])
        self.assertFalse(app._diagnostics_running)

    def test_diagnostics_cancelled_during_formatting_without_hardware(self):
        app = self.diagnostics_app()
        def format_snapshot(computer, data):
            app.running = False
            app._stop_event.set()
            return "cancelled result"

        with (
            mock.patch.object(overlay, "init_hardware_monitor") as initialize,
            mock.patch.object(overlay, "read_sensors") as sample,
            mock.patch.object(overlay, "_close_hardware_monitor") as close,
            mock.patch.object(overlay, "build_sensor_diagnostics", side_effect=format_snapshot),
        ):
            app.copy_diagnostics()
            if hasattr(app, "_diagnostics_thread"):
                app._diagnostics_thread.join(3)
            app._poll_diagnostics()
        initialize.assert_not_called()
        sample.assert_not_called()
        close.assert_not_called()
        self.assertTrue(app._diagnostics_results.empty())
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

    def test_inventory_cache_refreshes_at_30_seconds_on_sensor_owner(self):
        app = self.diagnostics_app()
        computer = mock.Mock()
        owner = threading.get_ident()
        observed = []

        def format_snapshot(source, data):
            self.assertIs(source, computer)
            self.assertFalse(app.lock.locked())
            observed.append(threading.get_ident())
            return f"CPU: {data['cpu_temp']}"

        with (
            mock.patch.object(overlay.time, "monotonic", side_effect=[100, 129.9, 130]),
            mock.patch.object(overlay, "build_sensor_diagnostics", side_effect=format_snapshot) as build,
            mock.patch.object(overlay, "init_hardware_monitor") as initialize,
            mock.patch.object(overlay, "read_sensors") as sample,
            mock.patch.object(overlay, "_close_hardware_monitor") as close,
        ):
            app._cache_sensor_diagnostics(computer, {"cpu_temp": 58})
            self.assertEqual(app._sensor_diagnostics_snapshot, (100, "CPU: 58"))
            app._cache_sensor_diagnostics(computer, {"cpu_temp": 60})
            self.assertEqual(app._sensor_diagnostics_snapshot, (100, "CPU: 58"))
            app._cache_sensor_diagnostics(computer, {"cpu_temp": 62})
        self.assertEqual(app._sensor_diagnostics_snapshot, (130, "CPU: 62"))
        self.assertEqual(build.call_count, 2)
        self.assertEqual(observed, [owner, owner])
        initialize.assert_not_called()
        sample.assert_not_called()
        close.assert_not_called()

    def test_failed_cache_refresh_preserves_complete_previous_snapshot(self):
        app = self.diagnostics_app()
        app._sensor_diagnostics_snapshot = (100, "previous inventory")
        with (
            mock.patch.object(overlay.time, "monotonic", return_value=130),
            mock.patch.object(overlay, "build_sensor_diagnostics", side_effect=RuntimeError("inventory failed")),
        ):
            with self.assertRaisesRegex(RuntimeError, "inventory failed"):
                app._cache_sensor_diagnostics(mock.Mock(), {})
        self.assertEqual(app._sensor_diagnostics_snapshot, (100, "previous inventory"))

    def test_copy_uses_atomic_cached_inventory_and_reports_age_without_hardware(self):
        app = self.diagnostics_app()
        app._sensor_diagnostics_snapshot = (100, "cached inventory")
        app.health_messages = ["Sensors unavailable now"]
        with (
            mock.patch.object(overlay.time, "monotonic", return_value=145),
            mock.patch.object(overlay, "init_hardware_monitor") as initialize,
            mock.patch.object(overlay, "read_sensors") as sample,
            mock.patch.object(overlay, "_close_hardware_monitor") as close,
            mock.patch.object(overlay, "build_sensor_diagnostics") as build,
        ):
            app.copy_diagnostics()
            app._sensor_diagnostics_snapshot = (146, "new inventory")
            app._diagnostics_thread.join(3)
            app._poll_diagnostics()
        detail = app.root.clipboard_append.call_args.args[0]
        self.assertIn("cached inventory", detail)
        self.assertNotIn("new inventory", detail)
        self.assertIn("snapshot age: 45.0s", detail)
        self.assertIn("Sensors unavailable now", detail)
        for operation in (initialize, sample, close, build):
            operation.assert_not_called()

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

    def test_quit_waits_for_diagnostics_formatting_worker(self):
        app = self.shutdown_app()
        opened = threading.Event()
        allow_cleanup = threading.Event()
        closed = threading.Event()
        def format_snapshot(computer, data):
            opened.set()
            allow_cleanup.wait(3)
            closed.set()
            return "cancelled result"

        app.root.destroy.side_effect = lambda: self.assertTrue(closed.is_set())
        with (
            mock.patch.object(overlay, "init_hardware_monitor") as initialize,
            mock.patch.object(overlay, "read_sensors") as sample,
            mock.patch.object(overlay, "_close_hardware_monitor") as close,
            mock.patch.object(overlay, "build_sensor_diagnostics", side_effect=format_snapshot),
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
                app.root.destroy.assert_called_once_with()
                release.assert_called_once_with()
                sample.assert_not_called()
                initialize.assert_not_called()
                close.assert_not_called()
                self.assertTrue(app._diagnostics_results.empty())
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

    def test_quit_stops_case_controller_before_destroy_and_is_idempotent(self):
        app = self.shutdown_app()
        app.fan_worker = mock.Mock()
        app.root.destroy.side_effect = lambda: app.fan_worker.stop.assert_called_once_with()
        with mock.patch.object(overlay, "release_single_instance") as release:
            app.quit()
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
