import json
import math
import os
import tempfile
import threading
import unittest
from types import SimpleNamespace
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
                "gpu_fan_max_rpm": True,
                "cpu_fan_max_rpm": False,
            }, f)

        cfg = overlay.load_config()

        self.assertEqual(cfg["x"], 50)
        self.assertEqual(cfg["y"], 50)
        self.assertFalse(cfg["peek_enabled"])
        self.assertTrue(cfg["alerts_enabled"])
        self.assertEqual(cfg["gpu_fan_max_rpm"], 2200)
        self.assertEqual(cfg["cpu_fan_max_rpm"], 1800)

    def test_save_config_writes_atomically_loadable_json(self):
        cfg = {
            "x": 123,
            "y": 456,
            "peek_enabled": False,
            "alerts_enabled": True,
            "gpu_fan_max_rpm": 3333,
            "cpu_fan_max_rpm": 2222,
        }

        overlay.save_config(cfg)

        self.assertFalse(os.path.exists(f"{overlay.CONFIG_PATH}.tmp"))
        self.assertEqual(overlay.load_config(), cfg)

    def test_disk_temperature_color_matches_critical_alert_threshold(self):
        self.assertEqual(overlay.disk_temp_color(None), "#888888")
        self.assertEqual(overlay.disk_temp_color(44), "#4ade80")
        self.assertEqual(overlay.disk_temp_color(45), "#facc15")
        self.assertEqual(overlay.disk_temp_color(54), "#facc15")
        self.assertEqual(overlay.disk_temp_color(55), "#f87171")

    def test_missing_required_dlls_reports_only_absent_direct_dlls(self):
        open(os.path.join(self._tmpdir.name, "LibreHardwareMonitorLib.dll"), "wb").close()

        self.assertEqual(overlay._missing_required_dlls(self._tmpdir.name), ["HidSharp.dll"])

    def test_main_does_not_kill_previous_instance_when_required_dlls_are_missing(self):
        with (
            mock.patch.object(overlay, "_missing_required_dlls", return_value=["HidSharp.dll"]),
            mock.patch.object(overlay, "kill_previous_instances") as kill_previous,
            mock.patch.object(overlay, "_show_error_message") as show_error,
        ):
            with self.assertRaises(SystemExit) as raised:
                overlay.main()

        self.assertEqual(raised.exception.code, 1)
        kill_previous.assert_not_called()
        show_error.assert_called_once()
        self.assertIn("HidSharp.dll", show_error.call_args.args[1])

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
            mock.patch.object(overlay, "_run_schtasks", return_value=(result, None)),
            mock.patch.object(overlay, "_delete_legacy_autostart_value", return_value=(True, "removed")) as delete_legacy,
        ):
            ok, message = overlay.enable_autostart()

        self.assertTrue(ok)
        self.assertEqual(message, "Autostart enabled")
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


class _FakeLabel:
    def __init__(self):
        self.options = {}

    def config(self, **kwargs):
        self.options.update(kwargs)


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
    def __init__(self):
        self.after_calls = []

    def after(self, delay, callback):
        self.after_calls.append((delay, callback))


def _completed(returncode=0, stdout=b"", stderr=b""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


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
