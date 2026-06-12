import json
import math
import os
import sys
import tempfile
import threading
import unittest
from types import ModuleType, SimpleNamespace
from unittest import mock

import overlay


class OverlayHelperTests(unittest.TestCase):
    def setUp(self):
        self._old_config_path = overlay.CONFIG_PATH
        self._tmpdir = tempfile.TemporaryDirectory()
        overlay.CONFIG_PATH = os.path.join(self._tmpdir.name, "overlay_config.json")

    def tearDown(self):
        overlay.CONFIG_PATH = self._old_config_path
        self._tmpdir.cleanup()

    def test_safe_round_rejects_invalid_values(self):
        self.assertIsNone(overlay._safe_round(None))
        self.assertIsNone(overlay._safe_round(math.nan))
        self.assertIsNone(overlay._safe_round(math.inf))
        self.assertIsNone(overlay._safe_round(-math.inf))
        self.assertEqual(overlay._safe_round(42.4), 42)
        self.assertEqual(overlay._safe_round(42.6), 43)

    def test_load_config_rejects_bool_numeric_fields(self):
        with open(overlay.CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "x": True,
                "y": False,
                "peek_enabled": False,
                "alerts_enabled": True,
                "details_enabled": False,
                "gpu_fan_max_rpm": True,
                "cpu_fan_max_rpm": False,
            }, f)

        with self.assertLogs("HeatMap", level="WARNING"):
            cfg = overlay.load_config()

        self.assertEqual(cfg["x"], 50)
        self.assertEqual(cfg["y"], 50)
        self.assertFalse(cfg["peek_enabled"])
        self.assertTrue(cfg["alerts_enabled"])
        self.assertFalse(cfg["details_enabled"])
        self.assertEqual(cfg["gpu_fan_max_rpm"], 2200)
        self.assertEqual(cfg["cpu_fan_max_rpm"], 1800)

    def test_load_config_result_warns_for_invalid_individual_fields(self):
        with open(overlay.CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "x": True,
                "y": 20.8,
                "peek_enabled": "yes",
                "details_enabled": "please",
                "alerts_enabled": False,
                "gpu_fan_max_rpm": -1,
                "cpu_fan_max_rpm": 2500.2,
            }, f)

        with self.assertLogs("HeatMap", level="WARNING"):
            cfg, message = overlay.load_config_result()

        self.assertEqual(cfg, {
            "x": 50,
            "y": 20,
            "peek_enabled": True,
            "details_enabled": False,
            "alerts_enabled": False,
            "gpu_fan_max_rpm": 2200,
            "cpu_fan_max_rpm": 2500,
        })
        self.assertEqual(message, "Adjusted invalid config fields: x, peek_enabled, details_enabled, gpu_fan_max_rpm")

    def test_save_config_writes_atomically_loadable_json(self):
        cfg = {
            "x": 123,
            "y": 456,
            "peek_enabled": False,
            "alerts_enabled": True,
            "details_enabled": True,
            "gpu_fan_max_rpm": 3333,
            "cpu_fan_max_rpm": 2222,
        }

        ok, message = overlay.save_config(cfg)

        self.assertTrue(ok)
        self.assertEqual(message, "Config saved")
        self.assertFalse(os.path.exists(f"{overlay.CONFIG_PATH}.tmp"))
        self.assertEqual(overlay.load_config(), cfg)

    def test_load_config_result_valid_config_has_no_warning(self):
        cfg = {
            "x": 123,
            "y": 456,
            "peek_enabled": False,
            "alerts_enabled": True,
            "details_enabled": True,
            "gpu_fan_max_rpm": 3333,
            "cpu_fan_max_rpm": 2222,
        }
        with open(overlay.CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f)

        loaded, message = overlay.load_config_result()

        self.assertEqual(loaded, cfg)
        self.assertIsNone(message)

    def test_load_config_result_missing_file_is_not_warning(self):
        cfg, message = overlay.load_config_result()

        self.assertEqual(cfg, {
            "x": 50,
            "y": 50,
            "peek_enabled": True,
            "alerts_enabled": True,
            "details_enabled": False,
            "gpu_fan_max_rpm": 2200,
            "cpu_fan_max_rpm": 1800,
        })
        self.assertIsNone(message)

    def test_load_config_result_invalid_json_returns_warning(self):
        with open(overlay.CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write("{broken")

        with self.assertLogs("HeatMap", level="WARNING"):
            cfg, message = overlay.load_config_result()

        self.assertEqual(cfg["x"], 50)
        self.assertIn("Failed to load config", message)

    def test_load_config_result_missing_new_fields_uses_defaults_without_warning(self):
        with open(overlay.CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "x": 123,
                "y": 456,
                "peek_enabled": False,
                "alerts_enabled": True,
                "gpu_fan_max_rpm": 3333,
                "cpu_fan_max_rpm": 2222,
            }, f)

        cfg, message = overlay.load_config_result()

        self.assertEqual(cfg, {
            "x": 123,
            "y": 456,
            "peek_enabled": False,
            "alerts_enabled": True,
            "details_enabled": False,
            "gpu_fan_max_rpm": 3333,
            "cpu_fan_max_rpm": 2222,
        })
        self.assertIsNone(message)

    def test_load_config_result_non_dict_returns_warning(self):
        with open(overlay.CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(["not", "dict"], f)

        with self.assertLogs("HeatMap", level="WARNING"):
            cfg, message = overlay.load_config_result()

        self.assertEqual(cfg["x"], 50)
        self.assertEqual(message, "Invalid config format")

    def test_clamp_overlay_position_keeps_fully_visible_position(self):
        self.assertEqual(
            overlay._clamp_overlay_position(
                100, 80,
                window_width=220, window_height=260,
                virt_x=0, virt_y=0, virt_w=1920, virt_h=1080,
            ),
            (100, 80),
        )

    def test_clamp_overlay_position_clamps_right_and_bottom_edges(self):
        self.assertEqual(
            overlay._clamp_overlay_position(
                1850, 1000,
                window_width=220, window_height=160,
                virt_x=0, virt_y=0, virt_w=1920, virt_h=1080,
            ),
            (1700, 920),
        )

    def test_clamp_overlay_position_handles_negative_virtual_screen(self):
        self.assertEqual(
            overlay._clamp_overlay_position(
                -2500, -100,
                window_width=200, window_height=120,
                virt_x=-1920, virt_y=0, virt_w=3840, virt_h=1080,
            ),
            (-1920, 0),
        )

    def test_clamp_overlay_position_handles_window_larger_than_screen(self):
        self.assertEqual(
            overlay._clamp_overlay_position(
                300, 200,
                window_width=2400, window_height=1200,
                virt_x=0, virt_y=0, virt_w=1920, virt_h=1080,
            ),
            (0, 0),
        )

    def test_load_config_result_read_failure_returns_warning(self):
        open(overlay.CONFIG_PATH, "w", encoding="utf-8").close()

        with (
            mock.patch("builtins.open", side_effect=OSError("denied")),
            self.assertLogs("HeatMap", level="WARNING"),
        ):
            cfg, message = overlay.load_config_result()

        self.assertEqual(cfg["x"], 50)
        self.assertIn("denied", message)

    def test_save_config_failure_returns_message_and_removes_tmp(self):
        with (
            mock.patch("builtins.open", side_effect=OSError("denied")),
            self.assertLogs("HeatMap", level="WARNING"),
        ):
            ok, message = overlay.save_config({"x": 1})

        self.assertFalse(ok)
        self.assertIn("denied", message)
        self.assertFalse(os.path.exists(f"{overlay.CONFIG_PATH}.tmp"))

    def test_disk_temperature_color_matches_critical_alert_threshold(self):
        self.assertEqual(overlay.disk_temp_color(None), "#888888")
        self.assertEqual(overlay.disk_temp_color(44), "#4ade80")
        self.assertEqual(overlay.disk_temp_color(45), "#facc15")
        self.assertEqual(overlay.disk_temp_color(54), "#facc15")
        self.assertEqual(overlay.disk_temp_color(55), "#f87171")

    def test_runtime_dll_errors_uses_manifest_verifier_and_allows_extra_dlls(self):
        with mock.patch("setup.verify_lib_manifest", return_value=(False, ["hash mismatch"])) as verify:
            self.assertEqual(
                overlay._runtime_dll_errors(lib_dir="lib-dir", manifest_path="manifest.json"),
                ["hash mismatch"],
            )

        verify.assert_called_once_with(
            lib_dir="lib-dir",
            manifest_path="manifest.json",
            allow_extra_dlls=True,
        )

    def test_main_does_not_kill_previous_instance_when_runtime_dlls_are_invalid(self):
        with (
            mock.patch.object(overlay, "_runtime_dll_errors", return_value=["missing DLL: lib/System.Memory.dll"]),
            mock.patch.object(overlay, "kill_previous_instances") as kill_previous,
            mock.patch.object(overlay, "_show_error_message") as show_error,
        ):
            with self.assertRaises(SystemExit) as raised:
                overlay.main()

        self.assertEqual(raised.exception.code, 1)
        kill_previous.assert_not_called()
        show_error.assert_called_once()
        self.assertIn("System.Memory.dll", show_error.call_args.args[1])
        self.assertIn("python setup.py --verify", show_error.call_args.args[1])

    def test_is_same_script_invocation_matches_absolute_and_relative_paths(self):
        script_path = os.path.join(self._tmpdir.name, "overlay.py")

        self.assertTrue(overlay.is_same_script_invocation(script_path, script_path))
        self.assertTrue(overlay.is_same_script_invocation(script_path, f'"{script_path}"'))
        self.assertTrue(overlay.is_same_script_invocation(script_path, "overlay.py", self._tmpdir.name))
        self.assertFalse(overlay.is_same_script_invocation(script_path, "overlay.py"))
        self.assertFalse(overlay.is_same_script_invocation(script_path, "overlay.py", os.path.dirname(self._tmpdir.name)))
        self.assertFalse(overlay.is_same_script_invocation(script_path, "other.py", self._tmpdir.name))

    def test_sensor_error_update_shows_error_state_and_clears_disk_rows(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.running = True
        app.lock = threading.Lock()
        app.sensor_data = {"error": "boom"}
        app.root = _FakeRoot()
        disk_child = _FakeChild()
        app.disk_frame = _FakeFrame([disk_child])
        app.disk_labels = ["disk_0"]
        app._last_disk_names = ["C:"]
        app.rows = {
            "cpu_temp": _FakeLabel(),
            "cpu_load": _FakeLabel(),
            "disk_0": _FakeLabel(),
            "disk_0_usage": _FakeLabel(),
        }

        app.update_ui()

        self.assertTrue(disk_child.destroyed)
        self.assertEqual(app.disk_labels, [])
        self.assertEqual(app._last_disk_names, [])
        self.assertNotIn("disk_0", app.rows)
        self.assertNotIn("disk_0_usage", app.rows)
        self.assertEqual(app.rows["cpu_temp"].options, {"text": "ERR", "fg": "#f87171"})
        self.assertEqual(app.rows["cpu_load"].options, {"text": "ERR", "fg": "#f87171"})
        self.assertEqual(app.root.after_calls, [(2000, app.update_ui)])

    def test_get_log_path_prefers_local_appdata(self):
        env = {"LOCALAPPDATA": os.path.join(self._tmpdir.name, "LocalAppData")}

        self.assertEqual(
            overlay._get_log_path(env=env, app_dir=self._tmpdir.name),
            os.path.join(env["LOCALAPPDATA"], "HeatMap", "HeatMap.log"),
        )

    def test_get_log_path_falls_back_to_app_dir(self):
        self.assertEqual(
            overlay._get_log_path(env={}, app_dir=self._tmpdir.name),
            os.path.join(self._tmpdir.name, "HeatMap.log"),
        )

    def test_format_autostart_task_run_quotes_paths(self):
        self.assertEqual(
            overlay._format_autostart_task_run(r"C:\Python313\pythonw.exe", r"C:\Heat Map\overlay.py"),
            r'"C:\Python313\pythonw.exe" "C:\Heat Map\overlay.py"',
        )

    def test_parse_autostart_task_xml_accepts_real_schtasks_shape(self):
        xml = _task_xml(r'"C:\Python313\pythonw.exe"', r'"C:\Users\Dima\Documents\GitHub\HeatMap\overlay.py"')

        command, arguments = overlay._parse_autostart_task_xml(xml)

        self.assertEqual(command, r'"C:\Python313\pythonw.exe"')
        self.assertEqual(arguments, r'"C:\Users\Dima\Documents\GitHub\HeatMap\overlay.py"')

    def test_parse_autostart_task_xml_tolerates_wrong_utf16_declaration(self):
        xml = _task_xml(r'"C:\Python313\pythonw.exe"', r'"C:\HeatMap\overlay.py"')
        xml_bytes = xml.encode("cp1252")

        command, arguments = overlay._parse_autostart_task_xml(xml_bytes)

        self.assertEqual(command, r'"C:\Python313\pythonw.exe"')
        self.assertEqual(arguments, r'"C:\HeatMap\overlay.py"')

    def test_parse_autostart_task_xml_accepts_real_utf16_bytes(self):
        xml = _task_xml(r'"C:\Python313\pythonw.exe"', r'"C:\HeatMap\overlay.py"')

        command, arguments = overlay._parse_autostart_task_xml(xml.encode("utf-16"))

        self.assertEqual(command, r'"C:\Python313\pythonw.exe"')
        self.assertEqual(arguments, r'"C:\HeatMap\overlay.py"')

    def test_autostart_enabled_rejects_stale_task_paths(self):
        xml = _task_xml(r'"C:\Python313\pythonw.exe"', r'"C:\Old\overlay.py"')
        result = _completed(returncode=0, stdout=xml.encode("cp1252"))
        with (
            mock.patch.object(overlay, "_run_schtasks", return_value=(result, None)),
            mock.patch.object(overlay, "get_pythonw_path", return_value=r"C:\Python313\pythonw.exe"),
            mock.patch.object(overlay, "SCRIPT_PATH", r"C:\HeatMap\overlay.py"),
        ):
            self.assertFalse(overlay.is_autostart_enabled())

    def test_autostart_enabled_rejects_stale_python_path(self):
        xml = _task_xml(r'"C:\OldPython\pythonw.exe"', r'"C:\HeatMap\overlay.py"')
        result = _completed(returncode=0, stdout=xml.encode("cp1252"))
        with (
            mock.patch.object(overlay, "_run_schtasks", return_value=(result, None)),
            mock.patch.object(overlay, "get_pythonw_path", return_value=r"C:\Python313\pythonw.exe"),
            mock.patch.object(overlay, "SCRIPT_PATH", r"C:\HeatMap\overlay.py"),
        ):
            self.assertFalse(overlay.is_autostart_enabled())

    def test_autostart_enabled_accepts_matching_task_paths(self):
        xml = _task_xml(r'"C:\Python313\pythonw.exe"', r'"C:\HeatMap\overlay.py"')
        result = _completed(returncode=0, stdout=xml.encode("cp1252"))
        with (
            mock.patch.object(overlay, "_run_schtasks", return_value=(result, None)),
            mock.patch.object(overlay, "get_pythonw_path", return_value=r"C:\Python313\pythonw.exe"),
            mock.patch.object(overlay, "SCRIPT_PATH", r"C:\HeatMap\overlay.py"),
        ):
            self.assertTrue(overlay.is_autostart_enabled())

    def test_autostart_enabled_returns_false_for_malformed_xml(self):
        result = _completed(returncode=0, stdout=b"<Task><Actions>")
        with mock.patch.object(overlay, "_run_schtasks", return_value=(result, None)):
            self.assertFalse(overlay.is_autostart_enabled())

    def test_enable_autostart_keeps_legacy_registry_on_create_failure(self):
        result = _completed(returncode=1, stderr=b"create failed")
        with (
            mock.patch.object(overlay, "_run_schtasks", return_value=(result, None)),
            mock.patch.object(overlay, "_delete_legacy_autostart_value") as delete_legacy,
        ):
            ok, message = overlay.enable_autostart()

        self.assertFalse(ok)
        self.assertIn("create failed", message)
        delete_legacy.assert_not_called()

    def test_enable_autostart_deletes_legacy_registry_after_successful_create(self):
        result = _completed(returncode=0)
        with (
            mock.patch.object(overlay, "_run_schtasks", return_value=(result, None)) as run_schtasks,
            mock.patch.object(overlay, "_delete_legacy_autostart_value", return_value=(True, "removed")) as delete_legacy,
        ):
            ok, message = overlay.enable_autostart()

        self.assertTrue(ok)
        self.assertEqual(message, "Autostart enabled")
        args = run_schtasks.call_args.args[0]
        self.assertIn("/DELAY", args)
        self.assertEqual(args[args.index("/DELAY") + 1], "0000:30")
        delete_legacy.assert_called_once()

    def test_toggle_autostart_failed_enable_shows_error_and_marks_menu(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.menu_labels = []
        app._set_menu_label = lambda key, label: app.menu_labels.append((key, label))
        with (
            mock.patch.object(overlay, "is_autostart_enabled", return_value=False),
            mock.patch.object(overlay, "enable_autostart", return_value=(False, "create failed")),
            mock.patch.object(overlay, "_show_error_message") as show_error,
        ):
            app.toggle_autostart()

        self.assertEqual(app.menu_labels, [("autostart", "Autostart: ERROR")])
        show_error.assert_called_once()
        self.assertIn("create failed", show_error.call_args.args[1])

    def test_toggle_autostart_success_updates_menu_from_validated_state(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.menu_labels = []
        app._set_menu_label = lambda key, label: app.menu_labels.append((key, label))
        with (
            mock.patch.object(overlay, "is_autostart_enabled", side_effect=[False, True]),
            mock.patch.object(overlay, "enable_autostart", return_value=(True, "Autostart enabled")),
        ):
            app.toggle_autostart()

        self.assertEqual(app.menu_labels, [("autostart", "Autostart: ON")])

    def test_read_sensors_without_computer_returns_psutil_fallback(self):
        with (
            mock.patch.object(overlay.psutil, "cpu_percent", return_value=42.4),
            mock.patch.object(overlay.psutil, "virtual_memory", return_value=_memory(percent=63.6, used_gb=2, total_gb=8)),
        ):
            data = overlay.read_sensors(None)

        self.assertEqual(data["cpu_load"], 42)
        self.assertEqual(data["ram_pct"], 64)
        self.assertEqual(data["ram_used_gb"], 2.0)
        self.assertEqual(data["ram_total_gb"], 8.0)
        self.assertEqual(data["disks"], [])
        self.assertEqual(data[overlay.SENSOR_STATUS_KEY], overlay.SENSOR_STATUS_PSUTIL_FALLBACK)

    def test_read_sensors_skips_failing_hardware_and_keeps_partial_sample(self):
        modules, HardwareType, SensorType = _fake_lhm_modules()
        bad_cpu = _FakeHardware("Bad CPU", HardwareType.Cpu, update_error=RuntimeError("driver timeout"))
        memory = _FakeHardware(
            "Memory",
            HardwareType.Memory,
            sensors=[_FakeSensor("Memory", SensorType.Load, 77)],
        )
        computer = SimpleNamespace(Hardware=[bad_cpu, memory])

        with (
            mock.patch.dict(sys.modules, modules),
            mock.patch.object(overlay.psutil, "cpu_percent", return_value=22),
            mock.patch.object(overlay.psutil, "virtual_memory", return_value=_memory(percent=55, used_gb=3, total_gb=16)),
            self.assertLogs("HeatMap", level="WARNING") as logs,
        ):
            data = overlay.read_sensors(computer)

        self.assertEqual(data["cpu_load"], 22)
        self.assertEqual(data["ram_pct"], 77)
        self.assertEqual(data["ram_used_gb"], 3.0)
        self.assertEqual(data["ram_total_gb"], 16.0)
        self.assertEqual(data[overlay.SENSOR_STATUS_KEY], overlay.SENSOR_STATUS_PARTIAL)
        self.assertTrue(any("Skipping hardware block" in message and "Bad CPU" in message for message in logs.output))

    def test_read_sensors_handles_hardware_enumeration_failure(self):
        modules, _, _ = _fake_lhm_modules()
        computer = _FailingHardwareComputer(RuntimeError("enumeration failed"))

        with (
            mock.patch.dict(sys.modules, modules),
            mock.patch.object(overlay.psutil, "cpu_percent", return_value=19),
            mock.patch.object(overlay.psutil, "virtual_memory", return_value=_memory(percent=31, used_gb=4, total_gb=32)),
            self.assertLogs("HeatMap", level="WARNING") as logs,
        ):
            data = overlay.read_sensors(computer)

        self.assertEqual(data["cpu_load"], 19)
        self.assertEqual(data["ram_pct"], 31)
        self.assertEqual(data["ram_used_gb"], 4.0)
        self.assertEqual(data["ram_total_gb"], 32.0)
        self.assertEqual(data["disks"], [])
        self.assertEqual(data[overlay.SENSOR_STATUS_KEY], overlay.SENSOR_STATUS_PSUTIL_FALLBACK)
        self.assertTrue(any("Failed to enumerate" in message for message in logs.output))

    def test_read_sensors_lhm_import_failure_marks_psutil_fallback(self):
        computer = SimpleNamespace(Hardware=[])

        with (
            mock.patch.dict(sys.modules, {"LibreHardwareMonitor.Hardware": None}),
            mock.patch.object(overlay.psutil, "cpu_percent", return_value=15),
            mock.patch.object(overlay.psutil, "virtual_memory", return_value=_memory(percent=25, used_gb=2, total_gb=4)),
            self.assertLogs("HeatMap", level="WARNING"),
        ):
            data = overlay.read_sensors(computer)

        self.assertEqual(data["cpu_load"], 15)
        self.assertEqual(data["ram_pct"], 25)
        self.assertEqual(data[overlay.SENSOR_STATUS_KEY], overlay.SENSOR_STATUS_PSUTIL_FALLBACK)

    def test_read_sensors_storage_skip_update_reads_cached_values(self):
        modules, HardwareType, SensorType = _fake_lhm_modules()
        storage = _FakeHardware(
            "Samsung SSD 980",
            HardwareType.Storage,
            sensors=[
                _FakeSensor("Temperature", SensorType.Temperature, 41),
                _FakeSensor("Used Space", SensorType.Load, 68),
            ],
        )
        computer = SimpleNamespace(Hardware=[storage])

        with (
            mock.patch.dict(sys.modules, modules),
            mock.patch.object(overlay.psutil, "cpu_percent", return_value=11),
            mock.patch.object(overlay.psutil, "virtual_memory", return_value=_memory(percent=22, used_gb=5, total_gb=10)),
        ):
            data = overlay.read_sensors(computer, update_storage=False)

        self.assertEqual(storage.update_calls, 0)
        self.assertEqual(data["disks"], [{"name": "980", "temp": 41, "used_pct": 68}])
        self.assertEqual(data["cpu_load"], 11)
        self.assertEqual(data["ram_pct"], 22)

    def test_read_sensors_storage_reads_life_level_when_available(self):
        modules, HardwareType, SensorType = _fake_lhm_modules()
        storage = _FakeHardware(
            "Samsung SSD 980 PRO",
            HardwareType.Storage,
            sensors=[
                _FakeSensor("Temperature", SensorType.Temperature, 41),
                _FakeSensor("Used Space", SensorType.Load, 68),
                _FakeSensor("Life", SensorType.Level, 77),
            ],
        )
        computer = SimpleNamespace(Hardware=[storage])

        with (
            mock.patch.dict(sys.modules, modules),
            mock.patch.object(overlay.psutil, "cpu_percent", return_value=11),
            mock.patch.object(overlay.psutil, "virtual_memory", return_value=_memory(percent=22, used_gb=5, total_gb=10)),
        ):
            data = overlay.read_sensors(computer)

        self.assertEqual(data["disks"], [{"name": "980 PRO", "temp": 41, "used_pct": 68, "life_pct": 77}])

    def test_read_sensors_gpu_vram_fan_and_clock_parsing(self):
        modules, HardwareType, SensorType = _fake_lhm_modules()
        gpu = _FakeHardware(
            "NVIDIA GPU",
            HardwareType.GpuNvidia,
            sensors=[
                _FakeSensor("GPU Core", SensorType.Temperature, 62),
                _FakeSensor("GPU Core", SensorType.Load, 71),
                _FakeSensor("GPU Core", SensorType.Clock, 1845),
                _FakeSensor("GPU Fan", SensorType.Fan, 1420),
                _FakeSensor("GPU Fan", SensorType.Control, 57),
                _FakeSensor("GPU Memory Used", SensorType.SmallData, 6144),
                _FakeSensor("GPU Memory Total", SensorType.SmallData, 12288),
            ],
        )
        computer = SimpleNamespace(Hardware=[gpu])

        with (
            mock.patch.dict(sys.modules, modules),
            mock.patch.object(overlay.psutil, "cpu_percent", return_value=10),
            mock.patch.object(overlay.psutil, "virtual_memory", return_value=_memory(percent=20, used_gb=2, total_gb=8)),
        ):
            data = overlay.read_sensors(computer)

        self.assertEqual(data["gpu_temp"], 62)
        self.assertEqual(data["gpu_load"], 71)
        self.assertEqual(data["gpu_clock"], 1845)
        self.assertEqual(data["gpu_fan"], 1420)
        self.assertEqual(data["gpu_fan_pct"], 57)
        self.assertEqual(data["gpu_vram_pct"], 50)
        self.assertEqual(data["gpu_vram_used_gb"], 6.0)
        self.assertEqual(data["gpu_vram_total_gb"], 12.0)
        self.assertNotIn(overlay.SENSOR_STATUS_KEY, data)

    def test_read_sensors_accepts_common_gpu_memory_and_ram_name_variants(self):
        modules, HardwareType, SensorType = _fake_lhm_modules()
        gpu = _FakeHardware(
            "AMD GPU",
            HardwareType.GpuAmd,
            sensors=[
                _FakeSensor("GPU D3D", SensorType.Load, 73),
                _FakeSensor("Memory Used", SensorType.SmallData, 8192),
                _FakeSensor("Memory Total", SensorType.SmallData, 16384),
            ],
        )
        memory = _FakeHardware(
            "Memory",
            HardwareType.Memory,
            sensors=[_FakeSensor("Memory Load", SensorType.Load, 77)],
        )
        computer = SimpleNamespace(Hardware=[gpu, memory])

        with (
            mock.patch.dict(sys.modules, modules),
            mock.patch.object(overlay.psutil, "cpu_percent", return_value=10),
            mock.patch.object(overlay.psutil, "virtual_memory", return_value=_memory(percent=22, used_gb=2, total_gb=8)),
        ):
            data = overlay.read_sensors(computer)

        self.assertEqual(data["gpu_load"], 73)
        self.assertEqual(data["gpu_vram_pct"], 50)
        self.assertEqual(data["gpu_vram_used_gb"], 8.0)
        self.assertEqual(data["gpu_vram_total_gb"], 16.0)
        self.assertEqual(data["ram_pct"], 77)

    def test_read_sensors_marks_empty_gpu_hardware_for_reinit(self):
        modules, HardwareType, _SensorType = _fake_lhm_modules()
        gpu = _FakeHardware("AMD Radeon RX 7900 XT", HardwareType.GpuAmd)
        computer = SimpleNamespace(Hardware=[gpu])

        with (
            mock.patch.dict(sys.modules, modules),
            mock.patch.object(overlay.psutil, "cpu_percent", return_value=10),
            mock.patch.object(overlay.psutil, "virtual_memory", return_value=_memory(percent=20, used_gb=2, total_gb=8)),
        ):
            data = overlay.read_sensors(computer)

        self.assertEqual(data[overlay.SENSOR_STATUS_KEY], overlay.SENSOR_STATUS_PARTIAL)
        self.assertTrue(data[overlay.SENSOR_REINIT_KEY])

    def test_read_sensors_ignores_zero_cpu_clocks(self):
        modules, HardwareType, SensorType = _fake_lhm_modules()
        cpu = _FakeHardware(
            "CPU",
            HardwareType.Cpu,
            sensors=[
                _FakeSensor("CPU Total", SensorType.Load, 35),
                _FakeSensor("Cores (Average)", SensorType.Clock, 0.0),
                _FakeSensor("Core #1", SensorType.Clock, None),
            ],
        )
        computer = SimpleNamespace(Hardware=[cpu])

        with (
            mock.patch.dict(sys.modules, modules),
            mock.patch.object(overlay.psutil, "virtual_memory", return_value=_memory(percent=44, used_gb=6, total_gb=12)),
        ):
            data = overlay.read_sensors(computer)

        self.assertIsNone(data["cpu_clock"])
        self.assertEqual(data["cpu_load"], 35)

    def test_read_sensors_skips_intel_igpu_before_update_when_discrete_gpu_exists(self):
        modules, HardwareType, SensorType = _fake_lhm_modules()
        discrete_gpu = _FakeHardware(
            "NVIDIA GPU",
            HardwareType.GpuNvidia,
            sensors=[_FakeSensor("GPU Core", SensorType.Temperature, 60)],
        )
        intel_gpu = _FakeHardware("Intel GPU", HardwareType.GpuIntel, update_error=RuntimeError("should skip"))
        computer = SimpleNamespace(Hardware=[discrete_gpu, intel_gpu])

        with (
            mock.patch.dict(sys.modules, modules),
            mock.patch.object(overlay.psutil, "cpu_percent", return_value=10),
            mock.patch.object(overlay.psutil, "virtual_memory", return_value=_memory(percent=20, used_gb=2, total_gb=8)),
        ):
            data = overlay.read_sensors(computer)

        self.assertEqual(data["gpu_temp"], 60)
        self.assertEqual(intel_gpu.update_calls, 0)
        self.assertNotIn(overlay.SENSOR_STATUS_KEY, data)

    def test_read_sensors_reads_intel_gpu_when_discrete_gpu_has_no_temperature(self):
        modules, HardwareType, SensorType = _fake_lhm_modules()
        discrete_gpu = _FakeHardware(
            "NVIDIA GPU",
            HardwareType.GpuNvidia,
            sensors=[_FakeSensor("GPU Core", SensorType.Load, 80)],
        )
        intel_gpu = _FakeHardware(
            "Intel GPU",
            HardwareType.GpuIntel,
            sensors=[_FakeSensor("GPU Core", SensorType.Temperature, 45)],
        )
        computer = SimpleNamespace(Hardware=[discrete_gpu, intel_gpu])

        with (
            mock.patch.dict(sys.modules, modules),
            mock.patch.object(overlay.psutil, "cpu_percent", return_value=10),
            mock.patch.object(overlay.psutil, "virtual_memory", return_value=_memory(percent=20, used_gb=2, total_gb=8)),
        ):
            data = overlay.read_sensors(computer)

        self.assertEqual(data["gpu_load"], 80)
        self.assertEqual(data["gpu_temp"], 45)
        self.assertEqual(intel_gpu.update_calls, 1)

    def test_read_sensors_cpu_fan_control_priority(self):
        modules, HardwareType, SensorType = _fake_lhm_modules()
        motherboard = _FakeHardware(
            "Motherboard",
            HardwareType.Motherboard,
            sub_hardware=[
                _FakeHardware(
                    "Controller",
                    "Controller",
                    sensors=[
                        _FakeSensor("CPU Fan", SensorType.Fan, 1300),
                        _FakeSensor("Case #1", SensorType.Control, 40),
                        _FakeSensor("CPU", SensorType.Control, 55),
                    ],
                ),
            ],
        )
        computer = SimpleNamespace(Hardware=[motherboard])

        with (
            mock.patch.dict(sys.modules, modules),
            mock.patch.object(overlay.psutil, "cpu_percent", return_value=10),
            mock.patch.object(overlay.psutil, "virtual_memory", return_value=_memory(percent=20, used_gb=2, total_gb=8)),
        ):
            data = overlay.read_sensors(computer)

        self.assertEqual(data["cpu_fan"], 1300)
        self.assertEqual(data["cpu_fan_pct"], 55)

    def test_read_sensors_collects_motherboard_temperatures(self):
        modules, HardwareType, SensorType = _fake_lhm_modules()
        motherboard = _FakeHardware(
            "Motherboard",
            HardwareType.Motherboard,
            sub_hardware=[
                _FakeHardware(
                    "Controller",
                    "Controller",
                    sensors=[
                        _FakeSensor("VRM MOS", SensorType.Temperature, 34),
                        _FakeSensor("Chipset", SensorType.Temperature, 30),
                        _FakeSensor("System #1", SensorType.Temperature, 27),
                    ],
                ),
            ],
        )
        computer = SimpleNamespace(Hardware=[motherboard])

        with (
            mock.patch.dict(sys.modules, modules),
            mock.patch.object(overlay.psutil, "cpu_percent", return_value=10),
            mock.patch.object(overlay.psutil, "virtual_memory", return_value=_memory(percent=20, used_gb=2, total_gb=8)),
        ):
            data = overlay.read_sensors(computer)

        self.assertEqual(data["motherboard_temps"], [
            {"name": "VRM MOS", "temp": 34},
            {"name": "Chipset", "temp": 30},
            {"name": "System #1", "temp": 27},
        ])

    def test_read_sensors_cpu_fan_control_falls_back_to_matching_number_only(self):
        modules, HardwareType, SensorType = _fake_lhm_modules()
        motherboard_hash_one = _FakeHardware(
            "Motherboard",
            HardwareType.Motherboard,
            sub_hardware=[
                _FakeHardware(
                    "Controller",
                    "Controller",
                    sensors=[
                        _FakeSensor("CPU Fan", SensorType.Fan, 1300),
                        _FakeSensor("Fan #1", SensorType.Control, 42),
                        _FakeSensor("Fan #2", SensorType.Control, 66),
                    ],
                ),
            ],
        )
        motherboard_first = _FakeHardware(
            "Motherboard",
            HardwareType.Motherboard,
            sub_hardware=[
                _FakeHardware(
                    "Controller",
                    "Controller",
                    sensors=[
                        _FakeSensor("CPU Fan", SensorType.Fan, 1300),
                        _FakeSensor("Pump", SensorType.Control, 37),
                    ],
                ),
            ],
        )

        with (
            mock.patch.dict(sys.modules, modules),
            mock.patch.object(overlay.psutil, "cpu_percent", return_value=10),
            mock.patch.object(overlay.psutil, "virtual_memory", return_value=_memory(percent=20, used_gb=2, total_gb=8)),
        ):
            hash_one_data = overlay.read_sensors(SimpleNamespace(Hardware=[motherboard_hash_one]))
            first_data = overlay.read_sensors(SimpleNamespace(Hardware=[motherboard_first]))

        self.assertEqual(hash_one_data["cpu_fan_pct"], 42)
        self.assertIsNone(first_data["cpu_fan_pct"])

    def test_read_sensors_cpu_temperature_from_subhardware(self):
        modules, HardwareType, SensorType = _fake_lhm_modules()
        cpu = _FakeHardware(
            "CPU",
            HardwareType.Cpu,
            sensors=[_FakeSensor("CPU Total", SensorType.Load, 35)],
            sub_hardware=[
                _FakeHardware(
                    "CPU DTS",
                    "SensorController",
                    sensors=[_FakeSensor("CPU Package", SensorType.Temperature, 52)],
                ),
            ],
        )
        computer = SimpleNamespace(Hardware=[cpu])

        with (
            mock.patch.dict(sys.modules, modules),
            mock.patch.object(overlay.psutil, "virtual_memory", return_value=_memory(percent=44, used_gb=6, total_gb=12)),
        ):
            data = overlay.read_sensors(computer)

        self.assertEqual(data["cpu_temp"], 52)
        self.assertEqual(data["cpu_load"], 35)

    def test_read_sensors_cpu_fan_falls_back_to_numbered_motherboard_fan(self):
        modules, HardwareType, SensorType = _fake_lhm_modules()
        motherboard = _FakeHardware(
            "Motherboard",
            HardwareType.Motherboard,
            sensors=[
                _FakeSensor("Fan #1", SensorType.Fan, 1250),
                _FakeSensor("Fan Control #1", SensorType.Control, 48),
            ],
        )
        computer = SimpleNamespace(Hardware=[motherboard])

        with (
            mock.patch.dict(sys.modules, modules),
            mock.patch.object(overlay.psutil, "cpu_percent", return_value=10),
            mock.patch.object(overlay.psutil, "virtual_memory", return_value=_memory(percent=20, used_gb=2, total_gb=8)),
        ):
            data = overlay.read_sensors(computer)

        self.assertEqual(data["cpu_fan"], 1250)
        self.assertEqual(data["cpu_fan_pct"], 48)

    def test_read_sensors_logs_and_skips_sensor_value_failure(self):
        modules, HardwareType, SensorType = _fake_lhm_modules()
        bad_gpu = _FakeHardware(
            "Bad GPU",
            HardwareType.GpuNvidia,
            sensors=[_FakeSensor("GPU Core", SensorType.Temperature, RuntimeError("bad value"))],
        )
        cpu = _FakeHardware(
            "CPU",
            HardwareType.Cpu,
            sensors=[_FakeSensor("CPU Total", SensorType.Load, 35)],
        )
        computer = SimpleNamespace(Hardware=[bad_gpu, cpu])

        with (
            mock.patch.dict(sys.modules, modules),
            mock.patch.object(overlay.psutil, "virtual_memory", return_value=_memory(percent=44, used_gb=6, total_gb=12)),
            self.assertLogs("HeatMap", level="WARNING") as logs,
        ):
            data = overlay.read_sensors(computer)

        self.assertIsNone(data["gpu_temp"])
        self.assertEqual(data["cpu_load"], 35)
        self.assertTrue(any("Bad GPU" in message for message in logs.output))

    def test_init_hardware_monitor_keeps_opened_computer_when_sanity_check_block_fails(self):
        modules, HardwareType, SensorType = _fake_lhm_modules()
        clr_module = ModuleType("clr")
        clr_module.AddReference = lambda _path: None
        bad_gpu = _FakeHardware("Bad GPU", HardwareType.GpuNvidia, update_error=RuntimeError("driver timeout"))
        cpu = _FakeHardware(
            "CPU",
            HardwareType.Cpu,
            sensors=[_FakeSensor("CPU Package", SensorType.Temperature, 42)],
        )
        computer = _FakeInitComputer([bad_gpu, cpu])
        modules["clr"] = clr_module
        modules["LibreHardwareMonitor.Hardware"].Computer = lambda: computer

        with (
            mock.patch.dict(sys.modules, modules),
            mock.patch.object(overlay.os.path, "exists", return_value=True),
            self.assertLogs("HeatMap", level="WARNING") as logs,
        ):
            result = overlay.init_hardware_monitor()

        self.assertIs(result, computer)
        self.assertTrue(computer.opened)
        self.assertTrue(any("Bad GPU" in message for message in logs.output))

    def test_init_hardware_monitor_keeps_opened_computer_when_cpu_sanity_check_fails(self):
        modules, HardwareType, _SensorType = _fake_lhm_modules()
        clr_module = ModuleType("clr")
        clr_module.AddReference = lambda _path: None
        cpu = _FakeHardware("CPU", HardwareType.Cpu, update_error=RuntimeError("driver timeout"))
        computer = _FakeInitComputer([cpu])
        modules["clr"] = clr_module
        modules["LibreHardwareMonitor.Hardware"].Computer = lambda: computer

        with (
            mock.patch.dict(sys.modules, modules),
            mock.patch.object(overlay.os.path, "exists", return_value=True),
            self.assertLogs("HeatMap", level="WARNING") as logs,
        ):
            result = overlay.init_hardware_monitor()

        self.assertIs(result, computer)
        self.assertTrue(computer.opened)
        self.assertTrue(any("CPU" in message for message in logs.output))

    def test_init_hardware_monitor_still_falls_back_when_open_fails(self):
        modules, _HardwareType, _SensorType = _fake_lhm_modules()
        clr_module = ModuleType("clr")
        clr_module.AddReference = lambda _path: None
        computer = _FakeInitComputer([], open_error=RuntimeError("open failed"))
        modules["clr"] = clr_module
        modules["LibreHardwareMonitor.Hardware"].Computer = lambda: computer

        with (
            mock.patch.dict(sys.modules, modules),
            mock.patch.object(overlay.os.path, "exists", return_value=True),
            self.assertLogs("HeatMap", level="WARNING") as logs,
        ):
            result = overlay.init_hardware_monitor()

        self.assertIsNone(result)
        self.assertTrue(any("Failed to init LibreHardwareMonitor" in message for message in logs.output))

    def test_runtime_status_hides_ok_and_config_priorities_override_sensor_warning(self):
        app = _status_app()

        app._set_sensor_status(overlay.SENSOR_STATUS_PARTIAL)
        self.assertTrue(app.status_label.packed)
        self.assertEqual(app.status_label.options["text"], "Sensors: partial data")
        self.assertEqual(app.status_label.options["fg"], "#facc15")

        app._set_config_status(overlay.STATUS_CONFIG_ADJUSTED)
        self.assertTrue(app.status_label.packed)
        self.assertEqual(app.status_label.options["text"], "Config adjusted")
        self.assertEqual(app.status_label.options["fg"], "#facc15")

        app._set_config_status(overlay.STATUS_CONFIG_SAVE_ERROR)
        self.assertTrue(app.status_label.packed)
        self.assertEqual(app.status_label.options["text"], "Config save failed")
        self.assertEqual(app.status_label.options["fg"], "#f87171")

        app._set_config_status(None)
        self.assertTrue(app.status_label.packed)
        self.assertEqual(app.status_label.options["text"], "Sensors: partial data")

        app._set_sensor_status(None)
        self.assertFalse(app.status_label.packed)

    def test_runtime_status_keeps_driver_warning_until_fixed(self):
        app = _status_app()
        app._driver_status = overlay.SENSOR_STATUS_DRIVER_MISSING

        app._refresh_runtime_status()
        self.assertTrue(app.status_label.packed)
        self.assertEqual(app.status_label.options["text"], "Driver: install PawnIO")
        self.assertEqual(app.status_label.options["fg"], "#f87171")

        app._set_sensor_status(None)
        self.assertTrue(app.status_label.packed)
        self.assertEqual(app.status_label.options["text"], "Driver: install PawnIO")

    def test_runtime_status_shows_sensor_warming_up(self):
        app = _status_app()

        app._set_sensor_status(overlay.SENSOR_STATUS_WARMING_UP)

        self.assertTrue(app.status_label.packed)
        self.assertEqual(app.status_label.options["text"], "Sensors: warming up")
        self.assertEqual(app.status_label.options["fg"], "#facc15")

    def test_detail_row_values_format_expanded_sensor_data(self):
        data = _sample_data()
        data.update({
            "cpu_fan": 1800,
            "gpu_fan": 0,
            "gpu_vram_used_gb": 0.6,
            "gpu_vram_total_gb": 20.0,
            "motherboard_temps": [
                {"name": "System #1", "temp": 27},
                {"name": "CPU", "temp": 65},
                {"name": "PCIe x16", "temp": 31},
                {"name": "VRM MOS", "temp": 34},
                {"name": "Chipset", "temp": 30},
            ],
            "disks": [
                {"name": "980", "temp": 34, "used_pct": 67, "life_pct": 77},
                {"name": "860", "temp": 23, "used_pct": 43, "life_pct": 97},
            ],
        })

        values = overlay._detail_row_values(data)

        self.assertEqual(values["detail_cpu_fan_rpm"], "1800 RPM")
        self.assertEqual(values["detail_gpu_fan_rpm"], "OFF")
        self.assertEqual(values["detail_vram_gb"], "0.6/20.0G")
        self.assertEqual(values["detail_board_temps"], "VRM 34°C  CHIP 30°C  SYS 27°C")
        self.assertEqual(values["detail_disk_life"], "980 77%  860 97%")

    def test_update_peak_values_tracks_maximums(self):
        peaks = overlay._empty_peak_data()
        first = _sample_data()
        first.update({
            "cpu_temp": 58,
            "gpu_temp": 60,
            "ram_pct": 42,
            "disks": [
                {"name": "980", "temp": 38, "used_pct": 68},
                {"name": "860", "temp": 24, "used_pct": 43},
            ],
        })
        second = _sample_data()
        second.update({
            "cpu_temp": 62,
            "gpu_temp": 55,
            "ram_pct": 40,
            "disks": [
                {"name": "980", "temp": 36, "used_pct": 67},
                {"name": "860", "temp": 25, "used_pct": 44},
            ],
        })

        overlay._update_peak_values(peaks, first)
        overlay._update_peak_values(peaks, second)

        self.assertEqual(peaks, {
            "cpu_temp": 62,
            "gpu_temp": 60,
            "ram_pct": 42,
            "disk_temp": 38,
            "disk_used_pct": 68,
        })

    def test_detail_row_values_includes_peak_values(self):
        data = _sample_data()
        peaks = {
            "cpu_temp": 66,
            "gpu_temp": 60,
            "ram_pct": 42,
            "disk_temp": 38,
            "disk_used_pct": 68,
        }

        values = overlay._detail_row_values(data, peaks)

        self.assertEqual(values["detail_peak_temps"], "CPU 66°C  GPU 60°C  DISK 38°C")
        self.assertEqual(values["detail_peak_usage"], "RAM 42%  DISK 68%")

    def test_build_sensor_diagnostics_includes_status_data_and_sensor_inventory(self):
        modules, HardwareType, SensorType = _fake_lhm_modules()
        cpu = _FakeHardware(
            "CPU",
            HardwareType.Cpu,
            sensors=[_FakeSensor("CPU Package", SensorType.Temperature, 58)],
        )
        computer = SimpleNamespace(Hardware=[cpu])
        data = _sample_data()
        data["cpu_temp"] = 58

        with mock.patch.dict(sys.modules, modules):
            text = overlay.build_sensor_diagnostics(
                computer,
                data,
                is_admin=True,
                pawnio_installed=True,
            )

        self.assertIn("Admin: yes", text)
        self.assertIn("PawnIO: installed", text)
        self.assertIn("cpu_temp=58", text)
        self.assertIn("Hardware: CPU (Cpu)", text)
        self.assertIn("Temperature CPU Package = 58", text)

    def test_update_ui_applies_and_clears_sensor_status(self):
        app = _update_ui_app()
        app.sensor_data = _sample_data(status=overlay.SENSOR_STATUS_PSUTIL_FALLBACK)

        app.update_ui()

        self.assertTrue(app.status_label.packed)
        self.assertEqual(app.status_label.options["text"], "Sensors: psutil fallback")

        app.sensor_data = _sample_data()
        app.update_ui()

        self.assertFalse(app.status_label.packed)

    def test_toggle_details_persists_config_and_updates_menu(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.running = True
        app.config = {"details_enabled": False}
        app.details_enabled = False
        app.details_frame = _FakeLabel()
        app.disk_frame = _FakeFrame([])
        app.menu_labels = []
        app._set_menu_label = lambda key, label: app.menu_labels.append((key, label))

        with mock.patch.object(overlay, "save_config", return_value=(True, "Config saved")):
            app.toggle_details()
            app.toggle_details()

        self.assertEqual(app.config["details_enabled"], False)
        self.assertFalse(app.details_enabled)
        self.assertEqual(app.menu_labels, [("details", "Details: ON"), ("details", "Details: OFF")])
        self.assertFalse(app.details_frame.packed)

    def test_clamp_saved_position_updates_config_geometry_and_persists_silently(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.config = {"x": 1850, "y": 1000}
        app.root = _FakeRoot(width=220, height=160)
        save_calls = []
        app._save_config = lambda update_status=True: save_calls.append(update_status)

        metrics = {
            overlay.SM_XVIRTUALSCREEN: 0,
            overlay.SM_YVIRTUALSCREEN: 0,
            overlay.SM_CXVIRTUALSCREEN: 1920,
            overlay.SM_CYVIRTUALSCREEN: 1080,
        }
        with mock.patch.object(overlay.user32, "GetSystemMetrics", side_effect=lambda key: metrics[key]):
            changed = app._clamp_saved_position_to_visible_screen(persist=True)

        self.assertTrue(changed)
        self.assertEqual(app.config["x"], 1700)
        self.assertEqual(app.config["y"], 920)
        self.assertEqual(app.root.geometry_calls, ["+1700+920"])
        self.assertEqual(save_calls, [False])

    def test_reset_peaks_clears_peak_state_and_rows(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.peaks = {
            "cpu_temp": 66,
            "gpu_temp": 60,
            "ram_pct": 42,
            "disk_temp": 38,
            "disk_used_pct": 68,
        }
        app.rows = {
            "detail_peak_temps": _FakeLabel(),
            "detail_peak_usage": _FakeLabel(),
        }

        app.reset_peaks()

        self.assertEqual(app.peaks, overlay._empty_peak_data())
        self.assertEqual(app.rows["detail_peak_temps"].options["text"], "--")
        self.assertEqual(app.rows["detail_peak_usage"].options["text"], "--")

    def test_copy_diagnostics_uses_fresh_monitor_and_clipboard(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.root = _FakeRoot()
        computer = _CloseableComputer()

        with (
            mock.patch.object(overlay, "init_hardware_monitor", return_value=computer) as init_monitor,
            mock.patch.object(overlay, "read_sensors", return_value={"cpu_temp": 58}) as read_sensors,
            mock.patch.object(overlay, "build_sensor_diagnostics", return_value="diagnostic dump") as build,
        ):
            app.copy_diagnostics()

        init_monitor.assert_called_once_with()
        read_sensors.assert_called_once_with(computer)
        build.assert_called_once_with(computer, {"cpu_temp": 58})
        self.assertTrue(computer.closed)
        self.assertEqual(app.root.clipboard_value, "diagnostic dump")

    def test_sensor_loop_reinitializes_after_repeated_sensor_reinit_hints(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        old_computer = _CloseableComputer()
        new_computer = _CloseableComputer()
        app.computer = old_computer
        app.running = True
        app.lock = threading.Lock()
        app._stop_event = _LoopStopEvent(iterations=3)
        app._sensor_start_time = 0

        data = _sample_data(status=overlay.SENSOR_STATUS_PARTIAL)
        data[overlay.SENSOR_REINIT_KEY] = True

        with (
            mock.patch.object(overlay, "read_sensors", return_value=data) as read_sensors,
            mock.patch.object(overlay, "init_hardware_monitor", return_value=new_computer) as init_monitor,
            mock.patch.object(overlay.time, "monotonic", return_value=overlay.SENSOR_WARMUP_SECONDS + 1),
            self.assertLogs("HeatMap", level="WARNING") as logs,
        ):
            app.sensor_loop()

        self.assertEqual(read_sensors.call_count, 3)
        self.assertTrue(old_computer.closed)
        self.assertIs(app.computer, new_computer)
        init_monitor.assert_called_once_with()
        self.assertTrue(any("incomplete sensor samples" in message for message in logs.output))

    def test_sensor_loop_reinitializes_immediately_during_warmup(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        old_computer = _CloseableComputer()
        new_computer = _CloseableComputer()
        app.computer = old_computer
        app.running = True
        app.lock = threading.Lock()
        app._stop_event = _LoopStopEvent(iterations=1)
        app._sensor_start_time = 100

        data = _sample_data(status=overlay.SENSOR_STATUS_PARTIAL)
        data[overlay.SENSOR_REINIT_KEY] = True

        with (
            mock.patch.object(overlay, "read_sensors", return_value=data) as read_sensors,
            mock.patch.object(overlay, "init_hardware_monitor", return_value=new_computer) as init_monitor,
            mock.patch.object(overlay.time, "monotonic", return_value=100 + overlay.SENSOR_WARMUP_SECONDS - 1),
            self.assertLogs("HeatMap", level="WARNING") as logs,
        ):
            app.sensor_loop()

        self.assertEqual(read_sensors.call_count, 1)
        self.assertTrue(old_computer.closed)
        self.assertIs(app.computer, new_computer)
        init_monitor.assert_called_once_with()
        self.assertEqual(app.sensor_data[overlay.SENSOR_STATUS_KEY], overlay.SENSOR_STATUS_WARMING_UP)
        self.assertTrue(any("incomplete sensor samples" in message for message in logs.output))

    def test_save_config_wrapper_sets_and_clears_config_status(self):
        app = _status_app()
        app.config = {"x": 1}
        app._set_config_status(overlay.STATUS_CONFIG_ADJUSTED)

        with mock.patch.object(overlay, "save_config", return_value=(False, "failed")):
            ok, message = app._save_config()

        self.assertFalse(ok)
        self.assertEqual(message, "failed")
        self.assertTrue(app.status_label.packed)
        self.assertEqual(app.status_label.options["text"], "Config save failed")
        self.assertEqual(app._config_status, overlay.STATUS_CONFIG_SAVE_ERROR)

        with mock.patch.object(overlay, "save_config", return_value=(True, "Config saved")):
            ok, message = app._save_config()

        self.assertTrue(ok)
        self.assertEqual(message, "Config saved")
        self.assertIsNone(app._config_status)
        self.assertFalse(app.status_label.packed)

    def test_open_log_file_opens_existing_file(self):
        app = _status_app()
        log_path = os.path.join(self._tmpdir.name, "HeatMap.log")
        open(log_path, "w", encoding="utf-8").close()

        with (
            mock.patch.object(overlay, "LOG_PATH", log_path),
            mock.patch.object(overlay.os, "startfile") as startfile,
        ):
            app.open_log_file()

        startfile.assert_called_once_with(os.path.abspath(log_path))

    def test_open_log_file_falls_back_to_log_directory(self):
        app = _status_app()
        log_path = os.path.join(self._tmpdir.name, "logs", "HeatMap.log")

        with (
            mock.patch.object(overlay, "LOG_PATH", log_path),
            mock.patch.object(overlay.os, "startfile") as startfile,
        ):
            app.open_log_file()

        self.assertTrue(os.path.isdir(os.path.dirname(log_path)))
        startfile.assert_called_once_with(os.path.abspath(os.path.dirname(log_path)))

    def test_copy_log_path_uses_clipboard(self):
        app = _status_app()
        log_path = os.path.join(self._tmpdir.name, "HeatMap.log")

        with mock.patch.object(overlay, "LOG_PATH", log_path):
            app.copy_log_path()

        self.assertEqual(app.root.clipboard_value, os.path.abspath(log_path))

    def test_log_action_failure_shows_error_message(self):
        app = _status_app()

        with (
            mock.patch.object(overlay.os, "startfile", side_effect=OSError("blocked")),
            mock.patch.object(overlay, "_show_error_message") as show_error,
            self.assertLogs("HeatMap", level="WARNING"),
        ):
            app.open_log_file()

        show_error.assert_called_once()
        self.assertIn("blocked", show_error.call_args.args[1])


class _FakeLabel:
    def __init__(self):
        self.options = {}
        self.packed = False
        self.pack_options = {}

    def config(self, **kwargs):
        self.options.update(kwargs)

    def pack(self, **kwargs):
        self.packed = True
        self.pack_options = kwargs

    def pack_forget(self):
        self.packed = False


class _FakeChild:
    def __init__(self):
        self.destroyed = False

    def destroy(self):
        self.destroyed = True


class _FakeFrame:
    def __init__(self, children):
        self._children = children

    def winfo_children(self):
        return self._children


class _FakeRoot:
    def __init__(self, width=200, height=120):
        self.after_calls = []
        self.clipboard_value = None
        self.width = width
        self.height = height
        self.geometry_calls = []

    def after(self, delay, callback):
        self.after_calls.append((delay, callback))

    def geometry(self, spec):
        self.geometry_calls.append(spec)

    def update_idletasks(self):
        pass

    def winfo_width(self):
        return self.width

    def winfo_height(self):
        return self.height

    def clipboard_clear(self):
        self.clipboard_value = ""

    def clipboard_append(self, value):
        self.clipboard_value += value


class _FakeSensor:
    def __init__(self, name, sensor_type, value):
        self.Name = name
        self.SensorType = sensor_type
        self._value = value

    @property
    def Value(self):
        if isinstance(self._value, Exception):
            raise self._value
        return self._value


class _FakeHardware:
    def __init__(self, name, hardware_type, sensors=None, sub_hardware=None, update_error=None):
        self.Name = name
        self.HardwareType = hardware_type
        self.Sensors = sensors or []
        self.SubHardware = sub_hardware or []
        self._update_error = update_error
        self.update_calls = 0

    def Update(self):
        self.update_calls += 1
        if self._update_error is not None:
            raise self._update_error


class _FailingHardwareComputer:
    def __init__(self, error):
        self._error = error

    @property
    def Hardware(self):
        raise self._error


class _CloseableComputer:
    def __init__(self):
        self.closed = False

    def Close(self):
        self.closed = True


class _LoopStopEvent:
    def __init__(self, iterations):
        self.iterations = 0
        self.max_iterations = iterations

    def is_set(self):
        return self.iterations >= self.max_iterations

    def wait(self, _timeout):
        self.iterations += 1
        return self.is_set()


class _FakeInitComputer:
    def __init__(self, hardware, open_error=None):
        self.Hardware = hardware
        self._open_error = open_error
        self.opened = False
        self.IsCpuEnabled = False
        self.IsGpuEnabled = False
        self.IsStorageEnabled = False
        self.IsMemoryEnabled = False
        self.IsMotherboardEnabled = False

    def Open(self):
        if self._open_error is not None:
            raise self._open_error
        self.opened = True


def _completed(returncode=0, stdout=b"", stderr=b""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _status_app():
    app = overlay.OverlayApp.__new__(overlay.OverlayApp)
    app.running = True
    app.root = _FakeRoot()
    app.status_label = _FakeLabel()
    app._status_label_visible = False
    app._config_status = None
    app._driver_status = None
    app._sensor_status = None
    return app


def _update_ui_app():
    app = _status_app()
    app.lock = threading.Lock()
    app.disk_frame = _FakeFrame([])
    app.disk_labels = []
    app._last_disk_names = []
    app.rows = {
        "cpu_temp": _FakeLabel(),
        "cpu_clock": _FakeLabel(),
        "cpu_load": _FakeLabel(),
        "gpu_temp": _FakeLabel(),
        "gpu_clock": _FakeLabel(),
        "gpu_load": _FakeLabel(),
        "vram": _FakeLabel(),
        "gpu_fan": _FakeLabel(),
        "cpu_fan": _FakeLabel(),
        "ram_gb": _FakeLabel(),
        "ram_pct": _FakeLabel(),
    }
    app._GPU_FAN_MAX_RPM = 2200
    app._CPU_FAN_MAX_RPM = 1800
    app._config_save_pending = False
    app.peaks = overlay._empty_peak_data()
    app.config = {}
    app.alerts_enabled = False
    app._check_alerts = lambda _data: None
    return app


def _sample_data(status=None):
    data = {
        "cpu_temp": None,
        "cpu_load": 10,
        "cpu_clock": None,
        "gpu_temp": None,
        "gpu_load": None,
        "gpu_clock": None,
        "cpu_fan": None,
        "cpu_fan_pct": None,
        "gpu_fan": None,
        "gpu_fan_pct": None,
        "gpu_vram_pct": None,
        "ram_pct": 20,
        "ram_used_gb": 2.0,
        "ram_total_gb": 8.0,
        "disks": [],
    }
    if status:
        data[overlay.SENSOR_STATUS_KEY] = status
    return data


def _fake_lhm_modules():
    hardware_type = SimpleNamespace(
        Cpu="Cpu",
        GpuAmd="GpuAmd",
        GpuNvidia="GpuNvidia",
        GpuIntel="GpuIntel",
        Storage="Storage",
        Motherboard="Motherboard",
        Memory="Memory",
    )
    sensor_type = SimpleNamespace(
        Temperature="Temperature",
        Load="Load",
        Clock="Clock",
        Fan="Fan",
        Control="Control",
        SmallData="SmallData",
        Level="Level",
    )
    root_module = ModuleType("LibreHardwareMonitor")
    hardware_module = ModuleType("LibreHardwareMonitor.Hardware")
    hardware_module.HardwareType = hardware_type
    hardware_module.SensorType = sensor_type
    return {
        "LibreHardwareMonitor": root_module,
        "LibreHardwareMonitor.Hardware": hardware_module,
    }, hardware_type, sensor_type


def _memory(percent, used_gb, total_gb):
    return SimpleNamespace(
        percent=percent,
        used=used_gb * 1024 ** 3,
        total=total_gb * 1024 ** 3,
    )


def _task_xml(command, arguments):
    return f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Actions Context="Author">
    <Exec>
      <Command>{command}</Command>
      <Arguments>{arguments}</Arguments>
    </Exec>
  </Actions>
</Task>'''


if __name__ == "__main__":
    unittest.main()
