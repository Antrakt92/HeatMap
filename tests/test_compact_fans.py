"""Compact fan display, source matching, and separate speed-intensity colors."""
import unittest

import overlay
from test_layout_audit import mapped_layout_app
from test_overlay_helpers import _FakeHardware, _FakeSensor, _fake_lhm_modules
from test_ui_layout import TkTestCase


def fan_preview(app):
    app.config["cpu_fan_reference_rpm"] = 2000
    app.config["case_fan_full_rpm"] = {
        "System Fan #1": 1200, "System Fan #2": 1000, "System Fan #4": 1100,
    }
    app.fan_worker.poll = lambda: dict(
        state="active", command_pct=100,
        controlled_channels=["System Fan #1", "System Fan #2"],
        firmware_channels=["System Fan #4"],
    )
    app.sensor_data = overlay._empty_sensor_data()
    app.sensor_data.update(
        cpu_temp=55, cpu_load=20, cpu_clock=4550, cpu_fan=1800, cpu_optional_fan=1900,
        gpu_core_temp=55, gpu_temp=55, gpu_load=30, gpu_clock=2200,
        gpu_hotspot_temp=78, gpu_memory_temp=72,
        gpu_vram_pct=45, gpu_vram_used_gb=8.7, gpu_vram_total_gb=20,
        ram_pct=43, ram_used_gb=13.8, ram_total_gb=32,
        fans=[dict(id=f"fan{number}", name=f"System Fan #{number}" + (" / Pump" if number >= 5 else ""),
                   rpm=rpm, control_pct=percent)
              for number, rpm, percent in ((1, 1000, 85), (2, 950, None), (3, 700, 0),
                                           (4, 1100, None), (5, 1300, 81), (6, 1300, None))],
    )
    app.update_ui()
    app.root.update_idletasks()


class FanPercentTests(unittest.TestCase):
    def test_measured_duty_precedes_estimate_without_losing_zero_or_over_100_estimate(self):
        for rpm, duty, reference, expected in (
            (1800, 67, 2000, (67, False)),
            (1800, 0, 2000, (0, False)),
            (None, 81, None, (81, False)),
            (1800, None, 2000, (90, True)),
            (2060, None, 2000, (103, True)),
            (0, None, 2000, (0, True)),
            (1800, float("nan"), 2000, (90, True)),
            (None, None, 2000, (None, False)),
            (1800, None, 0, (None, False)),
        ):
            with self.subTest(rpm=rpm, duty=duty, reference=reference):
                self.assertEqual(overlay._fan_percent(rpm, duty, reference), expected)

    def test_cpu_compact_format_preserves_estimation_marker(self):
        self.assertEqual(overlay._format_cpu_fan(1800, None, 2000), "1800 RPM · ~90%")
        self.assertEqual(overlay._format_cpu_fan(1800, 67, 2000), "1800 RPM · 67%")
        self.assertEqual(overlay._format_cpu_fan(1800, None, None), "1800 RPM")

    def test_percent_intensity_boundaries_and_unavailable_values(self):
        for percent, expected in ((0, "#4ade80"), (79.9, "#4ade80"), (80, "#fb923c"),
                                  (94.9, "#fb923c"), (95, "#f87171"), (103, "#f87171"),
                                  (None, "#888888"), (float("nan"), "#888888")):
            with self.subTest(percent=percent):
                self.assertEqual(overlay._fan_percent_color(percent), expected)


class CaseFanPercentSourceTests(unittest.TestCase):
    def read_board(self, specs):
        _modules, hardware_type, sensor_type = _fake_lhm_modules()
        chips = []
        for chip_id, readings in specs:
            chip = _FakeHardware(chip_id, hardware_type.Motherboard)
            chip.Identifier = chip_id
            for name, kind, value, identifier in readings:
                sensor = _FakeSensor(name, getattr(sensor_type, kind), value)
                sensor.Identifier = identifier
                sensor.Hardware = chip
                chip.Sensors.append(sensor)
            chips.append(chip)
        board = _FakeHardware("Board", hardware_type.Motherboard, sub_hardware=chips)
        data = overlay._empty_sensor_data()
        overlay._read_hardware_block(board, hardware_type, sensor_type, data, {})
        return {item["id"]: item for item in data["fans"]}

    def test_fan_percent_uses_matching_identifier_even_when_control_names_differ(self):
        data = self.read_board([("/lpc/chip/0", [
            ("System Fan #1", "Fan", 1200, "/lpc/chip/0/fan/1"),
            ("Fan Control 1", "Control", 81, "/lpc/chip/0/control/1"),
            ("System Fan #1", "Control", 35, "/lpc/chip/0/control/2"),
        ])])
        self.assertEqual(data["/lpc/chip/0/fan/1"].get("control_pct"), 81)

    def test_same_name_or_channel_number_on_another_chip_cannot_supply_percentage(self):
        data = self.read_board([
            ("/lpc/first/0", [("System Fan #1", "Fan", 1200, "/lpc/first/0/fan/1")]),
            ("/lpc/second/0", [("System Fan #1", "Control", 99, "/lpc/second/0/control/1")]),
        ])
        self.assertIsNone(data["/lpc/first/0/fan/1"].get("control_pct"))

    def test_duplicate_or_invalid_control_readings_remain_unknown(self):
        for controls in ([81, 85], [None], [float("nan")], [101]):
            with self.subTest(controls=controls):
                data = self.read_board([("/lpc/chip/0", [
                    ("System Fan #1", "Fan", 1200, "/lpc/chip/0/fan/1"),
                    *[("System Fan #1", "Control", value, "/lpc/chip/0/control/1") for value in controls],
                ])])
                self.assertIsNone(data["/lpc/chip/0/fan/1"].get("control_pct"))


class CompactFanLayoutTests(TkTestCase):
    def test_each_case_fan_has_its_own_measured_estimated_or_unknown_percentage(self):
        with mapped_layout_app() as (app, _area):
            fan_preview(app)
            for key, percentage in (("cpu_fan", "~90%"), ("cpu_optional_fan", "~95%"),
                                    ("case_fan_1", "85%"), ("case_fan_2", "~95%"),
                                    ("case_fan_3", "0%"), ("case_fan_4", "~100%"),
                                    ("case_fan_5", "81%"), ("case_fan_6", "--%")):
                with self.subTest(key=key):
                    self.assertEqual(app.fan_percent_labels[key].cget("text").strip(), f"· {percentage}")
                    self.assertNotIn("%", app.rows[key].cget("text"))
                    self.assertEqual(app.rows[key].cget("fg"), "#4ade80")
            self.assertIn("FW", app.rows["case_fan_4"].cget("text"))
            self.assertEqual(app.fan_percent_labels["case_fan_4"].cget("fg"), "#f87171")
            self.assertEqual(app.fan_percent_labels["case_fan_5"].cget("fg"), "#fb923c")
            self.assertEqual(app.fan_percent_labels["case_fan_6"].cget("fg"), "#888888")

    def test_stale_sensor_state_clears_percentages_and_new_readings_restore_them(self):
        with mapped_layout_app() as (app, _area):
            fan_preview(app)
            app._show_sensor_error(text="--", color="#888888")

            for label in app.fan_percent_labels.values():
                self.assertEqual(label.cget("text").strip(), "· --%")
                self.assertEqual(label.cget("fg"), "#888888")

            fan_preview(app)
            self.assertEqual(app.fan_percent_labels["cpu_fan"].cget("text").strip(), "· ~90%")
            self.assertEqual(app.fan_percent_labels["case_fan_5"].cget("text").strip(), "· 81%")

    def test_confirmed_stall_marks_rpm_red_independently_of_duty_intensity(self):
        with mapped_layout_app() as (app, _area):
            app._set_fan_reading("case_fan_1", 0, 67, stalled=True)
            self.assertEqual(app.rows["case_fan_1"].cget("text"), "0 RPM")
            self.assertEqual(app.rows["case_fan_1"].cget("fg"), "#f87171")
            self.assertEqual(app.fan_percent_labels["case_fan_1"].cget("fg"), "#4ade80")

    def test_normal_font_layout_is_compact_and_warning_does_not_widen_it(self):
        with mapped_layout_app() as (app, _area):
            fan_preview(app)
            compact_width = app.root.winfo_width()
            self.assertLessEqual(compact_width, 270)

            app._set_health_panel(["GPU Hotspot temperature warning: reduce load and check cooling. " * 3], 2)
            app._fit_content()
            app.root.update_idletasks()

            self.assertLessEqual(app.root.winfo_width(), compact_width + 8)
            self.assertLessEqual(app.root.winfo_width(), 270)
            self.assertGreaterEqual(app.health_label.winfo_height(), app.health_label.winfo_reqheight())

    def test_percentages_and_rpm_do_not_clip_or_overlap_at_larger_dpi(self):
        for scaling in (1.333, 2.0, 2.666):
            with self.subTest(scaling=scaling), mapped_layout_app(scaling=scaling, height=1200) as (app, _area):
                fan_preview(app)
                for key in ("cpu_fan", "cpu_optional_fan", *(f"case_fan_{i}" for i in range(1, 7))):
                    row = app.rows[key].master
                    visible = sorted(row.winfo_children(), key=lambda widget: widget.winfo_x())
                    for index, widget in enumerate(visible):
                        self.assertGreaterEqual(widget.winfo_width(), widget.winfo_reqwidth(), (key, scaling))
                        self.assertGreaterEqual(widget.winfo_height(), widget.winfo_reqheight(), (key, scaling))
                        self.assertLessEqual(widget.winfo_x() + widget.winfo_width(), row.winfo_width(), (key, scaling))
                        if index:
                            previous = visible[index - 1]
                            self.assertGreaterEqual(widget.winfo_x(), previous.winfo_x() + previous.winfo_width(), (key, scaling))


if __name__ == "__main__":
    unittest.main()
