import unittest

from thermal_policy import CaseAirflowPolicy, ThermalAdvisor, case_fan_demand


def sample(**changes):
    return dict(dict(cpu_temp=45, gpu_temp=40, gpu_core_temp=40,
                     gpu_hotspot_temp=60, gpu_memory_temp=60, gpu_id="gpu-a",
                     fans=[], disks=[]), **changes)


def fan(rpm, index=0, name=None, identifier=None):
    names = ("System Fan #5 / Pump", "System Fan #6 / Pump", "System Fan #4")
    return dict(name=name or names[index], id=identifier or f"/lpc/it8792e/0/fan/{index}",
                rpm=rpm, control_pct=100)


class AirflowAssistTests(unittest.TestCase):
    def feed(self, policy, temperatures, **changes):
        return [policy.update(sample(cpu_temp=value, **changes), step * 2)
                for step, value in enumerate(temperatures)]

    def test_stationary_and_cooling_profiles_keep_existing_curves(self):
        for values in ([45] * 12, [68] * 12, [76, 74, 72, 70, 68, 66, 64]):
            with self.subTest(values=values):
                results = self.feed(CaseAirflowPolicy(), values)
                self.assertEqual(results, [case_fan_demand(sample(cpu_temp=t)) for t in values])

    def test_sustained_cpu_rise_anticipates_heat_without_changing_curves(self):
        results = self.feed(CaseAirflowPolicy(), [60, 62, 64, 65, 66, 68])
        self.assertEqual(results[-1], (86, "CPU rising: airflow assist"))
        self.assertEqual(case_fan_demand(sample(cpu_temp=68)), (81, "CPU curve"))
        self.assertEqual(results[:3], [case_fan_demand(sample(cpu_temp=t)) for t in (60, 62, 64)])

    def test_hotspot_and_memory_can_independently_anticipate(self):
        for key, label in (("gpu_hotspot_temp", "GPU Hotspot"), ("gpu_memory_temp", "GPU Memory")):
            policy = CaseAirflowPolicy()
            for step, value in enumerate((74, 76, 78, 80)):
                data = sample(gpu_core_temp=50, **{key: value})
                result = policy.update(data, step * 2)
            self.assertEqual(result, (87, label + " rising: airflow assist"))

    def test_single_spike_and_one_degree_jitter_do_not_count_as_sustained_rise(self):
        for values in ([60, 60, 60, 60, 60, 68], [68, 69, 68, 69, 68, 69]):
            results = self.feed(CaseAirflowPolicy(), values)
            self.assertEqual(results[-1], case_fan_demand(sample(cpu_temp=values[-1])))

    def test_latest_falling_temperature_disables_prediction(self):
        result = self.feed(CaseAirflowPolicy(), [60, 62, 64, 66, 65])[-1]
        self.assertEqual(result, case_fan_demand(sample(cpu_temp=65)))

    def test_assist_is_upward_only_and_capped_at_ten_points(self):
        for start in (30, 40, 50, 60, 70):
            values = [start, start + 1, start + 2, start + 5]
            results = self.feed(CaseAirflowPolicy(), values)
            for value, (demand, _) in zip(values, results):
                base = case_fan_demand(sample(cpu_temp=value))[0]
                self.assertGreaterEqual(demand, base)
                self.assertLessEqual(demand, min(100, base + 10))

    def test_five_degree_projection_limit_bounds_fast_rise(self):
        result = self.feed(CaseAirflowPolicy(), [40, 50, 60, 70])[-1]
        self.assertEqual(result[0], 90)  # Projects at most75C, not the uncapped95C.

    def test_missing_temperatures_and_hotspot_gap_keep_full_airflow_priority(self):
        for changes in (dict(cpu_temp=None), dict(cpu_temp=float("nan")),
                        dict(gpu_core_temp=45, gpu_hotspot_temp=85)):
            policy = CaseAirflowPolicy()
            self.feed(policy, [60, 62, 64, 66])
            data = sample(**changes)
            self.assertEqual(policy.update(data, 8), case_fan_demand(data))
            self.assertEqual(policy.update(sample(cpu_temp=68), 10), case_fan_demand(sample(cpu_temp=68)))

    def test_nonmonotonic_times_gaps_and_gpu_changes_restart_history(self):
        for now, gpu in ((6, "gpu-a"), (5, "gpu-a"), (12, "gpu-a"), (8, "gpu-b")):
            with self.subTest(now=now, gpu=gpu):
                policy = CaseAirflowPolicy()
                self.feed(policy, [60, 62, 64, 66])
                data = sample(cpu_temp=68, gpu_id=gpu)
                self.assertEqual(policy.update(data, now), case_fan_demand(data))

    def test_invalid_time_fails_to_full_airflow_then_restarts_history(self):
        for now in (None, float("nan"), float("inf"), True, 10 ** 1000):
            policy = CaseAirflowPolicy()
            self.feed(policy, [60, 62, 64, 66])
            self.assertEqual(policy.update(sample(), now)[0], 100)
            self.assertEqual(policy.update(sample(cpu_temp=68), 8), case_fan_demand(sample(cpu_temp=68)))

    def test_old_temperature_history_expires(self):
        policy = CaseAirflowPolicy()
        self.feed(policy, [60, 62, 64, 66])
        for now in range(8, 20, 2):
            result = policy.update(sample(cpu_temp=66), now)
        self.assertEqual(result, case_fan_demand(sample(cpu_temp=66)))

    def test_known_running_firmware_headers_need_two_fresh_zero_samples(self):
        for index in (0, 1, 2):
            policy = CaseAirflowPolicy()
            policy.update(sample(cpu_temp=72, fans=[fan(900, index)]), 0)
            data = sample(cpu_temp=72, fans=[fan(0, index)])
            self.assertEqual(policy.update(data, 2), case_fan_demand(data))
            result = policy.update(data, 4)
            self.assertEqual(result[0], 100)
            self.assertIn("confirmed 0 RPM", result[1])

    def test_never_running_foreign_or_wrongly_named_headers_cannot_trigger_stall(self):
        for changes in ({}, {"identifier": "/foreign/fan/0"}, {"name": "CPU Fan"}):
            policy = CaseAirflowPolicy()
            if changes:
                policy.update(sample(cpu_temp=72, fans=[fan(900, **changes)]), 0)
            for now in (2, 4, 6):
                data = sample(cpu_temp=72, fans=[fan(0, **changes)])
                self.assertEqual(policy.update(data, now), case_fan_demand(data))

    def test_missing_tach_breaks_zero_confirmation_without_inventing_stall(self):
        policy = CaseAirflowPolicy()
        policy.update(sample(cpu_temp=72, fans=[fan(900)]), 0)
        for now, rpm in ((2, 0), (4, None), (6, 0)):
            data = sample(cpu_temp=72, fans=[fan(rpm)])
            self.assertEqual(policy.update(data, now), case_fan_demand(data))
        self.assertEqual(policy.update(sample(cpu_temp=72, fans=[fan(0)]), 8)[0], 100)

    def test_idle_stopped_fan_or_full_duty_alone_does_not_raise_airflow(self):
        policy = CaseAirflowPolicy()
        for now, rpm in ((0, 900), (2, 0), (4, 0), (6, 900)):
            data = sample(fans=[fan(rpm)])
            self.assertEqual(policy.update(data, now), case_fan_demand(data))

    def test_stall_timer_resets_on_duplicate_time_gap_and_duplicate_identity(self):
        for reset in ("duplicate_time", "gap", "duplicate_identity"):
            policy = CaseAirflowPolicy()
            policy.update(sample(cpu_temp=72, fans=[fan(900)]), 0)
            data = sample(cpu_temp=72, fans=[fan(0)])
            policy.update(data, 2)
            if reset == "duplicate_time":
                self.assertEqual(policy.update(data, 2), case_fan_demand(data))
            elif reset == "gap":
                self.assertEqual(policy.update(data, 8), case_fan_demand(data))
            else:
                duplicate = dict(data, fans=[fan(0), fan(0)])
                self.assertEqual(policy.update(duplicate, 4), case_fan_demand(duplicate))
                self.assertEqual(policy.update(data, 6), case_fan_demand(data))


class MissingTachAdvisorTests(unittest.TestCase):
    def evaluate(self, advisor, rpm, now, cpu_temp=72):
        thresholds = {key: (120, 140) for key in ("cpu_temp", "gpu_temp", "gpu_hotspot_temp", "gpu_memory_temp")}
        thresholds.update(disk_used=(90, 95), ram_pct=(90, 95), gpu_vram_pct=(90, 95))
        return advisor.evaluate(sample(cpu_temp=cpu_temp, fans=[fan(rpm)]), now,
                                thresholds, lambda _: (80, 90))

    def test_previously_running_tach_unavailable_warns_after_ten_seconds(self):
        advisor = ThermalAdvisor()
        self.evaluate(advisor, 900, 0)
        self.assertFalse(self.evaluate(advisor, None, 2))
        self.assertFalse(self.evaluate(advisor, None, 11))
        findings = self.evaluate(advisor, None, 12)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, 1)
        self.assertIn("tachometer unavailable", findings[0].text)
        self.assertNotIn("0 RPM", findings[0].text)

    def test_missing_and_zero_timers_do_not_share_confirmation(self):
        advisor = ThermalAdvisor()
        self.evaluate(advisor, 900, 0)
        self.evaluate(advisor, None, 2)
        self.assertFalse(self.evaluate(advisor, 0, 12))
        findings = self.evaluate(advisor, 0, 22)
        self.assertEqual(findings[0].severity, 2)
        self.assertIn("0 RPM", findings[0].text)
        self.assertFalse(self.evaluate(advisor, None, 24))

    def test_cold_or_never_observed_fan_has_no_missing_tach_alarm(self):
        advisor = ThermalAdvisor()
        self.evaluate(advisor, None, 0)
        self.assertFalse(self.evaluate(advisor, None, 20))
        self.evaluate(advisor, 900, 22)
        self.evaluate(advisor, None, 24, cpu_temp=45)
        self.assertFalse(self.evaluate(advisor, None, 44, cpu_temp=45))


if __name__ == "__main__":
    unittest.main()
