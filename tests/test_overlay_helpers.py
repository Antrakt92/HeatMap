import ctypes
import json
import logging
import math
import os
import sys
import tempfile
import threading
import unittest
import xml.etree.ElementTree as ET
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

    def test_log_formatter_distinguishes_rotated_log_dates(self):
        record = logging.LogRecord("HeatMap", logging.WARNING, __file__, 1, "message", (), None)

        rendered = logging.Formatter(overlay._LOG_FORMAT, datefmt=overlay._LOG_DATEFMT).format(record)

        self.assertRegex(rendered, r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} \[WARNING\] message$")

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

    def test_load_config_rejects_non_finite_numeric_fields(self):
        defaults = overlay._default_config()
        with open(overlay.CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({
                "x": math.nan,
                "y": math.inf,
                "gpu_fan_max_rpm": -math.inf,
                "cpu_fan_max_rpm": 2100,
            }, f)

        with self.assertLogs("HeatMap", level="WARNING"):
            cfg, warning = overlay.load_config_result()

        self.assertIsNotNone(warning)
        self.assertEqual(cfg["x"], defaults["x"])
        self.assertEqual(cfg["y"], defaults["y"])
        self.assertEqual(cfg["gpu_fan_max_rpm"], defaults["gpu_fan_max_rpm"])
        self.assertEqual(cfg["cpu_fan_max_rpm"], 2100)

    def test_load_config_clamps_huge_integer_coordinates_without_float_conversion(self):
        huge = 10 ** 1000
        with open(overlay.CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"x": huge, "y": -huge}, f)

        cfg, warning = overlay.load_config_result()

        self.assertIsNone(warning)
        self.assertEqual(cfg["x"], huge)
        self.assertEqual(cfg["y"], -huge)

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

    def test_monitor_aware_clamp_moves_window_out_of_staggered_gap(self):
        monitor_areas = (
            ((0, 0, 100, 100), (0, 0, 100, 90)),
            ((100, 50, 200, 150), (100, 50, 200, 140)),
        )

        self.assertEqual(
            overlay._clamp_overlay_to_monitor_areas(150, 10, 20, 20, monitor_areas),
            (150, 50),
        )

    def test_exposed_right_edge_ignores_internal_monitor_seam(self):
        left = ((0, 0, 100, 100), (0, 0, 100, 100))
        right = ((100, 0, 200, 100), (100, 0, 200, 100))
        monitor_areas = (left, right)

        self.assertIsNone(overlay._exposed_right_edge_monitor(99, 50, monitor_areas))
        self.assertEqual(
            overlay._exposed_right_edge_monitor(199, 50, monitor_areas),
            right,
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

    def test_main_does_not_acquire_instance_when_runtime_dlls_are_invalid(self):
        with (
            mock.patch.object(overlay, "_runtime_dll_errors", return_value=["missing DLL: lib/System.Memory.dll"]),
            mock.patch.object(overlay, "acquire_single_instance") as acquire,
            mock.patch.object(overlay, "_show_error_message") as show_error,
        ):
            with self.assertRaises(SystemExit) as raised:
                overlay.main()

        self.assertEqual(raised.exception.code, 1)
        acquire.assert_not_called()
        show_error.assert_called_once()
        self.assertIn("System.Memory.dll", show_error.call_args.args[1])
        self.assertIn("python setup.py --verify", show_error.call_args.args[1])

    def test_single_instance_mutex_rejects_existing_instance_without_killing_it(self):
        with (
            mock.patch.object(overlay, "_instance_mutex_handle", None),
            mock.patch.object(overlay.kernel32, "CreateMutexW", return_value=123),
            mock.patch.object(overlay.kernel32, "GetLastError", return_value=overlay._ERROR_ALREADY_EXISTS),
            mock.patch.object(overlay.kernel32, "CloseHandle") as close,
        ):
            self.assertFalse(overlay.acquire_single_instance())

        close.assert_called_once_with(123)

    def test_single_instance_mutex_is_released_cleanly(self):
        with (
            mock.patch.object(overlay, "_instance_mutex_handle", None),
            mock.patch.object(overlay.kernel32, "CreateMutexW", return_value=456),
            mock.patch.object(overlay.kernel32, "GetLastError", return_value=0),
            mock.patch.object(overlay.kernel32, "ReleaseMutex") as release,
            mock.patch.object(overlay.kernel32, "CloseHandle") as close,
        ):
            self.assertTrue(overlay.acquire_single_instance())
            overlay.release_single_instance()

        release.assert_called_once_with(456)
        close.assert_called_once_with(456)

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

    def test_import_does_not_attach_production_file_handler(self):
        self.assertFalse(any(
            getattr(handler, "_heatmap_log_path", None)
            for handler in overlay.log.handlers
        ))

    def test_parse_autostart_task_xml_accepts_real_schtasks_shape(self):
        xml = _task_xml(r'"C:\Python313\pythonw.exe"', r'"C:\Users\Dima\Documents\GitHub\HeatMap\overlay.py"')

        definition = overlay._parse_autostart_task_xml(xml)

        self.assertEqual(definition.command, r'"C:\Python313\pythonw.exe"')
        self.assertEqual(definition.arguments, r'"C:\Users\Dima\Documents\GitHub\HeatMap\overlay.py"')
        self.assertEqual(definition.run_level, "")

    def test_parse_autostart_task_xml_tolerates_wrong_utf16_declaration(self):
        xml = _task_xml(r'"C:\Python313\pythonw.exe"', r'"C:\HeatMap\overlay.py"')
        xml_bytes = xml.encode("cp1252")

        definition = overlay._parse_autostart_task_xml(xml_bytes)

        self.assertEqual(definition.command, r'"C:\Python313\pythonw.exe"')
        self.assertEqual(definition.arguments, r'"C:\HeatMap\overlay.py"')

    def test_parse_autostart_task_xml_accepts_real_utf16_bytes(self):
        xml = _task_xml(r'"C:\Python313\pythonw.exe"', r'"C:\HeatMap\overlay.py"')

        definition = overlay._parse_autostart_task_xml(xml.encode("utf-16"))

        self.assertEqual(definition.command, r'"C:\Python313\pythonw.exe"')
        self.assertEqual(definition.arguments, r'"C:\HeatMap\overlay.py"')

    def test_build_autostart_task_xml_round_trips_safe_contract(self):
        user_id = r"DESKTOP\Dima"
        xml = overlay._build_autostart_task_xml(
            user_id,
            app_dir=r"C:\Heat Map & Tools",
            system_root=r"C:\Windows",
        )

        definition = overlay._parse_autostart_task_xml(xml)

        self.assertEqual(
            overlay._classify_autostart_task(
                definition,
                user_id,
                app_dir=r"C:\Heat Map & Tools",
                system_root=r"C:\Windows",
            ),
            overlay.AUTOSTART_SAFE_CURRENT,
        )
        self.assertEqual(definition.run_level, "LeastPrivilege")
        self.assertEqual(definition.execution_time_limit, "PT0S")
        self.assertEqual(definition.disallow_start_on_batteries, "false")
        self.assertEqual(definition.stop_on_batteries, "false")
        self.assertEqual(definition.start_when_available, "true")
        self.assertIn(r"Heat Map & Tools\run_as_admin.bat", definition.arguments)

    def test_classify_autostart_rejects_extra_trigger_and_action(self):
        user_id = r"DESKTOP\Dima"
        root = ET.fromstring(overlay._build_autostart_task_xml(user_id))
        namespace = "{http://schemas.microsoft.com/windows/2004/02/mit/task}"
        triggers = root.find(f"{namespace}Triggers")
        ET.SubElement(triggers, f"{namespace}TimeTrigger")
        actions = root.find(f"{namespace}Actions")
        extra_exec = ET.SubElement(actions, f"{namespace}Exec")
        ET.SubElement(extra_exec, f"{namespace}Command").text = "notepad.exe"

        definition = overlay._parse_autostart_task_xml(ET.tostring(root))

        self.assertEqual(
            overlay._classify_autostart_task(definition, user_id),
            overlay.AUTOSTART_STALE_HEATMAP,
        )

    def test_classify_autostart_rejects_each_mutated_lifecycle_contract(self):
        user_id = r"DESKTOP\Dima"
        safe = overlay._parse_autostart_task_xml(overlay._build_autostart_task_xml(user_id))
        mutations = {
            "run_only_if_network_available": "true",
            "allow_start_on_demand": "false",
            "allow_hard_terminate": "false",
            "hidden": "true",
            "wake_to_run": "true",
            "idle_stop_on_end": "true",
            "idle_restart": "true",
            "trigger_user_id": r"DESKTOP\OtherUser",
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                changed = overlay.AutostartTaskDefinition(
                    **{**safe.__dict__, field: value}
                )
                self.assertEqual(
                    overlay._classify_autostart_task(changed, user_id),
                    overlay.AUTOSTART_STALE_HEATMAP,
                )

    def test_query_autostart_does_not_treat_generic_exit_one_as_absent(self):
        result = _completed(returncode=1, stderr=b"Access is denied")
        with (
            mock.patch.object(
                overlay, "_run_task_powershell", return_value=(result, None)
            ) as run,
            mock.patch.object(overlay.time, "sleep") as sleep,
        ):
            definition, error = overlay._query_autostart_task_definition()

        self.assertIsNone(definition)
        self.assertIn("Access is denied", error)
        run.assert_called_once()
        sleep.assert_not_called()

    def test_completed_process_message_decodes_powershell_clixml(self):
        result = _completed(returncode=1, stderr=_task_rpc_clixml_error())

        message = overlay._completed_process_message(result)

        self.assertIn("Get-ScheduledTask : The remote procedure call failed.", message)
        self.assertIn("HRESULT 0x800706be,Get-ScheduledTask", message)
        self.assertNotIn("CLIXML", message)
        self.assertNotIn("_x000D_", message)
        self.assertNotIn("<Objs", message)

    def test_query_autostart_retries_transient_rpc_failure(self):
        user_id = r"DESKTOP\Dima"
        failure = _completed(returncode=1, stderr=_task_rpc_clixml_error())
        success = _completed(stdout=overlay._build_autostart_task_xml(user_id))
        with (
            mock.patch.object(
                overlay,
                "_run_task_powershell",
                side_effect=[(failure, None), (success, None)],
            ) as run,
            mock.patch.object(overlay.time, "sleep") as sleep,
        ):
            definition, error = overlay._query_autostart_task_definition()

        self.assertIsNone(error)
        self.assertEqual(definition.source, overlay.AUTOSTART_SOURCE)
        self.assertEqual(run.call_count, 2)
        sleep.assert_called_once()

    def test_reconcile_error_message_does_not_claim_unattempted_migration(self):
        message = overlay._format_autostart_reconcile_error(
            False,
            "exit code 1: Get-ScheduledTask failed",
        )

        self.assertIn("could not verify autostart security", message)
        self.assertIn("No autostart task was changed", message)
        self.assertNotIn("old elevated autostart task", message)

    def test_register_autostart_passes_xml_in_memory(self):
        result = _completed(returncode=0)
        with mock.patch.object(
            overlay, "_run_task_powershell", return_value=(result, None)
        ) as run:
            ok, _message = overlay._register_autostart_xml(
                overlay._build_autostart_task_xml(r"DESKTOP\Dima")
            )

        self.assertTrue(ok)
        payload = run.call_args.kwargs["input_text"]
        decoded = __import__("base64").b64decode(payload).decode("utf-8")
        self.assertIn("<Task", decoded)
        self.assertNotIn("HeatMap_task_", run.call_args.args[0])

    def test_autostart_identity_rejects_over_the_shoulder_elevation(self):
        with (
            mock.patch.object(overlay, "_current_user_id", return_value="S-1-5-21-1"),
            mock.patch.object(overlay, "_current_user_account", return_value=r"ADMIN\Admin"),
            mock.patch.object(overlay, "_interactive_user_account", return_value=r"DESKTOP\Dima"),
        ):
            user_id, identities, error = overlay._resolve_autostart_identity()

        self.assertIsNone(user_id)
        self.assertEqual(identities, ())
        self.assertIn("different administrator credentials", error)

    def test_classify_autostart_rejects_highest_privilege_legacy_task(self):
        legacy = overlay.AutostartTaskDefinition(
            run_level="HighestAvailable",
            command=r'"C:\Python313\pythonw.exe"',
            arguments=r'"C:\HeatMap\overlay.py"',
        )

        self.assertEqual(
            overlay._classify_autostart_task(legacy, r"DESKTOP\Dima"),
            overlay.AUTOSTART_LEGACY_UNSAFE,
        )

    def test_autostart_enabled_accepts_only_safe_current_definition(self):
        user_id = r"DESKTOP\Dima"
        safe = overlay._parse_autostart_task_xml(
            overlay._build_autostart_task_xml(user_id)
        )
        with (
            mock.patch.object(overlay, "_query_autostart_task_definition", return_value=(safe, None)),
            mock.patch.object(overlay, "_current_user_id", return_value=user_id),
        ):
            self.assertTrue(overlay.is_autostart_enabled())

    def test_autostart_enabled_returns_false_for_malformed_xml(self):
        result = _completed(returncode=0, stdout=b"<Task><Actions>")
        with (
            mock.patch.object(overlay, "_run_task_powershell", return_value=(result, None)),
            mock.patch.object(
                overlay,
                "_resolve_autostart_identity",
                return_value=(r"S-1-5-21-1", (r"S-1-5-21-1",), None),
            ),
        ):
            self.assertFalse(overlay.is_autostart_enabled())

    def test_enable_autostart_keeps_legacy_registry_on_create_failure(self):
        with (
            mock.patch.object(overlay, "_current_user_id", return_value=r"DESKTOP\Dima"),
            mock.patch.object(overlay, "_query_autostart_task_definition", return_value=(None, None)),
            mock.patch.object(overlay, "_register_autostart_xml", return_value=(False, "create failed")),
            mock.patch.object(overlay, "_delete_legacy_autostart_value") as delete_legacy,
        ):
            ok, message = overlay.enable_autostart()

        self.assertFalse(ok)
        self.assertIn("create failed", message)
        delete_legacy.assert_not_called()

    def test_enable_autostart_deletes_legacy_registry_after_successful_create(self):
        user_id = r"DESKTOP\Dima"
        safe = overlay._parse_autostart_task_xml(overlay._build_autostart_task_xml(user_id))
        with (
            mock.patch.object(overlay, "_current_user_id", return_value=user_id),
            mock.patch.object(
                overlay,
                "_query_autostart_task_definition",
                side_effect=[(None, None), (safe, None)],
            ),
            mock.patch.object(overlay, "_register_autostart_xml", return_value=(True, "created")),
            mock.patch.object(overlay, "_delete_legacy_autostart_value", return_value=(True, "removed")) as delete_legacy,
        ):
            ok, message = overlay.enable_autostart()

        self.assertTrue(ok)
        self.assertIn("UAC", message)
        delete_legacy.assert_called_once()

    def test_classify_autostart_rejects_unsafe_lifecycle_setting(self):
        user_id = r"DESKTOP\Dima"
        safe = overlay._parse_autostart_task_xml(overlay._build_autostart_task_xml(user_id))
        unsafe = overlay.AutostartTaskDefinition(
            **{
                **safe.__dict__,
                "disallow_start_on_batteries": "true",
            }
        )

        self.assertEqual(
            overlay._classify_autostart_task(unsafe, user_id),
            overlay.AUTOSTART_STALE_HEATMAP,
        )

    def test_enable_autostart_replaces_legacy_task_fail_closed(self):
        user_id = r"DESKTOP\Dima"
        legacy = overlay.AutostartTaskDefinition(
            run_level="HighestAvailable",
            command=r'"C:\Python313\pythonw.exe"',
            arguments=r'"C:\HeatMap\overlay.py"',
        )
        safe = overlay._parse_autostart_task_xml(overlay._build_autostart_task_xml(user_id))
        disabled_legacy = overlay.AutostartTaskDefinition(
            **{**legacy.__dict__, "enabled": "false"}
        )
        calls = []

        with (
            mock.patch.object(overlay, "_current_user_id", return_value=user_id),
            mock.patch.object(
                overlay,
                "_query_autostart_task_definition",
                side_effect=[
                    (legacy, None),
                    (legacy, None),
                    (disabled_legacy, None),
                    (None, None),
                    (safe, None),
                ],
            ),
            mock.patch.object(
                overlay,
                "_disable_autostart_task",
                side_effect=lambda: (calls.append("disable") or (True, "disabled")),
            ),
            mock.patch.object(
                overlay,
                "_delete_autostart_task",
                side_effect=lambda: (calls.append("delete") or (True, "deleted")),
            ),
            mock.patch.object(
                overlay,
                "_register_autostart_xml",
                side_effect=lambda _xml: (calls.append("create") or (True, "created")),
            ),
            mock.patch.object(overlay, "_delete_legacy_autostart_value", return_value=(True, "removed")),
        ):
            ok, _message = overlay.enable_autostart()

        self.assertTrue(ok)
        self.assertEqual(calls, ["disable", "delete", "create"])

    def test_enable_autostart_leaves_unknown_name_collision_untouched(self):
        collision = overlay.AutostartTaskDefinition(
            source="AnotherApp",
            command=r"C:\Windows\System32\notepad.exe",
        )
        with (
            mock.patch.object(overlay, "_current_user_id", return_value=r"DESKTOP\Dima"),
            mock.patch.object(overlay, "_query_autostart_task_definition", return_value=(collision, None)),
            mock.patch.object(overlay, "_delete_autostart_task") as delete,
            mock.patch.object(overlay, "_register_autostart_xml") as register,
        ):
            ok, message = overlay.enable_autostart()

        self.assertFalse(ok)
        self.assertIn("not owned", message)
        delete.assert_not_called()
        register.assert_not_called()

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

        self.assertEqual(app.menu_labels, [("autostart", "Autostart: ON (UAC)")])

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
        self.assertTrue(data[overlay.SENSOR_REINIT_KEY])
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
        self.assertTrue(data[overlay.SENSOR_REINIT_KEY])

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
                _FakeSensor("Temperature 2", SensorType.Temperature, 62),
                _FakeSensor("Temperature warning", SensorType.Temperature, 81),
                _FakeSensor("Temperature critical", SensorType.Temperature, 84),
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

        self.assertEqual(data["disks"], [{"name": "980 PRO", "temp": 62, "used_pct": 68, "life_pct": 77}])

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

    def test_read_sensors_gpu_temperature_breakdown_uses_hottest_sensor(self):
        modules, HardwareType, SensorType = _fake_lhm_modules()
        gpu = _FakeHardware(
            "AMD Radeon RX 7900 XT",
            HardwareType.GpuAmd,
            sensors=[
                _FakeSensor("Core", SensorType.Temperature, 54),
                _FakeSensor("Memory Junction", SensorType.Temperature, 68),
                _FakeSensor("Hot Spot", SensorType.Temperature, 64),
            ],
        )
        computer = SimpleNamespace(Hardware=[gpu])

        with (
            mock.patch.dict(sys.modules, modules),
            mock.patch.object(overlay.psutil, "cpu_percent", return_value=10),
            mock.patch.object(overlay.psutil, "virtual_memory", return_value=_memory(percent=20, used_gb=2, total_gb=8)),
        ):
            data = overlay.read_sensors(computer)

        self.assertEqual(data["gpu_core_temp"], 54)
        self.assertEqual(data["gpu_memory_temp"], 68)
        self.assertEqual(data["gpu_hotspot_temp"], 64)
        self.assertEqual(data["gpu_temp"], 68)
        self.assertEqual(data["gpu_temp_label"], "MEM")

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

    def test_read_sensors_prefers_aggregate_gpu_load_regardless_of_sensor_order(self):
        modules, HardwareType, SensorType = _fake_lhm_modules()

        def read_with(sensors):
            gpu = _FakeHardware("NVIDIA GPU", HardwareType.GpuNvidia, sensors=sensors)
            with (
                mock.patch.dict(sys.modules, modules),
                mock.patch.object(overlay.psutil, "cpu_percent", return_value=10),
                mock.patch.object(
                    overlay.psutil,
                    "virtual_memory",
                    return_value=_memory(percent=20, used_gb=2, total_gb=8),
                ),
            ):
                return overlay.read_sensors(SimpleNamespace(Hardware=[gpu]))

        aggregate = _FakeSensor("GPU Core", SensorType.Load, 80)
        fallback = _FakeSensor("D3D 3D", SensorType.Load, 20)

        self.assertEqual(read_with([aggregate, fallback])["gpu_load"], 80)
        self.assertEqual(read_with([fallback, aggregate])["gpu_load"], 80)

    def test_read_sensors_prefers_physical_ram_regardless_of_hardware_order(self):
        modules, HardwareType, SensorType = _fake_lhm_modules()
        physical = _FakeHardware(
            "Total Memory",
            HardwareType.Memory,
            sensors=[_FakeSensor("Memory", SensorType.Load, 70)],
        )
        virtual = _FakeHardware(
            "Virtual Memory",
            HardwareType.Memory,
            sensors=[_FakeSensor("Memory", SensorType.Load, 45)],
        )

        def read_with(hardware):
            with (
                mock.patch.dict(sys.modules, modules),
                mock.patch.object(overlay.psutil, "cpu_percent", return_value=10),
                mock.patch.object(
                    overlay.psutil,
                    "virtual_memory",
                    return_value=_memory(percent=22, used_gb=2, total_gb=8),
                ),
            ):
                return overlay.read_sensors(SimpleNamespace(Hardware=hardware))

        self.assertEqual(read_with([physical, virtual])["ram_pct"], 70)
        self.assertEqual(read_with([virtual, physical])["ram_pct"], 70)

    def test_read_sensors_rejects_temperature_sentinels(self):
        modules, HardwareType, SensorType = _fake_lhm_modules()
        gpu = _FakeHardware(
            "NVIDIA GPU",
            HardwareType.GpuNvidia,
            sensors=[_FakeSensor("GPU Core", SensorType.Temperature, 0)],
        )
        storage = _FakeHardware(
            "SSD",
            HardwareType.Storage,
            sensors=[_FakeSensor("Temperature", SensorType.Temperature, -1)],
        )
        motherboard = _FakeHardware(
            "Motherboard",
            HardwareType.Motherboard,
            sensors=[_FakeSensor("System", SensorType.Temperature, 255)],
        )

        with (
            mock.patch.dict(sys.modules, modules),
            mock.patch.object(overlay.psutil, "cpu_percent", return_value=10),
            mock.patch.object(
                overlay.psutil,
                "virtual_memory",
                return_value=_memory(percent=20, used_gb=2, total_gb=8),
            ),
        ):
            data = overlay.read_sensors(SimpleNamespace(Hardware=[gpu, storage, motherboard]))

        self.assertIsNone(data["gpu_temp"])
        self.assertEqual(data["disks"], [{"name": "SSD", "temp": None, "used_pct": None}])
        self.assertEqual(data["motherboard_temps"], [])

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

    def test_read_sensors_uses_one_gpu_bundle_when_discrete_gpu_has_no_temperature(self):
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

        self.assertIsNone(data["gpu_load"])
        self.assertEqual(data["gpu_temp"], 45)
        self.assertEqual(intel_gpu.update_calls, 1)

    def test_read_sensors_selects_multi_gpu_metrics_as_permutation_invariant_bundle(self):
        modules, HardwareType, SensorType = _fake_lhm_modules()
        gpu_a = _FakeHardware(
            "GPU A",
            HardwareType.GpuNvidia,
            sensors=[
                _FakeSensor("GPU Core", SensorType.Temperature, 90),
                _FakeSensor("GPU Core", SensorType.Load, 10),
            ],
        )
        gpu_b = _FakeHardware(
            "GPU B",
            HardwareType.GpuAmd,
            sensors=[
                _FakeSensor("GPU Core", SensorType.Temperature, 50),
                _FakeSensor("GPU Core", SensorType.Load, 80),
            ],
        )

        def read(order):
            with (
                mock.patch.dict(sys.modules, modules),
                mock.patch.object(overlay.psutil, "cpu_percent", return_value=10),
                mock.patch.object(
                    overlay.psutil,
                    "virtual_memory",
                    return_value=_memory(percent=20, used_gb=2, total_gb=8),
                ),
            ):
                return overlay.read_sensors(SimpleNamespace(Hardware=order))

        first = read([gpu_a, gpu_b])
        second = read([gpu_b, gpu_a])

        self.assertEqual((first["gpu_temp"], first["gpu_load"]), (90, 10))
        self.assertEqual((second["gpu_temp"], second["gpu_load"]), (90, 10))

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

    def test_init_hardware_monitor_sanity_check_skips_non_cpu_hardware(self):
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
            self.assertNoLogs("HeatMap", level="WARNING"),
        ):
            result = overlay.init_hardware_monitor()

        self.assertIs(result, computer)
        self.assertTrue(computer.opened)
        self.assertEqual(bad_gpu.update_calls, 0)
        self.assertEqual(cpu.update_calls, 1)

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
        self.assertTrue(computer.closed)
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

        app._set_config_status(overlay.STATUS_CONFIG_ADJUSTED)
        self.assertEqual(app.status_label.options["text"], "Driver: install PawnIO")

        app._set_config_status(overlay.STATUS_CONFIG_SAVE_ERROR)
        self.assertEqual(app.status_label.options["text"], "Config save failed")

    def test_runtime_status_reports_cpu_unavailable_after_driver_check(self):
        app = _status_app()

        app._set_sensor_status(overlay.SENSOR_STATUS_CPU_UNAVAILABLE)

        self.assertTrue(app.status_label.packed)
        self.assertEqual(app.status_label.options["text"], "CPU sensor unavailable")
        self.assertEqual(app.status_label.options["fg"], "#f87171")

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

    def test_detail_row_values_includes_gpu_temperature_breakdown(self):
        data = _sample_data()
        data.update({
            "gpu_core_temp": 54,
            "gpu_hotspot_temp": 64,
            "gpu_memory_temp": 68,
        })

        values = overlay._detail_row_values(data)

        self.assertEqual(values["detail_gpu_temps"], "CORE 54°C  HOT 64°C  MEM 68°C")

    def test_update_peak_values_tracks_maximums(self):
        peaks = overlay._empty_peak_data()
        first = _sample_data()
        first.update({
            "cpu_temp": 58,
            "gpu_temp": 60,
            "gpu_temp_label": "CORE",
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
            "gpu_temp_label": "HOT",
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
            "gpu_temp_label": "CORE",
            "ram_pct": 42,
            "disk_temp": 38,
            "disk_used_pct": 68,
        })

    def test_detail_row_values_includes_peak_values(self):
        data = _sample_data()
        peaks = {
            "cpu_temp": 66,
            "gpu_temp": 60,
            "gpu_temp_label": "HOT",
            "ram_pct": 42,
            "disk_temp": 38,
            "disk_used_pct": 68,
        }

        values = overlay._detail_row_values(data, peaks)

        self.assertEqual(values["detail_peak_temps"], "CPU 66°C  GPU HOT 60°C  DISK 38°C")
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

        monitor_areas = (((0, 0, 1920, 1080), (0, 0, 1920, 1080)),)
        with mock.patch.object(overlay, "_get_monitor_areas", return_value=monitor_areas):
            changed = app._clamp_saved_position_to_visible_screen(persist=True)

        self.assertTrue(changed)
        self.assertEqual(app.config["x"], 1700)
        self.assertEqual(app.config["y"], 920)
        self.assertEqual(app.root.geometry_calls, ["+1700+920"])
        self.assertEqual(save_calls, [False])

    def test_set_parent_verified_checks_native_postcondition(self):
        with (
            mock.patch.object(overlay.user32, "IsWindow", return_value=True),
            mock.patch.object(overlay.user32, "GetWindowLongW", return_value=overlay.WS_POPUP),
            mock.patch.object(overlay.user32, "SetWindowLongW") as set_style,
            mock.patch.object(overlay.user32, "SetParent") as set_parent,
            mock.patch.object(overlay.user32, "GetParent", return_value=0),
        ):
            self.assertFalse(overlay._set_parent_verified(100, 200))

        set_parent.assert_called_once_with(100, 200)
        self.assertEqual(
            set_style.call_args_list,
            [
                mock.call(100, overlay.GWL_STYLE, overlay.WS_CHILD),
                mock.call(100, overlay.GWL_STYLE, ctypes.c_long(overlay.WS_POPUP).value),
            ],
        )

    def test_set_parent_verified_accepts_native_null_as_detached_parent(self):
        with (
            mock.patch.object(overlay.user32, "IsWindow", return_value=True),
            mock.patch.object(overlay.user32, "GetWindowLongW", return_value=overlay.WS_CHILD),
            mock.patch.object(overlay.user32, "SetWindowLongW") as set_style,
            mock.patch.object(overlay.user32, "SetParent") as set_parent,
            mock.patch.object(overlay.user32, "GetParent", return_value=None),
            mock.patch.object(overlay.user32, "SetWindowPos") as set_window_pos,
        ):
            self.assertTrue(overlay._set_parent_verified(100, 0))

        set_parent.assert_called_once_with(100, 0)
        set_style.assert_called_once_with(
            100,
            overlay.GWL_STYLE,
            ctypes.c_long(overlay.WS_POPUP).value,
        )
        set_window_pos.assert_called_once()

    def test_set_parent_verified_sets_child_style_before_embedding(self):
        with (
            mock.patch.object(overlay.user32, "IsWindow", return_value=True),
            mock.patch.object(overlay.user32, "GetWindowLongW", return_value=overlay.WS_POPUP),
            mock.patch.object(overlay.user32, "SetWindowLongW") as set_style,
            mock.patch.object(overlay.user32, "SetParent") as set_parent,
            mock.patch.object(overlay.user32, "GetParent", return_value=200),
            mock.patch.object(overlay.user32, "SetWindowPos") as set_window_pos,
        ):
            self.assertTrue(overlay._set_parent_verified(100, 200))

        set_style.assert_called_once_with(100, overlay.GWL_STYLE, overlay.WS_CHILD)
        set_parent.assert_called_once_with(100, 200)
        set_window_pos.assert_called_once()

    def test_find_desktop_host_rejects_progman_parent_that_dies_with_explorer(self):
        progman = 100

        def find_window_ex(parent, after, class_name, _title):
            if (parent, class_name) == (progman, "SHELLDLL_DefView"):
                return 200
            return 0

        def enum_windows(callback, _lparam):
            callback(progman, 0)
            return True

        with (
            mock.patch.object(overlay.user32, "FindWindowW", return_value=progman),
            mock.patch.object(overlay.user32, "SendMessageTimeoutW", return_value=1),
            mock.patch.object(overlay.user32, "FindWindowExW", side_effect=find_window_ex),
            mock.patch.object(overlay.user32, "EnumWindows", side_effect=enum_windows),
        ):
            self.assertIsNone(overlay.find_desktop_worker_w())

    def test_find_desktop_host_uses_worker_behind_defview_worker(self):
        progman = 100
        icons_worker = 300
        wallpaper_worker = 400

        def find_window_ex(parent, after, class_name, _title):
            if (parent, class_name) == (icons_worker, "SHELLDLL_DefView"):
                return 301
            if (parent, after, class_name) == (0, icons_worker, "WorkerW"):
                return wallpaper_worker
            return 0

        def enum_windows(callback, _lparam):
            callback(progman, 0)
            callback(icons_worker, 0)
            return True

        with (
            mock.patch.object(overlay.user32, "FindWindowW", return_value=progman),
            mock.patch.object(overlay.user32, "SendMessageTimeoutW", return_value=1),
            mock.patch.object(overlay.user32, "FindWindowExW", side_effect=find_window_ex),
            mock.patch.object(overlay.user32, "EnumWindows", side_effect=enum_windows),
        ):
            self.assertEqual(overlay.find_desktop_worker_w(), wallpaper_worker)

    def test_embed_in_desktop_keeps_overlay_behind_host_children(self):
        expected_flags = overlay.SWP_NOMOVE | overlay.SWP_NOSIZE | overlay.SWP_NOACTIVATE
        with (
            mock.patch.object(overlay, "find_desktop_worker_w", return_value=200),
            mock.patch.object(overlay, "_set_parent_verified", return_value=True) as set_parent,
            mock.patch.object(overlay.user32, "SetWindowPos", return_value=True) as set_window_pos,
        ):
            self.assertTrue(overlay.embed_in_desktop(100))

        set_parent.assert_called_once_with(100, 200)
        set_window_pos.assert_called_once_with(100, overlay.HWND_BOTTOM, 0, 0, 0, 0, expected_flags)

    def test_failed_lowering_keeps_native_parent_tracked_for_later_detach(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.running = True
        app.topmost = False
        app.peek_visible = False
        app._peek_animating = False
        app.embedded = False
        app.root = _FakeRoot()
        app._embed_after_id = None
        app._get_hwnd = lambda: 100
        with (
            mock.patch.object(overlay, "find_desktop_worker_w", return_value=200),
            mock.patch.object(overlay, "_set_parent_verified", return_value=True) as set_parent,
            mock.patch.object(overlay, "set_tool_window"),
            mock.patch.object(overlay.user32, "SetWindowPos", return_value=False),
            mock.patch.object(overlay.user32, "GetParent", return_value=200),
            mock.patch.object(overlay.user32, "IsWindow", return_value=True),
            mock.patch.object(overlay.log, "warning"),
        ):
            app._embed_into_desktop()
            self.assertTrue(app.embedded)
            self.assertTrue(app._detach_from_desktop())

        self.assertFalse(app.embedded)
        self.assertEqual(set_parent.call_args_list, [mock.call(100, 200), mock.call(100, 0)])

    def test_failed_lowering_does_not_claim_a_lost_desktop_parent(self):
        with (
            mock.patch.object(overlay, "find_desktop_worker_w", return_value=200),
            mock.patch.object(overlay, "_set_parent_verified", return_value=True),
            mock.patch.object(overlay.user32, "SetWindowPos", return_value=False),
            mock.patch.object(overlay.user32, "GetParent", return_value=0),
            mock.patch.object(overlay.user32, "IsWindow", return_value=False),
            mock.patch.object(overlay.log, "warning"),
        ):
            self.assertFalse(overlay.embed_in_desktop(100))

    def test_stale_embed_callback_is_ignored_after_transition_cancel(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.running = True
        app.topmost = False
        app.peek_visible = False
        app._peek_animating = False
        app.embedded = False
        app.root = _FakeRoot()
        app._embed_after_id = None
        app._window_transition_generation = 0
        app._get_hwnd = lambda: 123

        with mock.patch.object(overlay, "embed_in_desktop", return_value=True) as embed:
            app._schedule_embed(100)
            stale_callback = app.root.after_calls[-1][1]
            app._cancel_scheduled_embed()
            stale_callback()

        embed.assert_not_called()
        self.assertFalse(app.embedded)

    def test_screen_poll_reembeds_after_explorer_replaces_desktop_parent(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.running = True
        app.topmost = False
        app.peek_visible = False
        app._peek_animating = False
        app.embedded = True
        app._embed_after_id = None
        app._monitor_areas = [((0, 0, 1920, 1080), (0, 0, 1920, 1040))]
        app.root = _FakeRoot()
        app._has_valid_desktop_parent = lambda: False
        app._schedule_embed = mock.Mock()

        with mock.patch.object(overlay, "_get_monitor_areas", return_value=app._monitor_areas):
            app._poll_screen_change()

        self.assertFalse(app.embedded)
        app._schedule_embed.assert_called_once_with(50)
        self.assertEqual(app.root.after_calls[-1][0], 5000)

    def test_toggle_topmost_stays_off_when_detach_fails(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.topmost = False
        app.peek_visible = False
        app._peek_animating = False
        app._saved_pos = None
        app.embedded = True
        app.root = _FakeRoot()
        app._embed_after_id = None
        app._window_transition_generation = 0
        app._detach_from_desktop = lambda: False

        with mock.patch.object(overlay, "_show_error_message") as show_error:
            app.toggle_topmost()

        self.assertFalse(app.topmost)
        show_error.assert_called_once()

    def test_toggle_topmost_from_peek_restores_visible_alpha(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.running = True
        app.peek_enabled = True
        app.topmost = False
        app.peek_visible = True
        app._peek_animating = False
        app._saved_pos = (100, 120)
        app._peek_monitor_area = ((0, 0, 1920, 1080), (0, 0, 1920, 1040))
        app.embedded = False
        app.config = {"x": 50, "y": 60}
        app.root = _FakeRoot()
        app._embed_after_id = None
        app._window_transition_generation = 0
        app._detach_from_desktop = lambda: True
        app._set_menu_label = lambda *_args: None

        app.toggle_topmost()

        self.assertTrue(app.topmost)
        self.assertIn(("-alpha", 0.88), app.root.attribute_calls)
        self.assertEqual(app.root.attribute_calls[-1], ("-topmost", True))

    def test_peek_uses_work_area_right_edge_and_restores_alpha(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.running = True
        app.peek_enabled = True
        app.peek_visible = False
        app._peek_animating = False
        app.topmost = False
        app.embedded = False
        app.config = {"x": 100, "y": 120}
        app.root = _FakeRoot(width=200, height=120)
        app._embed_after_id = None
        app._window_transition_generation = 0
        app._monitor_areas = (((0, 0, 1920, 1080), (0, 0, 1880, 1040)),)
        app._is_desktop_at_cursor = lambda: False
        app._detach_from_desktop = lambda: True
        animation = []
        app._animate_slide = lambda *args, **kwargs: animation.append((args, kwargs))

        app._peek_show(app._monitor_areas[0])

        self.assertEqual(animation[0][0][:3], (1920, 1680, 120))
        self.assertIn(("-alpha", 0.88), app.root.attribute_calls)

    def test_toggle_peek_off_restores_saved_position_and_persists_it(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.running = True
        app.config = {"peek_enabled": True, "x": 50, "y": 60}
        app.peek_enabled = True
        app.peek_visible = True
        app._peek_animating = False
        app._saved_pos = (320, 240)
        app._peek_monitor_area = ((0, 0, 1920, 1080), (0, 0, 1920, 1040))
        app.topmost = False
        app.root = _FakeRoot()
        app._cursor_was_at_peek_edge = True
        app.menu_labels = []
        schedule_calls = []
        save_snapshots = []
        app._set_menu_label = lambda key, label: app.menu_labels.append((key, label))
        app._schedule_embed = lambda delay: schedule_calls.append(delay)
        app._save_config = lambda: save_snapshots.append(dict(app.config))

        app.toggle_peek()

        self.assertFalse(app.peek_enabled)
        self.assertFalse(app.peek_visible)
        self.assertFalse(app._peek_animating)
        self.assertIsNone(app._saved_pos)
        self.assertEqual(app.config, {"peek_enabled": False, "x": 320, "y": 240})
        self.assertEqual(app.root.geometry_calls, ["+320+240"])
        self.assertEqual(app.root.attribute_calls, [("-alpha", 0), ("-topmost", False)])
        self.assertEqual(schedule_calls, [50])
        self.assertEqual(app.menu_labels, [("peek", "Peek from edge: OFF")])
        self.assertEqual(save_snapshots[-1], {"peek_enabled": False, "x": 320, "y": 240})

    def test_toggle_peek_on_rearms_cursor_edge(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.running = True
        app.config = {"peek_enabled": False, "x": 50, "y": 60}
        app.peek_enabled = False
        app.peek_visible = False
        app._peek_animating = False
        app._saved_pos = None
        app.topmost = False
        app.root = _FakeRoot()
        app._cursor_was_at_peek_edge = True
        app.menu_labels = []
        save_snapshots = []
        app._set_menu_label = lambda key, label: app.menu_labels.append((key, label))
        app._save_config = lambda: save_snapshots.append(dict(app.config))

        app.toggle_peek()

        self.assertTrue(app.peek_enabled)
        self.assertEqual(app.config["peek_enabled"], True)
        self.assertFalse(app._cursor_was_at_peek_edge)
        self.assertEqual(app.menu_labels, [("peek", "Peek from edge: ON")])
        self.assertEqual(save_snapshots, [{"peek_enabled": True, "x": 50, "y": 60}])

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
        app.running = True
        app._stop_event = threading.Event()
        app._set_menu_label = mock.Mock()
        app.root = _FakeRoot()
        computer = _CloseableComputer()

        with (
            mock.patch.object(overlay, "init_hardware_monitor", return_value=computer) as init_monitor,
            mock.patch.object(overlay, "read_sensors", return_value={"cpu_temp": 58}) as read_sensors,
            mock.patch.object(overlay, "build_sensor_diagnostics", return_value="diagnostic dump") as build,
        ):
            app.copy_diagnostics()
            app._diagnostics_thread.join(3)
            app._poll_diagnostics()

        init_monitor.assert_called_once_with()
        read_sensors.assert_called_once_with(computer)
        build.assert_called_once_with(computer, {"cpu_temp": 58})
        self.assertTrue(computer.closed)
        self.assertEqual(app.root.clipboard_value, "diagnostic dump")

    def test_prepare_verified_pawnio_installer_returns_verified_path(self):
        with mock.patch("setup.download_pawnio", return_value=r"C:\verified\PawnIO.exe"):
            ok, detail = overlay.prepare_verified_pawnio_installer()

        self.assertTrue(ok)
        self.assertEqual(detail, r"C:\verified\PawnIO.exe")

    def test_pawnio_status_check_fails_safe_when_setup_check_breaks(self):
        with (
            mock.patch("setup.is_pawnio_driver_installed", side_effect=OSError("registry denied")),
            self.assertLogs("HeatMap", level="WARNING"),
        ):
            self.assertFalse(overlay.is_pawnio_driver_installed())

    def test_finish_pawnio_repair_opens_folder_and_explains_restart(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.running = True
        app._pawnio_repair_running = True
        app.menu_labels = []
        app._set_menu_label = lambda key, label: app.menu_labels.append((key, label))
        with (
            mock.patch.object(overlay.os, "startfile", create=True) as startfile,
            mock.patch.object(overlay, "_show_info_message") as show_info,
        ):
            app._finish_pawnio_repair(True, r"C:\verified\PawnIO.exe")

        self.assertFalse(app._pawnio_repair_running)
        startfile.assert_called_once_with(r"C:\verified")
        self.assertIn("hardware-smoke", show_info.call_args.args[1])

    def test_sensor_loop_reinitializes_after_repeated_sensor_reinit_hints(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        old_computer = _CloseableComputer()
        new_computer = _CloseableComputer()
        app.computer = old_computer
        app.running = True
        app.lock = threading.Lock()
        app._stop_event = _LoopStopEvent(iterations=4)
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

        self.assertEqual(read_sensors.call_count, 4)
        self.assertTrue(old_computer.closed)
        self.assertTrue(new_computer.closed)
        self.assertIsNone(app.computer)
        init_monitor.assert_called_once_with()
        self.assertTrue(any("incomplete sensor samples" in message for message in logs.output))

    def test_sensor_loop_retries_initial_monitor_fallback(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        new_computer = _CloseableComputer()
        app.computer = None
        app.running = True
        app.lock = threading.Lock()
        app._stop_event = _LoopStopEvent(iterations=1)
        app._sensor_start_time = 0
        data = _sample_data()

        with (
            mock.patch.object(overlay, "init_hardware_monitor", return_value=new_computer) as init_monitor,
            mock.patch.object(overlay, "read_sensors", return_value=data) as read_sensors,
            mock.patch.object(overlay.time, "monotonic", return_value=overlay.SENSOR_WARMUP_SECONDS + 1),
            self.assertNoLogs("HeatMap", level="WARNING"),
        ):
            app.sensor_loop()

        init_monitor.assert_called_once_with()
        read_sensors.assert_called_once_with(new_computer, update_storage=True)
        self.assertTrue(new_computer.closed)
        self.assertIsNone(app.computer)

    def test_sensor_loop_schedules_reinitialization_during_warmup(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        old_computer = _CloseableComputer()
        new_computer = _CloseableComputer()
        app.computer = old_computer
        app.running = True
        app.lock = threading.Lock()
        app._stop_event = _LoopStopEvent(iterations=2)
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

        self.assertEqual(read_sensors.call_count, 2)
        self.assertTrue(old_computer.closed)
        self.assertTrue(new_computer.closed)
        self.assertIsNone(app.computer)
        init_monitor.assert_called_once_with()
        self.assertEqual(app.sensor_data[overlay.SENSOR_STATUS_KEY], overlay.SENSOR_STATUS_WARMING_UP)
        self.assertTrue(any("incomplete sensor samples" in message for message in logs.output))

    def test_sensor_loop_marks_cpu_unavailable_after_warmup(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.computer = _CloseableComputer()
        app.running = True
        app.lock = threading.Lock()
        app._stop_event = _LoopStopEvent(iterations=1)
        app._sensor_start_time = 0
        data = _sample_data()
        data["cpu_temp"] = None

        with (
            mock.patch.object(overlay, "read_sensors", return_value=data),
            mock.patch.object(
                overlay.time,
                "monotonic",
                return_value=overlay.SENSOR_WARMUP_SECONDS + 1,
            ),
        ):
            app.sensor_loop()

        self.assertEqual(
            app.sensor_data[overlay.SENSOR_STATUS_KEY],
            overlay.SENSOR_STATUS_CPU_UNAVAILABLE,
        )

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
        self.attribute_calls = []
        self.withdraw_count = 0
        self.deiconify_count = 0
        self.cancelled_after_ids = []

    def after(self, delay, callback):
        self.after_calls.append((delay, callback))
        return f"after-{len(self.after_calls)}"

    def after_cancel(self, after_id):
        self.cancelled_after_ids.append(after_id)

    def geometry(self, spec):
        self.geometry_calls.append(spec)

    def wm_attributes(self, name, value):
        self.attribute_calls.append((name, value))

    def withdraw(self):
        self.withdraw_count += 1

    def deiconify(self):
        self.deiconify_count += 1

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
        self.closed = False
        self.IsCpuEnabled = False
        self.IsGpuEnabled = False
        self.IsStorageEnabled = False
        self.IsMemoryEnabled = False
        self.IsMotherboardEnabled = False

    def Open(self):
        if self._open_error is not None:
            raise self._open_error
        self.opened = True

    def Close(self):
        self.closed = True


def _completed(returncode=0, stdout=b"", stderr=b""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _task_rpc_clixml_error():
    return (
        '#< CLIXML\n<Objs Version="1.1.0.1" '
        'xmlns="http://schemas.microsoft.com/powershell/2004/04">'
        '<S S="Error">Get-ScheduledTask : The remote procedure call failed. '
        '_x000D__x000A_</S>'
        '<S S="Error">    + FullyQualifiedErrorId : HRESULT '
        '0x800706be,Get-ScheduledTask_x000D__x000A_</S>'
        '</Objs>'
    ).encode("utf-8")


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
