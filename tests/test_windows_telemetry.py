import ctypes
import math
import threading
import time
import unittest
from unittest import mock

import overlay
from windows_telemetry import WindowsTelemetry, thermal_zones, gpu_utilization
from test_ui_layout import layout_app, settle_layout, TkTestCase
from test_layout_audit import mapped_layout_app


class NativeTelemetryUiTests(TkTestCase):
    def test_mapped_temperatures_clock_and_disk_temperature_fit_with_long_model_at_supported_scales(self):
        for scaling in (1.333, 2.0, 2.666):
            with self.subTest(scaling=scaling), mapped_layout_app(scaling=scaling, windows_only=True) as (app, _area):
                now = time.monotonic()
                app.sensor_data = dict(overlay._empty_sensor_data(), cpu_load=25, cpu_clock=2100,
                    gpu_load=17.5, gpu_clock=280, cpu_temp=42.1, cpu_temp_label="SoC",
                    gpu_temp=36.7, gpu_core_temp=36.7, gpu_temp_label="Thermal zone",
                    _sensor_status="windows_only", thermal_zones=[{"name": "TZ0", "temp": 42.1}])
                app._sensor_sample_time = now
                app._volume_snapshot = (now, {"volumes": [{"name": "C:", "disk_number": 0,
                    "device_name": "KIOXIA " + "Very long storage model " * 8,
                    "total_bytes": 100 * 1024 ** 3, "free_bytes": 60 * 1024 ** 3,
                    "used_pct": 40, "temp": 34}], "volume_errors": []})
                app._update_ui_once()
                settle_layout(app)
                self.assertEqual(app.summary_labels["gpu"].cget("text"), "Temperature")
                self.assertEqual(app.rows["cpu_temp"].winfo_manager(), "pack")
                self.assertEqual(app.rows["gpu_temp"].winfo_manager(), "pack")
                self.assertIn("280", app.rows["gpu_clock"].cget("text"))
                self.assertEqual(app.groups["THERMAL ZONES"].winfo_manager(), "")
                self.assertEqual(app.status_label.winfo_manager(), "")
                self.assertEqual(app.health_label.winfo_manager(), "", app.health_messages)
                temperature = app.rows["disk_0"]
                self.assertEqual(temperature.cget("text"), "34°C")
                self.assertGreaterEqual(temperature.winfo_width(), temperature.winfo_reqwidth())
                self.assertNotIn("disk_0_capacity", app.rows)
                usage = app.rows["disk_0_usage"]
                self.assertGreaterEqual(usage.winfo_width(), usage.winfo_reqwidth())
                self.assertLessEqual(usage.winfo_rootx() + usage.winfo_width(),
                                     usage.master.winfo_rootx() + usage.master.winfo_width())
                names = [label for label in temperature.master.winfo_children()
                         if label not in (temperature, usage)]
                self.assertTrue(names[0].cget("text").endswith("…"))
                app.toggle_details()
                settle_layout(app)
                self.assertEqual(app.groups["THERMAL ZONES"].winfo_manager(), "pack")
                app._set_sensor_status(overlay.SENSOR_STATUS_STALE)
                self.assertEqual(app.status_label.winfo_manager(), "pack")
                app._set_health_panel(["Storage read failed"], 1)
                self.assertEqual(app.health_label.winfo_manager(), "pack")

    def test_arm_ui_shows_named_zones_and_gpu_load_without_relabeling_die_sensors(self):
        with layout_app(windows_only=True) as app:
            app.sensor_data = dict(overlay._empty_sensor_data(), cpu_load=25, cpu_clock=1800,
                                   gpu_load=17.5, _sensor_status="windows_only",
                                   thermal_zones=[{"name": "\\_SB.TZ0", "temp": 42.1},
                                                  {"name": "\\_SB.TZ6", "temp": 38.2}])
            app._sensor_sample_time = time.monotonic()
            app._update_ui_once()
            settle_layout(app)
            self.assertIn("42.1°C", app.rows["native_thermal"].cget("text"))
            self.assertIn("TZ0", app.rows["native_thermal"].cget("text"))
            self.assertIn("2 zones", app.rows["native_thermal_range"].cget("text"))
            self.assertIn("17.5%", app.rows["gpu_load"].cget("text"))
            self.assertEqual(app.rows["cpu_temp"].winfo_manager(), "")
            self.assertEqual(app.rows["gpu_temp"].winfo_manager(), "")
            self.assertEqual(app.groups["GPU"].winfo_manager(), "pack")
            self.assertIn("TZ6: 38.2", app.rows["native_thermal_details"].cget("text"))
            app.sensor_data["thermal_zones"] = []
            app._update_ui_once()
            self.assertEqual(app.rows["native_thermal"].cget("text"), "--")


class WindowsTelemetryTests(unittest.TestCase):
    def test_tenths_kelvin_preferred_and_inactive_sentinels_filtered(self):
        result = thermal_zones({"TZ0": 312, "TZ1": 233, "TZ2": 0, "TZ3": 310},
                               {"TZ0": 3125, "TZ1": 2330, "TZ2": 0})
        self.assertEqual(result, [{"name": "TZ0", "temp": 39.4}, {"name": "TZ3", "temp": 36.9}])

    def test_missing_and_nonfinite_precise_values_use_valid_standard_values(self):
        self.assertEqual(thermal_zones({"a": 300, "b": 301}, {"a": math.nan, "b": 0}),
                         [{"name": "a", "temp": 26.9}, {"name": "b", "temp": 27.9}])
        self.assertEqual(thermal_zones({"a": math.inf}, {"b": -1}), [])

    def test_gpu_adds_processes_on_one_engine_but_not_parallel_engines_or_adapters(self):
        samples = {
            "pid_1_luid_0x0_0x1_phys_0_eng_0_engtype_3D": 30,
            "pid_2_luid_0x0_0x1_phys_0_eng_0_engtype_3D": 40,
            "pid_1_luid_0x0_0x1_phys_0_eng_1_engtype_Copy": 60,
            "pid_2_luid_0x0_0x2_phys_0_eng_0_engtype_3D": 10,
            "unknown": 100,
        }
        self.assertEqual(gpu_utilization(samples, "luid_0x0_0x1"), 70)

    def test_gpu_missing_is_distinct_from_real_idle_zero(self):
        self.assertIsNone(gpu_utilization({}))
        name = "pid_1_luid_0x0_0x1_phys_0_eng_0_engtype_3D"
        self.assertEqual(gpu_utilization({name: 0}), 0)
        self.assertIsNone(gpu_utilization({name: math.nan}))

    def test_failed_collection_drops_previous_values(self):
        reader = WindowsTelemetry.__new__(WindowsTelemetry)
        reader.query = 1
        reader.api = mock.Mock()
        reader.api.PdhCollectQueryData.return_value = 1
        with mock.patch("windows_telemetry.sample_gpu", return_value={}):
            result = reader.sample()
        self.assertEqual(result["thermal_zones"], [])
        self.assertIsNone(result["gpu_load"])
        self.assertIsNone(result["cpu_clock"])

    def test_close_releases_query_exactly_once(self):
        reader = WindowsTelemetry.__new__(WindowsTelemetry)
        reader.query = 123
        reader.api = mock.Mock()
        reader.close()
        reader.close()
        reader.api.PdhCloseQuery.assert_called_once_with(123)

    def test_reject_oversized_native_array_before_allocation(self):
        reader = WindowsTelemetry.__new__(WindowsTelemetry)
        reader.api = mock.Mock()
        def read(_counter, _flags, size, _count, _buffer):
            ctypes.cast(size, ctypes.POINTER(ctypes.c_ulong))[0] = 8 * 1024 * 1024
            return 0x800007D2
        reader.api.PdhGetFormattedCounterArrayW.side_effect = read
        self.assertEqual(reader._read(1), {})

    def test_native_sampling_keeps_zones_distinct_from_cpu_gpu_die_sensors(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.running = True
        app.lock = threading.Lock()
        app._stop_event = threading.Event()
        reader = mock.Mock()
        reader.sample.return_value = {"thermal_zones": [{"name": "TZ0", "temp": 42.1}],
                                      "gpu_load": 23, "cpu_clock": 1800}
        with mock.patch.object(overlay, "WindowsTelemetry", return_value=reader), \
                mock.patch.object(app._stop_event, "wait", side_effect=lambda _: app._stop_event.set()):
            app.windows_sensor_loop()
        self.assertIsNone(app.sensor_data["cpu_temp"])
        self.assertIsNone(app.sensor_data["gpu_core_temp"])
        self.assertEqual(app.sensor_data["thermal_zones"][0]["temp"], 42.1)
        self.assertEqual(app.sensor_data["gpu_load"], 23)
        reader.close.assert_called_once_with()

    def test_native_failure_preserves_independent_cpu_and_ram_samples(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.running = True
        app.lock = threading.Lock()
        app._stop_event = threading.Event()
        with mock.patch.object(overlay, "WindowsTelemetry", side_effect=OSError("counters unavailable")), \
                mock.patch.object(app._stop_event, "wait", side_effect=lambda _: app._stop_event.set()):
            app.windows_sensor_loop()
        self.assertIsNotNone(app.sensor_data["cpu_load"])
        self.assertIsNotNone(app.sensor_data["ram_pct"])
        self.assertEqual(app.sensor_data["native_telemetry_error"], "counters unavailable")


if __name__ == "__main__":
    unittest.main()
