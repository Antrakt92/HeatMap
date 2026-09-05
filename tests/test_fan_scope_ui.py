import io
import json
import unittest
from types import SimpleNamespace as NS
from unittest import mock

import case_fans as fans
import overlay
from test_case_fans import fixture
from test_thermal_advisor import sample
from test_ui_layout import TkTestCase, layout_app


def status_for(channels, **changes):
    return dict(state="active", command_pct=77, controlled_channels=list(channels),
                firmware_channels=[name for name in fans.TARGETS if name not in channels], **changes)


class FanScopeTests(unittest.TestCase):
    def test_mode_identifies_exact_active_channels(self):
        self.assertEqual(overlay._case_fan_mode(status_for(fans.INDEPENDENT_TARGETS)), "AUTO 77% · SYS 1/2")
        self.assertEqual(overlay._case_fan_mode(status_for(fans.TARGETS)), "AUTO 77% · SYS 1/2/4")
        for state, expected in (("off", "Firmware"), ("stopped", "Firmware"),
                                ("checking", "Checking..."), ("error", "ERROR")):
            self.assertEqual(overlay._case_fan_mode(dict(state=state)), expected)

    def test_malformed_scope_is_rejected_before_reaching_ui(self):
        for controlled, firmware in ((None, []), ([1, 2], []), (list(fans.INDEPENDENT_TARGETS), None),
                                      ([fans.TARGETS[0], fans.TARGETS[2]], []), ([], [])):
            with self.subTest(controlled=controlled, firmware=firmware):
                client = fans.FanWorkerClient(".")
                client.process = mock.Mock(pid=7, stdin=io.StringIO())
                client.process.poll.return_value = None
                client.status_path = "unused-mocked-status.json"
                client.started = 90
                report = dict(state="active", command_pct=80, time=100, pid=7,
                              controlled_channels=controlled, firmware_channels=firmware)
                with (
                    mock.patch.object(fans, "open_status_file", return_value=io.StringIO(json.dumps(report))),
                    mock.patch.object(fans.time, "time", return_value=100),
                ):
                    status = client.poll()
                self.assertEqual(status["state"], "error")
                self.assertEqual(overlay._case_fan_mode(status), "ERROR")

    def test_config_accepts_both_supported_full_rpm_reference_schemas(self):
        for channels in (fans.INDEPENDENT_TARGETS, fans.TARGETS):
            with self.subTest(channels=channels):
                reference = {name: 1200 for name in channels}
                config, errors = overlay._normalize_config(
                    dict(case_fan_full_rpm=reference), overlay._default_config())
                self.assertNotIn("case_fan_full_rpm", errors)
                self.assertEqual(config["case_fan_full_rpm"], reference)

    def test_config_rejects_unsupported_partial_or_foreign_reference_schemas(self):
        for channels in ((fans.TARGETS[0],), (fans.TARGETS[0], fans.TARGETS[2]),
                         (*fans.TARGETS, "System Fan #5 / Pump")):
            with self.subTest(channels=channels):
                config, errors = overlay._normalize_config(
                    dict(case_fan_full_rpm={name: 1200 for name in channels}), overlay._default_config())
                self.assertIn("case_fan_full_rpm", errors)
                self.assertNotIn("case_fan_full_rpm", config)

    def test_fallback_worker_restores_only_independent_channels_after_failures(self):
        for failure in ("sensor", "status", "owner"):
            with self.subTest(failure=failure):
                computer, controls = fixture()
                for sub in computer.Hardware[0].SubHardware:
                    for sensor in sub.Sensors:
                        if str(sensor.SensorType) == "Control" and "Pump" in str(sensor.Name):
                            sensor.Value = 82 if "#5" in sensor.Name else 81
                owner = mock.Mock()
                owner.create_time.return_value = 1
                owner.is_running.side_effect = [True, True, True, False]
                modules = {"clr": mock.Mock(), "LibreHardwareMonitor": mock.Mock(),
                           "LibreHardwareMonitor.Hardware": NS(Computer=lambda: computer)}
                reports = []

                def publish(_path, state, **details):
                    if failure == "status" and state == "checking":
                        raise PermissionError("synthetic status failure")
                    reports.append(dict(state=state, **details))

                readings = [{}, RuntimeError("synthetic sensor failure")] if failure == "sensor" else [{}, {}]
                with (
                    mock.patch.dict("sys.modules", modules),
                    mock.patch.object(overlay, "_is_admin", return_value=True),
                    mock.patch.object(overlay, "_runtime_dll_errors", return_value=[]),
                    mock.patch.object(fans.psutil, "process_iter", return_value=[]),
                    mock.patch.object(fans.psutil, "Process", return_value=owner),
                    mock.patch.object(fans.threading, "Thread"),
                    mock.patch.object(fans.threading.Event, "wait", return_value=False),
                    mock.patch.object(fans, "write_status", side_effect=publish),
                    mock.patch.object(overlay, "read_sensors", side_effect=readings),
                ):
                    result = fans.worker("unused-mocked-status.json", 7, 1)
                self.assertEqual(result, 0 if failure == "owner" else 1)
                for control in controls[:2]:
                    control.SetSoftware.assert_called_once_with(100.0)
                    control.SetDefault.assert_called_once_with()
                for untouched in controls[2:]:
                    untouched.SetSoftware.assert_not_called()
                    untouched.SetDefault.assert_not_called()
                computer.Close.assert_called_once_with()
                final = reports[-1]
                self.assertTrue(final["restore_confirmed"])
                self.assertEqual(final["restore_errors"], [])
                self.assertEqual(final["state"], "stopped" if failure == "owner" else "error")
                for report in reports:
                    self.assertEqual(report["controlled_channels"], list(fans.INDEPENDENT_TARGETS))
                    self.assertEqual(report["firmware_channels"], [fans.TARGETS[2]])
                    self.assertEqual([row["name"] for row in report["baseline"]], list(fans.INDEPENDENT_TARGETS))


class FanScopeNativeUiTests(TkTestCase):
    def test_cooling_status_reuses_dialog_refreshes_and_cancels_timer_on_close(self):
        with layout_app() as app:
            create_toplevel = overlay.tk.Toplevel

            def hidden_dialog(*args, **kwargs):
                dialog = create_toplevel(*args, **kwargs)
                dialog.withdraw()
                dialog.geometry("480x420+-30000+-30000")
                return dialog

            app._case_fan_status = status_for(fans.INDEPENDENT_TARGETS, demand_pct=72, reason="GPU Hotspot")
            config_before = dict(app.config)
            callbacks_before = set(app.root.tk.call("after", "info"))
            app._save_config = mock.Mock()
            with (
                mock.patch.object(overlay.tk, "Toplevel", side_effect=hidden_dialog) as create,
                mock.patch.object(app.root, "after", wraps=app.root.after) as schedule,
            ):
                app.show_cooling_status()
                dialog = app._cooling_dialog
                label = next(child for child in dialog.winfo_children() if isinstance(child, overlay.tk.Label))
                self.assertIn("Target: 72%   Command: 77%", label.cget("text"))
                self.assertIn("Firmware: System Fan #4", label.cget("text"))
                self.assertIsNone(app.root.grab_current())
                first_callback = schedule.call_args.args[1]
                first_timer = (set(app.root.tk.call("after", "info")) - callbacks_before).pop()
                with mock.patch.object(dialog, "lift") as lift:
                    app.show_cooling_status()
                create.assert_called_once()
                lift.assert_called_once_with()
                self.assertIs(app._cooling_dialog, dialog)
                # Deliver the scheduled Python callback without waiting a second.
                app.root.after_cancel(first_timer)
                app._case_fan_status = status_for(fans.INDEPENDENT_TARGETS, demand_pct=100, reason="Missing GPU Memory")
                app._case_fan_status["command_pct"] = 100
                first_callback()
                self.assertIn("Target: 100%   Command: 100%", label.cget("text"))
                self.assertIn("Missing GPU Memory", label.cget("text"))
                active_timers = set(app.root.tk.call("after", "info")) - callbacks_before
                self.assertEqual(len(active_timers), 1)
                probes = []
                app.root.after(0, lambda: probes.append("loop continues"))
                app.root.update()
                self.assertEqual(probes, ["loop continues"])
                self.assertFalse(dialog.winfo_ismapped())
                close = next(child for child in dialog.winfo_children() if isinstance(child, overlay.tk.Button))
                close.invoke()
                self.assertIsNone(app._cooling_dialog)
                self.assertFalse(dialog.winfo_exists())
                self.assertTrue(active_timers.isdisjoint(app.root.tk.call("after", "info")))
                first_callback()
                self.assertEqual(set(app.root.tk.call("after", "info")), callbacks_before)
                app.running = False
                app.show_cooling_status()
                create.assert_called_once()
            self.assertEqual(app.config, config_before)
            app._save_config.assert_not_called()

    def test_rendered_scope_and_firmware_row_follow_current_worker(self):
        with layout_app() as app:
            app.config["case_fans_enabled"] = True
            app.sensor_data = sample(fans=[dict(name=name, id=name, rpm=900) for name in fans.TARGETS])
            app.fan_worker.poll = mock.Mock(return_value=status_for(fans.INDEPENDENT_TARGETS))
            app.update_ui()
            self.assertEqual(app.rows["case_fan_control"].cget("text"), "AUTO 77% · SYS 1/2")
            self.assertEqual(app.rows["case_fan_4"].cget("text"), "900 RPM · Firmware")
            self.assertNotIn("Firmware", app.rows["case_fan_1"].cget("text"))
            self.assertNotIn("Firmware", app.rows["case_fan_2"].cget("text"))
            app.fan_worker.poll.return_value = status_for(fans.TARGETS)
            app.update_ui()
            self.assertEqual(app.rows["case_fan_control"].cget("text"), "AUTO 77% · SYS 1/2/4")
            self.assertEqual(app.rows["case_fan_4"].cget("text"), "900 RPM")

    def test_two_channel_commissioning_is_saved_and_shared_reference_is_preserved(self):
        for existing in (None, {name: 1250 for name in fans.TARGETS}):
            with self.subTest(existing=existing), layout_app() as app:
                reference = {name: 1200 for name in fans.INDEPENDENT_TARGETS}
                if existing:
                    app.config["case_fan_full_rpm"] = existing
                app.fan_worker.poll = mock.Mock(return_value=status_for(
                    fans.INDEPENDENT_TARGETS, verified_full_rpm=reference))
                app._save_config = mock.Mock()
                app.update_ui()
                saved = app.config["case_fan_full_rpm"]
                self.assertEqual(set(saved), set(existing or reference))
                if existing:
                    self.assertEqual(saved[fans.TARGETS[2]], existing[fans.TARGETS[2]])
                else:
                    self.assertEqual(saved, reference)
                    app._save_config.assert_called_once_with()

    def test_full_commissioning_adds_missing_shared_reference_for_next_restart(self):
        with layout_app() as app:
            app.config["case_fan_full_rpm"] = {name: 1200 for name in fans.INDEPENDENT_TARGETS}
            reference = {name: 1250 for name in fans.TARGETS}
            app.fan_worker.poll = mock.Mock(return_value=status_for(fans.TARGETS, verified_full_rpm=reference))
            app._save_config = mock.Mock()
            app.update_ui()
            self.assertEqual(app.config["case_fan_full_rpm"], reference)
            self.assertEqual(app.fan_worker.full_rpm, reference)
            app._save_config.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
