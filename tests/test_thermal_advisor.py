import unittest

import overlay
from thermal_policy import FanRamp, ThermalAdvisor, case_fan_demand, delta_severity, gpu_delta


def sample(**changes):
    data = dict(cpu_temp=45, gpu_temp=45, gpu_core_temp=45, gpu_hotspot_temp=60,
                gpu_memory_temp=60, fans=[], disks=[])
    return dict(data, **changes)


class ThermalPolicyTests(unittest.TestCase):
    def findings(self, advisor, data, now):
        return advisor.evaluate(data, now, overlay._METRIC_THRESHOLDS, overlay._disk_temperature_thresholds)

    def test_actual_report_demands_full_airflow(self):
        data = sample(cpu_temp=77, gpu_temp=53, gpu_core_temp=53, gpu_hotspot_temp=108, gpu_memory_temp=74)
        self.assertEqual(gpu_delta(data), 55)
        self.assertEqual(case_fan_demand(data)[0], 100)
        findings = self.findings(ThermalAdvisor(), data, 0)
        self.assertTrue(any(f.key == "gpu_hotspot_temp" and f.severity == 2 for f in findings))

    def test_each_hot_component_independently_wins(self):
        for key, value in (("cpu_temp", 80), ("gpu_core_temp", 75),
                           ("gpu_hotspot_temp", 95), ("gpu_memory_temp", 95)):
            with self.subTest(key=key):
                self.assertEqual(case_fan_demand(sample(**{key: value}))[0], 100)

    def test_invalid_and_missing_input_never_reduces_airflow(self):
        for key in ("cpu_temp", "gpu_core_temp", "gpu_hotspot_temp", "gpu_memory_temp"):
            for bad in (None, 0, -1, 999, float("nan"), float("inf"), True, "50"):
                with self.subTest(key=key, value=bad):
                    self.assertEqual(case_fan_demand(sample(**{key: bad}))[0], 100)

    def test_cool_profile_still_has_airflow_and_monotonic_curves(self):
        data = sample(cpu_temp=30, gpu_core_temp=30, gpu_hotspot_temp=40, gpu_memory_temp=40)
        self.assertEqual(case_fan_demand(data)[0], 60)
        values = [case_fan_demand(dict(data, cpu_temp=t))[0] for t in range(30, 100)]
        self.assertEqual(values, sorted(values))

    def test_ramp_immediate_up_delayed_down_and_deadband(self):
        ramp = FanRamp()
        self.assertEqual(ramp.update(60, 0), 100)
        self.assertEqual(ramp.update(60, 14), 100)
        self.assertEqual(ramp.update(60, 16), 96)
        self.assertEqual(ramp.update(95, 18), 96)
        self.assertEqual(ramp.update(100, 20), 100)
        self.assertEqual(ramp.update(60, 21), 100)
        self.assertEqual(ramp.update(60, 35), 100)

    def test_gap_requires_heat_and_continuous_ten_seconds(self):
        advisor = ThermalAdvisor()
        hot = sample(gpu_core_temp=53, gpu_hotspot_temp=95)
        self.assertEqual(delta_severity(sample(gpu_core_temp=30, gpu_hotspot_temp=70)), 0)
        for now in (0, 9):
            self.assertFalse(any(f.key == "gpu_gap" for f in self.findings(advisor, hot, now)))
        self.assertTrue(any(f.key == "gpu_gap" for f in self.findings(advisor, hot, 10)))
        self.findings(advisor, sample(), 11)
        self.assertFalse(any(f.key == "gpu_gap" for f in self.findings(advisor, hot, 12)))
        advisor.reset()
        self.assertFalse(any(f.key == "gpu_gap" for f in self.findings(advisor, hot, 100)))

    def test_unconnected_fan_is_not_a_stall_but_running_fan_stopping_is(self):
        advisor = ThermalAdvisor()
        stopped = sample(cpu_temp=78, fans=[dict(name="System Fan #1", id="fan1", rpm=0)])
        self.findings(advisor, stopped, 0)
        self.assertFalse(any(f.key == "fan1" for f in self.findings(advisor, stopped, 20)))
        self.findings(advisor, sample(fans=[dict(name="System Fan #1", id="fan1", rpm=800)]), 21)
        self.findings(advisor, stopped, 22)
        self.assertTrue(any(f.key == "fan1" and f.severity == 2 for f in self.findings(advisor, stopped, 32)))

    def test_storage_uses_primary_temperature_and_reports_full_disk(self):
        data = sample(disks=[dict(name="980 PRO", temp=45, aux_temp=62, used_pct=95)])
        result = self.findings(ThermalAdvisor(), data, 0)
        self.assertEqual([(f.key, f.severity) for f in result], [("space:980 PRO", 2)])

    def test_fan_display_never_invents_speed_percentage(self):
        self.assertEqual(overlay._format_fan_reading(1985), "1985 RPM")
        self.assertEqual(overlay._format_fan_reading(2764, 75), "2764 RPM | 75% ctl")
        self.assertEqual(overlay._format_fan_reading(None, 75), "-- | 75% ctl")
        self.assertEqual(overlay._format_fan_reading(0), "0 RPM")

    def test_cpu_auto_duty_is_not_replaced_by_case_fan_duty(self):
        self.assertIsNone(overlay._select_cpu_fan_control([("system fan #1", 100)], "cpu fan", True))
        self.assertEqual(overlay._select_cpu_fan_control([("cpu fan", 70)], "cpu fan", True), 70)


if __name__ == "__main__":
    unittest.main()
