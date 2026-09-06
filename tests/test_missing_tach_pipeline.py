"""Exercise missing tach feedback from the real board parser through the advisor."""
import unittest

import overlay
from thermal_policy import ThermalAdvisor
from test_overlay_helpers import _FakeHardware, _FakeSensor, _fake_lhm_modules


class MissingTachPipelineTests(unittest.TestCase):
    def setUp(self):
        _, self.hardware_type, self.sensor_type = _fake_lhm_modules()

    def sensor(self, name, kind, value, identifier):
        result = _FakeSensor(name, getattr(self.sensor_type, kind), value)
        result.Identifier = identifier
        return result

    def read(self, sensors):
        chip = _FakeHardware("IT8792E", "SuperIO", sensors=sensors)
        board = _FakeHardware("Motherboard", self.hardware_type.Motherboard, sub_hardware=[chip])
        data = overlay._empty_sensor_data()
        overlay._read_hardware_block(board, self.hardware_type, self.sensor_type, data,
                                     {"cpu_temp": [], "gpus": [], "ram_pct": []})
        data.update(cpu_temp=72, gpu_temp=40, gpu_core_temp=40, gpu_hotspot_temp=60, gpu_memory_temp=60)
        return data

    def evaluate(self, advisor, data, now):
        return advisor.evaluate(data, now, overlay._METRIC_THRESHOLDS, overlay._disk_temperature_thresholds)

    def test_present_sensor_with_none_value_reaches_delayed_missing_tach_warning(self):
        tach = self.sensor("System Fan #5 / Pump", "Fan", 900, "/lpc/it8792e/0/fan/0")
        control = self.sensor("System Fan #5 / Pump", "Control", 82, "/lpc/it8792e/0/control/0")
        advisor = ThermalAdvisor()
        self.evaluate(advisor, self.read([tach, control]), 0)
        tach._value = None
        missing = self.read([tach, control])
        self.assertEqual(missing["fans"], [dict(name=tach.Name, id=tach.Identifier, rpm=None, control_pct=82)])
        for now in (2, 11):
            self.assertFalse(any("tachometer unavailable" in f.text for f in self.evaluate(advisor, missing, now)))
        findings = self.evaluate(advisor, missing, 12)
        warning = next(f for f in findings if "tachometer unavailable" in f.text)
        self.assertEqual(warning.severity, 1)
        self.assertNotIn("0 RPM", warning.text)
        tach._value = 850
        self.assertFalse(any("tachometer unavailable" in f.text for f in self.evaluate(advisor, self.read([tach, control]), 14)))

    def test_unknown_rpm_is_retained_without_becoming_zero(self):
        for value in (None, float("nan"), float("inf"), -1):
            with self.subTest(value=value):
                tach = self.sensor("System Fan #4", "Fan", value, "/lpc/it8792e/0/fan/2")
                data = self.read([tach])
                self.assertEqual(len(data["fans"]), 1)
                self.assertIsNone(data["fans"][0]["rpm"])
                advisor = ThermalAdvisor()
                self.evaluate(advisor, data, 0)
                self.assertFalse(any("tachometer" in f.text or "0 RPM" in f.text
                                     for f in self.evaluate(advisor, data, 20)))

    def test_missing_optional_cpu_tach_cannot_replace_primary_fan(self):
        primary = self.sensor("CPU Fan", "Fan", 900, "/lpc/it8688e/0/fan/0")
        optional = self.sensor("CPU Optional Fan", "Fan", None, "/lpc/it8688e/0/fan/3")
        system = self.sensor("System Fan #1", "Fan", 1200, "/lpc/it8688e/0/fan/1")
        data = self.read([optional, system, primary])
        self.assertEqual(data["cpu_fan"], 900)
        self.assertEqual(data["cpu_fan_id"], primary.Identifier)
        self.assertIsNone(data["cpu_optional_fan"])
        self.assertNotIn("cpu_optional_fan_id", data)
        self.assertEqual(len(data["fans"]), 3)

    def test_missing_cpu_tach_cannot_be_replaced_by_system_rpm(self):
        primary = self.sensor("CPU Fan", "Fan", None, "/lpc/it8688e/0/fan/0")
        system = self.sensor("System Fan #1", "Fan", 1200, "/lpc/it8688e/0/fan/1")
        data = self.read([primary, system])
        self.assertIsNone(data["cpu_fan"])
        self.assertNotIn("cpu_fan_id", data)
        self.assertEqual(len(data["fans"]), 2)


if __name__ == "__main__":
    unittest.main()
