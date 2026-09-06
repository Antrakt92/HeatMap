"""Pair measured board fan duty only by exact channel identity on its chip."""
import unittest

import overlay
from test_overlay_helpers import _FakeHardware, _FakeSensor, _fake_lhm_modules


class CaseFanPercentSensorTests(unittest.TestCase):
    def setUp(self):
        _, self.hardware_type, self.sensor_type = _fake_lhm_modules()

    def sensor(self, name, kind, value, identifier=None):
        sensor = _FakeSensor(name, getattr(self.sensor_type, kind), value)
        if identifier is not None:
            sensor.Identifier = identifier
        return sensor

    @staticmethod
    def chip(identifier, sensors):
        chip = _FakeHardware(identifier, "SuperIO", sensors=sensors)
        chip.Identifier = identifier
        return chip

    def read(self, *chips):
        board = _FakeHardware("Motherboard", self.hardware_type.Motherboard, sub_hardware=list(chips))
        data = overlay._empty_sensor_data()
        candidates = {"cpu_temp": [], "gpus": [], "ram_pct": []}
        overlay._read_hardware_block(board, self.hardware_type, self.sensor_type, data, candidates)
        return {fan["id"]: fan for fan in data["fans"]}

    def test_control_order_does_not_change_the_measured_percentage(self):
        for control_first in (True, False):
            with self.subTest(control_first=control_first):
                fan = self.sensor("System Fan #1", "Fan", 1200, "/chip-a/fan/0")
                control = self.sensor("System Fan #1", "Control", 64, "/chip-a/control/0")
                sensors = [control, fan] if control_first else [fan, control]
                result = self.read(self.chip("/chip-a", sensors))
                self.assertEqual(result["/chip-a/fan/0"]["control_pct"], 64)
                self.assertEqual(result["/chip-a/fan/0"]["rpm"], 1200)

    def test_same_fan_names_on_different_chips_never_share_percentages(self):
        chips = []
        for chip_id, duty in (("/chip-a", 61), ("/chip-b", 84)):
            chips.append(self.chip(chip_id, [
                self.sensor("System Fan #1", "Fan", 1200, chip_id + "/fan/0"),
                self.sensor("System Fan #1", "Control", duty, chip_id + "/control/0"),
            ]))
        for ordered in (chips, chips[::-1]):
            result = self.read(*ordered)
            self.assertEqual(result["/chip-a/fan/0"]["control_pct"], 61)
            self.assertEqual(result["/chip-b/fan/0"]["control_pct"], 84)

    def test_same_name_control_on_another_chip_does_not_fill_missing_duty(self):
        result = self.read(
            self.chip("/chip-a", [self.sensor("System Fan #1", "Fan", 1200, "/chip-a/fan/0")]),
            self.chip("/chip-b", [self.sensor("System Fan #1", "Control", 84, "/chip-b/control/0")]),
        )
        self.assertIsNone(result["/chip-a/fan/0"]["control_pct"])

    def test_duplicate_control_identifiers_fail_closed_even_when_values_agree(self):
        for second in (82, 60, None):
            with self.subTest(second=second):
                result = self.read(self.chip("/chip-a", [
                    self.sensor("System Fan #5 / Pump", "Fan", 1350, "/chip-a/fan/4"),
                    self.sensor("System Fan #5 / Pump", "Control", 82, "/chip-a/control/4"),
                    self.sensor("System Fan #5 / Pump", "Control", second, "/chip-a/control/4"),
                ]))
                self.assertIsNone(result["/chip-a/fan/4"]["control_pct"])

    def test_unavailable_or_invalid_control_is_not_invented_from_rpm(self):
        for value in (None, float("nan"), float("inf"), -1, 101, True, "82"):
            with self.subTest(value_type=type(value).__name__):
                result = self.read(self.chip("/chip-a", [
                    self.sensor("System Fan #1", "Fan", 1200, "/chip-a/fan/0"),
                    self.sensor("System Fan #1", "Control", value, "/chip-a/control/0"),
                ]))
                self.assertIsNone(result["/chip-a/fan/0"]["control_pct"])

    def test_sys5_pump_reports_its_actual_82_percent_measurement(self):
        result = self.read(self.chip("/lpc/it8792e/0", [
            self.sensor("System Fan #5 / Pump", "Fan", 1390, "/lpc/it8792e/0/fan/4"),
            self.sensor("System Fan #5 / Pump", "Control", 82, "/lpc/it8792e/0/control/4"),
        ]))
        fan = result["/lpc/it8792e/0/fan/4"]
        self.assertEqual(fan["rpm"], 1390)
        self.assertEqual(fan["control_pct"], 82)

    def test_zero_and_full_duty_remain_valid_measurements_with_renamed_controls(self):
        for duty in (0, 100):
            with self.subTest(duty=duty):
                result = self.read(self.chip("/chip-a", [
                    self.sensor("System Fan #1", "Fan", 1200, "/chip-a/fan/0"),
                    self.sensor("Renamed PWM output", "Control", duty, "/chip-a/control/0"),
                ]))
                self.assertEqual(result["/chip-a/fan/0"]["control_pct"], duty)

    def test_same_name_with_different_channel_number_is_not_a_match(self):
        result = self.read(self.chip("/chip-a", [
            self.sensor("System Fan #5 / Pump", "Fan", 1390, "/chip-a/fan/4"),
            self.sensor("System Fan #5 / Pump", "Control", 82, "/chip-a/control/5"),
        ]))
        self.assertIsNone(result["/chip-a/fan/4"]["control_pct"])

    def test_missing_identifiers_cannot_be_paired_using_names(self):
        result = self.read(self.chip("/chip-a", [
            self.sensor("System Fan #1", "Fan", 1200),
            self.sensor("System Fan #1", "Control", 82),
        ]))
        self.assertEqual(len(result), 1)
        self.assertIsNone(next(iter(result.values()))["control_pct"])


if __name__ == "__main__":
    unittest.main()
