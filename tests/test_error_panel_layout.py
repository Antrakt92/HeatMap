"""Real, transparent Tk regressions for compact sensor-failure layout."""
import overlay
from test_layout_audit import mapped_layout_app
from test_ui_layout import TkTestCase


class ErrorPanelLayoutTests(TkTestCase):
    def test_removed_storage_rows_do_not_leave_their_old_height(self):
        for scaling in (1.333, 2.0, 2.666):
            with self.subTest(scaling=scaling), mapped_layout_app(scaling, height=1200) as (app, _area):
                app._set_health_panel(["Fresh sensor data unavailable"], 1)
                app._fit_content()
                baseline = app.canvas.winfo_height()
                disks = [dict(name=f"Disk {number}", temp=40, disk_number=number)
                         for number in range(4)]
                app._update_storage_rows(disks, None)
                app._fit_content()
                self.assertGreater(app.canvas.winfo_height(), baseline)

                app._show_sensor_error(text="--")

                self.assertEqual(app.disk_frame.winfo_children(), [])
                self.assertLessEqual(app.canvas.winfo_height(), baseline)
                ram = app.rows["ram_gb"].master
                gap = app.footer.winfo_rooty() - (ram.winfo_rooty() + ram.winfo_height())
                self.assertLessEqual(gap, 16)

                app._update_storage_rows(disks, None)
                app._fit_content()
                self.assertGreater(app.disk_frame.winfo_height(), 1)
                self.assertEqual(len(app.disk_labels), 4)

    def test_long_error_panel_fits_after_storage_disappears(self):
        for scaling in (1.333, 2.0, 2.666):
            with self.subTest(scaling=scaling), mapped_layout_app(scaling, height=700) as (app, _area):
                app._update_storage_rows([dict(name=f"Disk {number}", temp=40)
                                          for number in range(4)], None)
                app._fit_content()
                app._show_sensor_error(text="--")
                app._set_sensor_status(overlay.SENSOR_STATUS_STALE)
                app._set_health_panel([
                    "GPU fans: Controller stopped; toggle GPU fans OFF then ON",
                    "Case fans: automatic control stopped; firmware restored. Copy diagnostics for details.",
                    "Sensors paused: Other hardware monitoring tools are running: gcc.exe. "
                    "Close these tools before reading sensors or controlling fans.",
                ], 2)
                app._fit_content()

                self.assertLessEqual(app.root.winfo_height(), 700)
                self.assertGreaterEqual(app.health_label.winfo_height(), app.health_label.winfo_reqheight())
                self.assertLessEqual(app.health_label.winfo_rooty() + app.health_label.winfo_height(),
                                     app.root.winfo_rooty() + app.root.winfo_height())
                self.assertLessEqual(app.status_label.winfo_rooty() + app.status_label.winfo_height(),
                                     app.health_label.winfo_rooty())
