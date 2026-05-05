import json
import math
import os
import tempfile
import threading
import unittest
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
            mock.patch.object(overlay.ctypes.windll.user32, "MessageBoxW") as message_box,
        ):
            with self.assertRaises(SystemExit) as raised:
                overlay.main()

        self.assertEqual(raised.exception.code, 1)
        kill_previous.assert_not_called()
        message_box.assert_called_once()
        self.assertIn("HidSharp.dll", message_box.call_args.args[1])

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


if __name__ == "__main__":
    unittest.main()
