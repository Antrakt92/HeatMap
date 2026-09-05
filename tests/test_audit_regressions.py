import json
import os
import tempfile
import threading
import unittest
from unittest import mock

import overlay
from thermal_policy import ThermalAdvisor
import test_sensor_validation as sensor_validation
from test_thermal_advisor import sample
from test_overlay_helpers import _FakeLabel, _update_ui_app


class AuditRegressionTests(unittest.TestCase):
    def readings(self, sensors):
        fixture = sensor_validation.SensorValidationTests()
        fixture.setUp()
        return fixture.read(fixture.hardware("GpuNvidia", sensors, "Test GPU"))

    def findings(self, advisor, data, now):
        return advisor.evaluate(data, now, overlay._METRIC_THRESHOLDS, overlay._disk_temperature_thresholds)

    def test_gpu_fan_control_cannot_come_from_power_limit(self):
        data = self.readings([("GPU Fan", "Fan", 1000), ("GPU Fan", "Control", 40),
                              ("Power Limit", "Control", 75)])
        self.assertEqual(data["gpu_fan_pct"], 40)

    def test_numbered_gpu_control_never_falls_back_to_another_channel(self):
        data = self.readings([("GPU Fan 1", "Fan", 1000), ("GPU Fan 2", "Control", 75)])
        self.assertIsNone(data["gpu_fan_pct"])
        data = self.readings([("GPU Fan #1", "Fan", 1000), ("GPU Fan 1", "Control", 40)])
        self.assertEqual(data["gpu_fan_pct"], 40)

    def test_generic_and_numbered_gpu_fans_sort_without_mixing_types(self):
        data = self.readings([("GPU Fan #10", "Fan", 1000), ("GPU Fan", "Fan", 1200), ("GPU Fan 2", "Fan", 1300)])
        self.assertEqual([f["rpm"] for f in data["gpu_fans"]], [1200, 1300, 1000])

    def test_all_gpu_fans_survive_sensor_order_and_primary_matches_its_control(self):
        sensors = [("GPU Fan 2", "Fan", 0), ("GPU Fan 1", "Fan", 1000),
                   ("GPU Fan 1", "Control", 40), ("GPU Fan 2", "Control", 60)]
        first = self.readings(sensors)
        second = self.readings(list(reversed(sensors)))
        self.assertEqual(first["gpu_fans"], second["gpu_fans"])
        self.assertEqual(first["gpu_fan"], 1000)
        self.assertEqual(first["gpu_fan_pct"], 40)
        self.assertEqual([f["rpm"] for f in first["gpu_fans"]], [1000, 0])

    def test_gpu_stall_is_detected_but_cpu_load_does_not_disable_zero_rpm(self):
        advisor = ThermalAdvisor()
        running = [dict(id="gpu/fan1", name="GPU Fan 1", rpm=1200)]
        stopped = [dict(running[0], rpm=0)]
        self.findings(advisor, sample(gpu_fans=running), 0)
        self.findings(advisor, sample(cpu_temp=80, gpu_fans=stopped), 1)
        self.assertFalse(any(f.key == "gpu/fan1" for f in self.findings(advisor, sample(cpu_temp=80, gpu_fans=stopped), 20)))
        hot = sample(gpu_hotspot_temp=95, gpu_fans=stopped)
        self.findings(advisor, hot, 21)
        self.assertTrue(any(f.key == "gpu/fan1" and f.severity == 2 for f in self.findings(advisor, hot, 31)))

    def test_cpu_fan_stall_uses_cpu_temperature(self):
        for name in ("CPU Fan", "Processor Fan"):
            advisor = ThermalAdvisor()
            fan = dict(id="cpu/fan", name=name, rpm=1000)
            self.findings(advisor, sample(fans=[fan]), 0)
            idle_cpu = sample(cpu_temp=35, gpu_hotspot_temp=95, fans=[dict(fan, rpm=0)])
            self.findings(advisor, idle_cpu, 1)
            self.assertFalse(any(f.key == "cpu/fan" for f in self.findings(advisor, idle_cpu, 20)), name)

    def test_stopped_second_gpu_fan_is_visible_red_and_triggers_sound(self):
        app = _update_ui_app()
        app.sensor_data = sample(gpu_hotspot_temp=95, gpu_fan=1000,
            gpu_fans=[dict(id="gpu/1", name="GPU Fan 1", rpm=1000), dict(id="gpu/2", name="GPU Fan 2", rpm=1100)])
        with mock.patch.object(overlay.time, "monotonic", return_value=0):
            app.update_ui()
        app.sensor_data["gpu_fans"][1]["rpm"] = 0
        for now in (1, 11):
            with mock.patch.object(overlay.time, "monotonic", return_value=now):
                app.update_ui()
        self.assertIn("2: 0 RPM", app.rows["gpu_fan"].options["text"])
        self.assertEqual(app.rows["gpu_fan"].options["fg"], "#f87171")
        app.alerts_enabled = True
        app._last_alert_time = 0
        app._ALERT_COOLDOWN = 60
        with mock.patch.object(overlay.threading, "Thread") as beep, mock.patch.object(overlay.time, "time", return_value=1000):
            overlay.OverlayApp._check_alerts(app, app.sensor_data)
            beep.assert_called_once()

    def test_different_gpu_does_not_inherit_optional_sensor_expectations(self):
        advisor = ThermalAdvisor()
        self.findings(advisor, sample(gpu_id="amd"), 0)
        findings = self.findings(advisor, sample(gpu_id="intel", gpu_hotspot_temp=None, gpu_memory_temp=None), 1)
        self.assertFalse(findings)

    def test_optional_temperature_loss_is_visible_only_after_detection(self):
        advisor = ThermalAdvisor()
        missing = sample(gpu_hotspot_temp=None, gpu_memory_temp=None)
        self.assertFalse(self.findings(advisor, missing, 0))
        self.findings(advisor, sample(), 1)
        findings = self.findings(advisor, missing, 2)
        self.assertEqual({f.key for f in findings}, {"missing:gpu_hotspot_temp", "missing:gpu_memory_temp"})

    def test_controller_error_panel_is_bounded_and_confirms_restore(self):
        app = _update_ui_app()
        app.health_label = _FakeLabel()
        app._case_fan_status = dict(state="error", reason="C:/very-long-private-path/" * 100, restore_confirmed=True)
        app._update_thermal_advice(sample())
        text = app.health_label.options["text"]
        self.assertLess(len(text), 250)
        self.assertIn("firmware", text.lower())
        self.assertIn("diagnostics", text.lower())

    def test_diagnostics_keep_all_unabridged_warning_and_controller_details(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.running = True
        app.root = mock.Mock()
        app._set_menu_label = mock.Mock()
        app._stop_event = threading.Event()
        app.health_messages = ["warning " + str(n) + "x" * 200 for n in range(5)]
        app._case_fan_status = dict(state="error", reason="complete original error", restore_confirmed=True)
        with mock.patch.object(overlay, "init_hardware_monitor", return_value=mock.Mock()), \
             mock.patch.object(overlay, "read_sensors", return_value={}), \
             mock.patch.object(overlay, "build_sensor_diagnostics", return_value="sensor dump"):
            app.copy_diagnostics()
            app._diagnostics_thread.join(3)
            self.assertFalse(app._diagnostics_thread.is_alive())
            app._poll_diagnostics()
        text = app.root.clipboard_append.call_args.args[0]
        for message in app.health_messages:
            self.assertIn(message, text)
        self.assertIn("complete original error", text)

    def test_hidden_findings_and_muted_sound_remain_discoverable(self):
        app = _update_ui_app()
        app.health_label = _FakeLabel()
        app._update_thermal_advice(sample())
        self.assertIn("Sound: OFF", app.health_label.options["text"])
        app._update_thermal_advice(sample(cpu_temp=95, gpu_temp=95, gpu_hotspot_temp=110, gpu_memory_temp=110, ram_pct=99))
        self.assertIn("2 more", app.health_label.options["text"])

    def test_required_airflow_input_loss_is_visible_on_first_sample(self):
        app = _update_ui_app()
        app.config["case_fans_enabled"] = True
        app.health_label = _FakeLabel()
        app._update_thermal_advice(sample(gpu_memory_temp=None))
        self.assertIn("VRAM temp", app.health_label.options["text"])
        self.assertNotIn("No thermal warnings", app.health_label.options["text"])

    def test_bad_fan_calibration_does_not_poison_future_config_saves(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(overlay, "CONFIG_PATH", os.path.join(directory, "config.json")):
            with open(overlay.CONFIG_PATH, "w") as stream:
                json.dump(dict(case_fan_full_rpm={"System Fan #1": float("nan")}, case_fans_enabled=False), stream)
            with self.assertLogs("HeatMap", level="WARNING"):
                config, warning = overlay.load_config_result()
            self.assertIn("case_fan_full_rpm", warning)
            self.assertNotIn("case_fan_full_rpm", config)
            self.assertTrue(overlay.save_config(config)[0])


if __name__ == "__main__":
    unittest.main()
