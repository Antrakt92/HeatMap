import threading
import unittest
from unittest import mock

import overlay


def shutdown_app():
    app = overlay.OverlayApp.__new__(overlay.OverlayApp)
    app.running = True
    app._stop_event = threading.Event()
    app.root = mock.Mock()
    app.root.tk.call.return_value = ()
    app.root.winfo_rootx.return_value = 70
    app.root.winfo_rooty.return_value = 80
    app._cancel_scheduled_embed = mock.Mock()
    app._saved_pos = None
    app.config = {"x": 50, "y": 60}
    app.peek_enabled = app.alerts_enabled = app.details_enabled = False
    app.peek_visible = app._peek_animating = app.embedded = False
    app._save_config = mock.Mock()
    app.fan_worker = mock.Mock()
    app.sensor_thread = mock.Mock()
    app.sensor_thread.is_alive.return_value = False
    return app


class ShutdownAuditTests(unittest.TestCase):
    def test_destroyed_window_coordinates_do_not_abandon_shutdown(self):
        app = shutdown_app()
        app.root.winfo_rootx.side_effect = overlay.tk.TclError("application has been destroyed")
        with mock.patch.object(overlay, "release_single_instance") as release:
            app.quit()
            app.quit()
        self.assertFalse(app.running)
        self.assertTrue(app._stop_event.is_set())
        self.assertEqual((app.config["x"], app.config["y"]), (50, 60))
        app.fan_worker.stop.assert_called_once_with()
        app.root.destroy.assert_called_once_with()
        release.assert_called_once_with()

    def test_second_coordinate_failure_does_not_partially_overwrite_saved_position(self):
        app = shutdown_app()
        app.root.winfo_rooty.side_effect = overlay.tk.TclError("window disappeared")
        with mock.patch.object(overlay, "release_single_instance"):
            app.quit()
        self.assertEqual((app.config["x"], app.config["y"]), (50, 60))
        app.root.destroy.assert_called_once_with()

    def test_already_destroyed_root_still_releases_single_instance(self):
        app = shutdown_app()
        app.root.destroy.side_effect = overlay.tk.TclError("application has been destroyed")
        with mock.patch.object(overlay, "release_single_instance") as release:
            app.quit()
            app.quit()
        app.fan_worker.stop.assert_called_once_with()
        release.assert_called_once_with()

    def test_fan_stop_failure_does_not_leave_sensor_workers_running(self):
        app = shutdown_app()
        app.fan_worker.stop.side_effect = OSError("pipe unavailable")
        with mock.patch.object(overlay, "release_single_instance") as release:
            app.quit()
            app.quit()
        self.assertFalse(app.running)
        self.assertTrue(app._stop_event.is_set())
        app.root.destroy.assert_called_once_with()
        release.assert_called_once_with()
        app.fan_worker.stop.assert_called_once_with()

    def test_shutdown_stops_new_work_before_controller_cleanup(self):
        app = shutdown_app()
        observed_states = []

        def stop_controller():
            observed_states.append((app.running, app._stop_event.is_set()))

        app.fan_worker.stop.side_effect = stop_controller
        with mock.patch.object(overlay, "release_single_instance"):
            app.quit()
        self.assertEqual(observed_states, [(False, True)])
        app.root.destroy.assert_called_once_with()

    def test_close_hides_window_before_waiting_for_hardware_cleanup(self):
        app = shutdown_app()
        sequence = []
        app._save_config.side_effect = lambda **_kwargs: sequence.append("save")
        app.root.withdraw.side_effect = lambda: sequence.append("hide")
        app.sensor_thread.is_alive.return_value = True
        app.sensor_thread.join.side_effect = lambda **_kwargs: sequence.append("join")
        app.root.destroy.side_effect = lambda: sequence.append("destroy")
        with mock.patch.object(overlay, "release_single_instance"):
            app.quit()
        self.assertEqual(sequence, ["save", "hide", "join", "destroy"])
        self.assertEqual((app.config["x"], app.config["y"]), (70, 80))

    def test_main_cleans_up_after_unexpected_event_loop_exit(self):
        for failure in (None, RuntimeError("native event loop failure"), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__):
                app = mock.Mock()
                app.run.side_effect = failure
                with (
                    mock.patch.object(overlay, "_runtime_dll_errors", return_value=[]),
                    mock.patch.object(overlay, "acquire_single_instance", return_value=True),
                    mock.patch.object(overlay, "release_single_instance") as release,
                    mock.patch.object(overlay, "_is_admin", return_value=False),
                    mock.patch.object(overlay, "OverlayApp", return_value=app),
                ):
                    if isinstance(failure, RuntimeError):
                        with self.assertRaisesRegex(RuntimeError, "native event loop failure"):
                            overlay.main()
                    else:
                        overlay.main()
                app.quit.assert_called_once_with()
                release.assert_called_once_with()

    def test_closed_cpu_reference_dialog_does_not_write_config_or_touch_menu(self):
        app = shutdown_app()
        app._set_menu_label = mock.Mock()

        def dialog(*_args, **_kwargs):
            # Modal Tk dialogs service events, including an externally requested close.
            app.running = False
            return 2000

        with mock.patch.object(overlay.simpledialog, "askinteger", side_effect=dialog):
            app.configure_cpu_reference()
        self.assertFalse(app._settings_dialog_open)
        self.assertNotIn("cpu_fan_reference_rpm", app.config)
        app._save_config.assert_not_called()
        app._set_menu_label.assert_not_called()

    def test_shutdown_during_modal_destroy_is_a_clean_dialog_cancel(self):
        app = shutdown_app()
        app._set_menu_label = mock.Mock()

        def dialog(*_args, **_kwargs):
            app.running = False
            raise overlay.tk.TclError("application has been destroyed")

        with mock.patch.object(overlay.simpledialog, "askinteger", side_effect=dialog):
            app.configure_cpu_reference()
        self.assertFalse(app._settings_dialog_open)
        app._save_config.assert_not_called()
        app._set_menu_label.assert_not_called()

    def test_pawnio_thread_start_failure_resets_menu_and_allows_retry(self):
        app = shutdown_app()
        app._set_menu_label = mock.Mock()
        with (
            mock.patch.object(overlay.threading.Thread, "start", side_effect=RuntimeError("cannot start worker")) as start,
            mock.patch.object(overlay, "prepare_verified_pawnio_installer") as download,
            mock.patch.object(overlay, "_show_error_message") as show_error,
            mock.patch.object(overlay.os, "startfile") as open_folder,
        ):
            for attempt in range(2):
                app.prepare_pawnio_repair()
                app._poll_pawnio_repair()
                self.assertFalse(app._pawnio_repair_running)
                app._set_menu_label.assert_called_with("pawnio", "Prepare verified PawnIO repair...")
                self.assertEqual(start.call_count, attempt + 1)
            download.assert_not_called()
            open_folder.assert_not_called()
            self.assertEqual(show_error.call_count, 2)
            show_error.assert_called_with("PawnIO repair", "Could not prepare PawnIO installer:\ncannot start worker")

    def test_pawnio_prepare_after_shutdown_does_not_start_work(self):
        app = shutdown_app()
        app.running = False
        app._set_menu_label = mock.Mock()
        with (
            mock.patch.object(overlay.threading, "Thread") as worker,
            mock.patch.object(overlay, "prepare_verified_pawnio_installer") as download,
        ):
            app.prepare_pawnio_repair()
        worker.assert_not_called()
        download.assert_not_called()
        app._set_menu_label.assert_not_called()
        app.root.after.assert_not_called()


if __name__ == "__main__":
    unittest.main()
