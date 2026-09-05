"""Real Tk geometry without hardware access, audio, visible windows or settings writes."""
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
import gc
import threading
import unittest
from unittest import mock
import weakref

import overlay


@contextmanager
def layout_app(scaling=1.333, height=720):
    real_tk = overlay.tk.Tk
    def hidden_root():
        root = real_tk()
        root.withdraw()
        root.tk.call("tk", "scaling", scaling)
        return root
    areas = (((0, 0, 1280, height), (0, 0, 1280, height)),)
    with ExitStack() as stack:
        # Suppress dispatch, not just winsound.Beep: a delayed second beep could
        # otherwise outlive the fixture and run after the sound mock is removed.
        for name in ("sensor_loop", "_check_alerts", "_schedule_embed", "_poll_screen_change", "_poll_peek_edge", "_poll_desktop_visibility"):
            stack.enter_context(mock.patch.object(overlay.OverlayApp, name))
        stack.enter_context(mock.patch.object(overlay.tk, "Tk", side_effect=hidden_root))
        stack.enter_context(mock.patch.object(overlay, "_get_monitor_areas", return_value=areas))
        stack.enter_context(mock.patch.object(overlay, "is_pawnio_driver_installed", return_value=True))
        stack.enter_context(mock.patch.object(overlay, "load_config_result", return_value=(overlay._default_config(), None)))
        stack.enter_context(mock.patch.object(overlay, "save_config", return_value=(True, "")))
        app = overlay.OverlayApp(SimpleNamespace(enabled=False))
        try:
            yield app
        finally:
            app.running = False
            for callback in app.root.tk.call("after", "info"):
                app.root.after_cancel(callback)
            app.root.destroy()


class TkTestCase(unittest.TestCase):
    def tearDown(self):
        super().tearDown()
        # Mock call records can retain destroyed Tk roots in unreachable cycles.
        # Collect after test-method locals have gone, on the interpreter's owner
        # thread, before a later worker happens to trigger cyclic collection.
        gc.collect()


class LayoutTests(TkTestCase):
    def test_destroyed_fixture_interpreter_is_released_before_later_worker_tests(self):
        roots = []
        finalizer_threads = []

        class FixtureTest(TkTestCase):
            def runTest(self):
                with layout_app() as app:
                    roots.append(weakref.ref(app.root))
                    weakref.finalize(app.root, lambda: finalizer_threads.append(threading.get_ident()))
                    with mock.patch.object(overlay.simpledialog, "askinteger", return_value=2000):
                        app.configure_cpu_reference()

        was_enabled = gc.isenabled()
        gc.disable()
        try:
            result = FixtureTest().run()
            self.assertTrue(result.wasSuccessful(), result.errors or result.failures)
            self.assertEqual(len(roots), 1)
            self.assertIsNone(roots[0]())
            self.assertEqual(finalizer_threads, [threading.get_ident()])
        finally:
            # Also clean safely if the regression intentionally fails.
            gc.collect()
            if was_enabled:
                gc.enable()

    def test_critical_preview_updates_visuals_without_starting_audio_thread(self):
        with layout_app() as app:
            app.alerts_enabled = True
            app.sensor_data = overlay._empty_sensor_data()
            app.sensor_data.update(cpu_temp=95, gpu_core_temp=50, gpu_temp=50,
                                   gpu_hotspot_temp=110, ram_pct=99)
            with mock.patch.object(overlay.threading, "Thread") as worker:
                app.update_ui()
            worker.assert_not_called()
            self.assertEqual(app.rows["gpu_hotspot_temp"].cget("fg"), "#f87171")
            self.assertIn("GPU Hotspot", app.health_label.cget("text"))

    def test_empty_warning_panel_reclaims_space_and_reappears_for_a_problem(self):
        with layout_app() as app:
            app._set_sensor_status(None)
            app._set_health_panel(["RAM: 95%"], 2)
            app._fit_content()
            with_warning = app.root.winfo_reqheight()
            app._set_health_panel([], 0)
            app._fit_content()
            self.assertEqual(app.health_label.winfo_manager(), "")
            self.assertEqual(app.footer.winfo_manager(), "")
            self.assertLess(app.root.winfo_reqheight(), with_warning)
            app._set_health_panel(["Unavailable: CPU"], 1)
            app._fit_content()
            self.assertEqual(app.health_label.winfo_manager(), "pack")
            self.assertEqual(app.footer.winfo_manager(), "pack")
            self.assertEqual(app.health_label.cget("text"), "Unavailable: CPU")
            app._set_health_panel([], 0)
            app._set_sensor_status(overlay.SENSOR_STATUS_STALE)
            app._fit_content()
            self.assertEqual(app.footer.winfo_manager(), "pack")
            self.assertEqual(app.status_label.winfo_manager(), "pack")

    def test_expanded_metrics_scroll_without_hiding_warnings_or_close_button(self):
        for scaling in (1.333, 2.0, 2.666):
            with self.subTest(scaling=scaling), layout_app(scaling, height=640) as app:
                app.details_enabled = True
                app._apply_details_visibility()
                for number in (1, 2, 4, 5):
                    app.rows[f"case_fan_{number}"].master.pack(fill="x", before=app.rows["case_fan_control"].master)
                for key in ("detail_board_temps", "detail_disk_sensors"):
                    app.rows[key].configure(text="Sensor one 54°C | Sensor two 74°C | Sensor three 88°C | Sensor four 56°C")
                app._make_disk_row("disk_0", "Long NVMe model " * 8, app.disk_frame)
                app._set_health_panel(["GPU Hotspot 108°C: reduce load / check cooling", "RAM: 99%"], 2)
                app._fit_content()
                app.root.update_idletasks()
                self.assertLessEqual(app.root.winfo_reqheight(), 640)
                self.assertLessEqual(app.root.winfo_reqwidth(), 1280)
                self.assertTrue(app._content_overflow)
                self.assertIs(app.health_label.master, app.footer)
                before = app.canvas.yview()
                app._scroll_content(SimpleNamespace(delta=-1200))
                self.assertGreater(app.canvas.yview()[0], before[0])

    def test_collapsing_details_restores_compact_height_and_scroll_position(self):
        with layout_app(height=640) as app:
            app.details_enabled = True
            app._apply_details_visibility()
            app._fit_content()
            app.canvas.yview_moveto(1)
            app.details_enabled = False
            app._apply_details_visibility()
            app._fit_content()
            app.root.update_idletasks()
            self.assertFalse(app._content_overflow)
            self.assertEqual(app.canvas.yview()[0], 0)
            self.assertLess(app.root.winfo_reqheight(), 640)

    def test_peek_layout_uses_widget_monitor_instead_of_edge_monitor(self):
        with layout_app(height=1000) as app:
            app.details_enabled = True
            app._apply_details_visibility()
            app._fit_content()
            before = (app.root.winfo_reqwidth(), app.root.winfo_reqheight())
            app.peek_visible = True
            app._peek_monitor_area = ((1280, 0, 2560, 400), (1280, 0, 2560, 400))
            app._fit_content()
            self.assertEqual((app.root.winfo_reqwidth(), app.root.winfo_reqheight()), before)
            self.assertEqual((app.config["x"], app.config["y"]), (50, 50))


if __name__ == "__main__":
    unittest.main()
