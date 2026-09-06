"""Header ownership remains truthful across worker failures and restoration."""
import unittest
from unittest import mock

import overlay
from test_compact_fans import fan_preview
from test_ui_layout import TkTestCase, layout_app


class FanOwnershipTests(unittest.TestCase):
    def test_restore_evidence_overrides_historical_selection(self):
        selected = ["System Fan #1", "System Fan #2", "System Fan #4"]
        for state, restored, expected in (
            ("active", None, ""), ("checking", None, ""),
            ("stopped", True, "FW"), ("error", True, "FW"),
            ("stopped", False, "?"), ("error", False, "?"),
            ("error", "true", "?"), ("off", None, "FW"),
        ):
            with self.subTest(state=state, restored=restored):
                status = dict(state=state, controlled_channels=selected, restore_confirmed=restored)
                for name in selected:
                    self.assertEqual(overlay._case_fan_owner(status, name), expected)
                self.assertEqual(overlay._case_fan_owner(status, "System Fan #5 / Pump"), "FW")

    def test_missing_or_corrupt_report_never_confirms_default_controller_restoration(self):
        for selected in (None, [], "System Fan #1", ["System Fan #4", None]):
            with self.subTest(selected=selected):
                status = dict(state="error", controlled_channels=selected)
                self.assertEqual(overlay._case_fan_owner(status, "System Fan #1"), "?")
                self.assertEqual(overlay._case_fan_owner(status, "System Fan #2"), "?")
                self.assertEqual(overlay._case_fan_owner(status, "System Fan #5 / Pump"), "FW")
        self.assertEqual(overlay._case_fan_owner(dict(state="error", controlled_channels=["System Fan #4", None]),
                                                "System Fan #4"), "?")
        self.assertEqual(overlay._case_fan_owner({}, "System Fan #1"), "?")
        self.assertEqual(overlay._case_fan_owner(None, "System Fan #1"), "?")
        self.assertEqual(overlay._case_fan_owner(dict(state="off"), "Unidentified Fan #1"), "?")

    def test_header_alias_is_not_a_physical_device_or_fixed_firmware_assignment(self):
        status = dict(state="active", controlled_channels=["System Fan #5 / Pump"])
        self.assertEqual(overlay._case_fan_owner(status, "System Fan #5"), "")
        self.assertEqual(overlay._case_fan_owner(status, "System Fan #1"), "FW")
        status["state"] = "error"
        self.assertEqual(overlay._case_fan_owner(status, "System Fan #5"), "?")


class FanOwnershipUiTests(TkTestCase):
    def test_cooling_status_keeps_close_outside_scroll_content_at_large_dpi(self):
        for scaling in (1.333, 2.0, 2.666):
            with self.subTest(scaling=scaling), layout_app(scaling=scaling, height=720) as app:
                real_toplevel = overlay.tk.Toplevel

                def hidden_dialog(*args, **kwargs):
                    dialog = real_toplevel(*args, **kwargs)
                    dialog.withdraw()
                    return dialog

                app._case_fan_status = dict(state="active", controlled_channels=["System Fan #1", "System Fan #2"],
                                            reason="GPU Hotspot rising: airflow assist")
                with mock.patch.object(overlay.tk, "Toplevel", side_effect=hidden_dialog):
                    app.show_cooling_status()
                dialog = app._cooling_dialog
                app.root.update_idletasks()
                self.assertLessEqual(dialog.winfo_reqheight(), 680)
                self.assertLessEqual(dialog.winfo_reqwidth(), 510)
                viewport = next(child for child in dialog.winfo_children() if isinstance(child, overlay.tk.Frame))
                canvas = next(child for child in viewport.winfo_children() if isinstance(child, overlay.tk.Canvas))
                label = next(child for child in dialog.winfo_children() if isinstance(child, overlay.tk.Label))
                if scaling >= 2:
                    self.assertGreater(label.winfo_reqheight(), int(canvas.cget("height")))
                    self.assertTrue(canvas.cget("yscrollcommand"))
                close = next(child for child in dialog.winfo_children() if isinstance(child, overlay.tk.Button))
                self.assertIs(close.master, dialog)
                self.assertFalse(dialog.winfo_ismapped())
                close.invoke()
                self.assertIsNone(app._cooling_dialog)

    def test_all_headers_show_ownership_without_changing_percentage_source(self):
        with layout_app() as app:
            fan_preview(app)
            for number in range(1, 7):
                row = app.rows[f"case_fan_{number}"]
                captions = [child.cget("text").strip() for child in row.master.winfo_children()
                            if isinstance(child, overlay.tk.Label)]
                self.assertIn(f"SYS {number}", captions)
                self.assertFalse(any("Pump" in caption for caption in captions))
                self.assertEqual(" · FW" in row.cget("text"), number not in (1, 2))
            self.assertEqual(app.fan_percent_labels["case_fan_5"].cget("text"), " · 81%")
            self.assertEqual(app.fan_percent_labels["case_fan_4"].cget("text"), " · ~100%")
            app.fan_worker.poll = lambda: dict(state="error", restore_confirmed=False,
                                               controlled_channels=["System Fan #1", "System Fan #2"])
            app.update_ui()
            self.assertTrue(app.rows["case_fan_1"].cget("text").endswith(" · ?"))
            self.assertTrue(app.rows["case_fan_5"].cget("text").endswith(" · FW"))
            app.fan_worker.poll = lambda: dict(state="stopped", restore_confirmed=True,
                                               controlled_channels=["System Fan #1", "System Fan #2"])
            app.update_ui()
            self.assertTrue(app.rows["case_fan_1"].cget("text").endswith(" · FW"))

    def test_cooling_copy_tracks_restoration_and_explains_incomparable_percentages(self):
        with layout_app() as app:
            real_toplevel = overlay.tk.Toplevel

            def hidden_dialog(*args, **kwargs):
                dialog = real_toplevel(*args, **kwargs)
                dialog.withdraw()
                return dialog

            app._case_fan_status = dict(state="active", controlled_channels=["System Fan #1", "System Fan #2"])
            with mock.patch.object(overlay.tk, "Toplevel", side_effect=hidden_dialog):
                app.show_cooling_status()
            dialog = app._cooling_dialog
            label = next(child for child in dialog.winfo_children() if isinstance(child, overlay.tk.Label))
            text = label.cget("text")
            self.assertIn("HeatMap: SYS 1, SYS 2", text)
            self.assertIn("Firmware: SYS 3, SYS 4, SYS 5, SYS 6", text)
            self.assertIn("Bare %: controller duty readback", text)
            self.assertIn("~%: RPM / reference RPM, not duty", text)
            self.assertIn("independent firmware restoration", text)
            self.assertNotIn("pumps", text.lower())
            close = next(child for child in dialog.winfo_children() if isinstance(child, overlay.tk.Button))
            close.invoke()
            app._case_fan_status = dict(state="active", controlled_channels=["System Fan #1", "System Fan #4"])
            with mock.patch.object(overlay.tk, "Toplevel", side_effect=hidden_dialog):
                app.show_cooling_status()
            label = next(child for child in app._cooling_dialog.winfo_children() if isinstance(child, overlay.tk.Label))
            self.assertIn("HeatMap: SYS 1, SYS 4", label.cget("text"))
            self.assertNotIn("SYS4/5/6 remain", label.cget("text"))


if __name__ == "__main__":
    unittest.main()
