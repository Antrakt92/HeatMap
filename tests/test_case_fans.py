import io
import json
import os
import tempfile
import time
import unittest
from types import SimpleNamespace as NS
from unittest import mock

import case_fans as fans
import fan_common
from hardware_access_guard import HardwareAccessConflict


def fixture():
    sensors = []
    controls = []
    chip_sensors = {"/lpc/it8688e/0": [], "/lpc/it8792e/0": []}
    for name in (*fans.TARGETS, "CPU Fan", "System Fan #5 / Pump", "System Fan #6 / Pump"):
        chip, index = fans.CHANNELS.get(name, ("/lpc/it8792e/0", 0 if "#5" in name else 1))
        if name == "CPU Fan":
            chip, index = "/lpc/it8688e/0", 0
        sensor = NS(Name=name, SensorType="Control", Value=100 if "Pump" in name else None)
        sensor.Identifier = f"{chip}/control/{index}"
        control = mock.Mock(MinSoftwareValue=0, MaxSoftwareValue=100)
        control.SetSoftware.side_effect = lambda value, s=sensor: setattr(s, "Value", value)
        original = sensor.Value
        control.SetDefault.side_effect = lambda s=sensor, v=original: setattr(s, "Value", v)
        sensor.Control = control
        controls.append(control)
        chip_sensors[chip].extend([sensor, NS(Name=name, SensorType="Fan", Value=800,
                                            Identifier=f"{chip}/fan/{index}")])
    subs = [NS(Identifier=chip, Sensors=items, Close=mock.Mock(), Update=mock.Mock())
            for chip, items in chip_sensors.items()]
    report = "LPC IT87XX\nChip ID: 0x8688\nEnvironment Controller Registers Bank 0\n\n"
    report += "      " + " ".join(f"{index:02X}" for index in range(16)) + "\n\n"
    report += "\n".join(f" {row:02X}   " + " ".join("07" if row + col == 0x13 else "00"
                                               for col in range(16)) for row in range(0, 0xB0, 16))
    subs[0].GetReport = mock.Mock(return_value=report + "\n\nGPIO Registers\n 00\n")
    board = NS(HardwareType="Motherboard", Model="B550_AORUS_PRO_AC", SubHardware=subs)
    return NS(Hardware=[board], Open=mock.Mock(), Close=mock.Mock()), controls


class CaseFanTests(unittest.TestCase):
    def test_exact_headers_only_and_restore_original_values(self):
        computer, controls = fixture()
        session = fans.CaseFanSession(fans.select_controls(computer))
        before = session.readings()
        session.apply(100)
        for c in controls[:2]:
            c.SetSoftware.assert_called_once_with(100)
        for c in controls[2:]:
            c.SetSoftware.assert_not_called()
        self.assertEqual(session.restore(), [])
        self.assertEqual(fans.verify_restore(before, session.readings()), [])
        self.assertEqual(session.restore(), [])

    def test_wrong_board_missing_duplicate_and_stopped_rejected(self):
        for fault in ("model", "missing", "duplicate", "stopped"):
            computer, _ = fixture()
            board = computer.Hardware[0]
            sensors = board.SubHardware[0].Sensors
            if fault == "model":
                board.Model = "B550_AORUS_ELITE"
            elif fault == "missing":
                sensors.pop(0)
            elif fault == "duplicate":
                sensors.append(sensors[0])
            elif fault == "stopped":
                sensors[1].Value = 0
            with self.subTest(fault=fault), self.assertRaises(RuntimeError):
                fans.select_controls(computer)

    def test_pythonnet_interface_proxy_uses_concrete_board_model(self):
        computer, _ = fixture()
        board = computer.Hardware[0]
        computer.Hardware[0] = NS(HardwareType="Motherboard", SubHardware=board.SubHardware,
                                  __implementation__=board)
        self.assertEqual(len(fans.select_controls(computer)), 2)

    def test_partial_native_write_restores_even_the_channel_that_raised(self):
        computer, controls = fixture()
        session = fans.CaseFanSession(fans.select_controls(computer))
        controls[1].SetSoftware.side_effect = RuntimeError("partial native failure")
        with self.assertRaises(RuntimeError):
            session.apply(100)
        self.assertEqual(session.restore(), [])
        controls[0].SetDefault.assert_called_once()
        controls[1].SetDefault.assert_called_once()
        controls[2].SetDefault.assert_not_called()

    def test_restore_failure_retains_channel_for_retry(self):
        computer, controls = fixture()
        session = fans.CaseFanSession(fans.select_controls(computer))
        session.apply(100)
        controls[0].SetDefault.side_effect = [RuntimeError("bus"), None]
        self.assertEqual(len(session.restore()), 1)
        self.assertEqual(len(session.touched), 1)
        self.assertEqual(session.restore(), [])

    def test_invalid_commands_cannot_touch_hardware(self):
        computer, controls = fixture()
        session = fans.CaseFanSession(fans.select_controls(computer))
        for bad in (0, 59, 101, float("nan"), True, None):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                session.apply(bad)
        for c in controls:
            c.SetSoftware.assert_not_called()

    def test_command_readback_alone_is_not_success(self):
        baseline = [dict(name="SYS1", rpm=800, control_pct=None)]
        for rpm, pct in ((800, 100), (1200, None), (0, 100)):
            with self.subTest(rpm=rpm, pct=pct), self.assertRaises(RuntimeError):
                fans.verify_full_airflow(baseline, [dict(name="SYS1", rpm=rpm, control_pct=pct)])
        self.assertIsNone(fans.verify_full_airflow(
            baseline, [dict(name="SYS1", rpm=1200, control_pct=100)]))

    def test_silent_restore_failure_detected(self):
        self.assertTrue(fans.verify_restore([dict(name="SYS1", control_pct=None)], [dict(name="SYS1", control_pct=100)]))
        self.assertTrue(fans.verify_restore([dict(name="SYS1", control_pct=59)], [dict(name="SYS1", control_pct=100)]))

    def test_restart_accepts_previously_verified_full_rpm_without_new_acceleration(self):
        baseline = [dict(name=name, rpm=1200, control_pct=None) for name in fans.TARGETS]
        readings = [dict(name=name, rpm=1190, control_pct=100) for name in fans.TARGETS]
        with self.assertRaises(RuntimeError):
            fans.verify_full_airflow(baseline, readings)
        fans.verify_full_airflow(baseline, readings, {name: 1200 for name in fans.TARGETS})
        readings[0]["rpm"] = 800
        with self.assertRaises(RuntimeError):
            fans.verify_full_airflow(baseline, readings, {name: 1200 for name in fans.TARGETS})

    def test_invalid_reference_or_incomplete_readings_never_passes_verification(self):
        for value in (None, {}, {fans.TARGETS[0]: 1200}, {name: float("nan") for name in fans.TARGETS}):
            self.assertIsNone(fans.full_rpm_reference(value))
        with self.assertRaises(RuntimeError):
            fans.verify_full_airflow([], [])
        self.assertTrue(fans.verify_restore([], []))

    def test_completed_status_does_not_expire_into_false_error(self):
        with tempfile.TemporaryDirectory() as directory:
            client = fans.FanWorkerClient(directory)
            client.status_path = os.path.join(directory, "state.json")
            client.started = 5
            client.process = mock.Mock(pid=7)
            client.process.poll.return_value = 0
            for state in ("stopped", "error"):
                with open(client.status_path, "w") as stream:
                    json.dump(dict(profile=fans.PROFILE, pid=7, time=10, state=state, reason="original reason"), stream)
                with mock.patch.object(fans.time, "time", return_value=100):
                    self.assertEqual(client.poll()["reason"], "original reason")

    def test_windows_venv_redirector_accepts_descendant_worker_pid(self):
        with tempfile.TemporaryDirectory() as directory:
            client = fans.FanWorkerClient(directory)
            client.status_path = os.path.join(directory, "state.json")
            client.started = 90
            client.process = mock.Mock(pid=7, stdin=io.StringIO())
            client.process.poll.return_value = None
            child = mock.Mock()
            child.parents.return_value = [NS(pid=7)]
            with open(client.status_path, "w") as stream:
                json.dump(dict(profile=fans.PROFILE, pid=8, time=100, state="active", command_pct=100), stream)
            with mock.patch.object(fans.time, "time", return_value=100), \
                 mock.patch.object(fans.psutil, "Process", return_value=child):
                self.assertEqual(client.poll()["state"], "active")
            self.assertEqual(client.worker_pid, 8)

    def test_malformed_status_never_looks_like_active_control(self):
        with tempfile.TemporaryDirectory() as directory:
            client = fans.FanWorkerClient(directory)
            client.status_path = os.path.join(directory, "state.json")
            client.started = 90
            client.process = mock.Mock(pid=7, stdin=io.StringIO())
            client.process.poll.return_value = None
            for fields in (dict(state="unknown", pid=7), dict(state="active", pid=None, command_pct=80),
                           dict(state="active", pid=7, command_pct=float("nan"))):
                with open(client.status_path, "w") as stream:
                    json.dump(dict(profile=fans.PROFILE, time=100, **fields), stream)
                with mock.patch.object(fans.time, "time", return_value=100):
                    self.assertEqual(client.poll()["state"], "error")

    def test_unwritable_status_directory_is_reported_without_crashing_ui(self):
        client = fans.FanWorkerClient(".")
        with mock.patch.object(fans.os, "makedirs", side_effect=PermissionError("denied")), \
             mock.patch.object(fans.subprocess, "Popen") as launch:
            client.start()
        launch.assert_not_called()
        self.assertEqual(client.poll()["state"], "error")

    def test_client_rejects_dead_stale_wrong_pid_and_future_active_status(self):
        with tempfile.TemporaryDirectory() as directory:
            client = fans.FanWorkerClient(directory)
            client.status_path = os.path.join(directory, "state.json")
            client.started = 100
            client.process = mock.Mock(pid=7, stdin=io.StringIO())
            for pid, stamp, exitcode in ((7, 100, 1), (7, 80, None), (8, 100, None), (7, 110, None)):
                with open(client.status_path, "w") as stream:
                    json.dump(dict(profile=fans.PROFILE, pid=pid, time=stamp, state="active", command_pct=80), stream)
                client.process.poll.return_value = exitcode
                with mock.patch.object(fans.time, "time", return_value=100):
                    self.assertEqual(client.poll()["state"], "error")

    def test_stop_uses_eof_without_killing_controller(self):
        client = fans.FanWorkerClient(".")
        client.process = mock.Mock(stdin=io.StringIO())
        client.stop()
        self.assertTrue(client.process.stdin.closed)
        client.process.kill.assert_not_called()
        client.process.terminate.assert_not_called()

    def test_hardware_conflict_before_open_before_write_or_midrun_stops_safely(self):
        import overlay
        for phase in ("before_open", "before_write", "midrun"):
            with self.subTest(phase=phase):
                computer, controls = fixture()
                owner = mock.Mock()
                owner.create_time.return_value = 1
                owner.is_running.return_value = True
                completed_checks = {"before_open": 0, "before_write": 3, "midrun": 4}[phase]
                message = "Other hardware monitoring tools are running: hwinfo64.exe. Restart HeatMap."
                checks = [None] * completed_checks + [HardwareAccessConflict(message)]
                modules = {"clr": mock.Mock(), "LibreHardwareMonitor": mock.Mock(),
                           "LibreHardwareMonitor.Hardware": NS(Computer=lambda: computer)}
                with (
                    mock.patch.dict("sys.modules", modules),
                    mock.patch.object(fans, "make_shared_computer", return_value=computer),
                    mock.patch.object(overlay, "_is_admin", return_value=True),
                    mock.patch.object(overlay, "_runtime_dll_errors", return_value=[]),
                    mock.patch.object(fans.psutil, "Process", return_value=owner),
                    mock.patch.object(fans.threading, "Thread"),
                    mock.patch.object(fans, "require_hardware_access", side_effect=checks) as guard,
                    mock.patch.object(overlay, "read_sensors", return_value=dict(
                        cpu_temp=50, gpu_core_temp=45, gpu_hotspot_temp=60, gpu_memory_temp=60)) as read,
                    mock.patch.object(fans, "write_status") as publish,
                ):
                    self.assertEqual(fans.worker("unused-mocked.json", 7, 1), 1)
                self.assertEqual(guard.call_count, completed_checks + 1)
                self.assertEqual(computer.Open.call_count, 0 if phase == "before_open" else 1)
                self.assertEqual(read.call_count, 0 if phase == "before_open" else 1)
                for control in controls[:2]:
                    if phase == "midrun":
                        control.SetSoftware.assert_called_once_with(100.0)
                        control.SetDefault.assert_called_once_with()
                    else:
                        control.SetSoftware.assert_not_called()
                        control.SetDefault.assert_not_called()
                for untouched in controls[2:]:
                    untouched.SetSoftware.assert_not_called()
                    untouched.SetDefault.assert_not_called()
                computer.Close.assert_called_once_with()
                self.assertEqual(publish.call_args.args[1], "error")
                self.assertEqual(publish.call_args.kwargs["reason"], message)
                if phase == "midrun":
                    self.assertTrue(publish.call_args.kwargs["restore_confirmed"])

    def test_process_inventory_failure_is_reported_before_hardware_open(self):
        import overlay
        computer, _ = fixture()
        owner = mock.Mock()
        owner.create_time.return_value = 1
        modules = {"clr": mock.Mock(), "LibreHardwareMonitor": mock.Mock(),
                   "LibreHardwareMonitor.Hardware": NS(Computer=lambda: computer)}
        with (
            mock.patch.dict("sys.modules", modules),
            mock.patch.object(fans, "make_shared_computer", return_value=computer),
            mock.patch.object(overlay, "_is_admin", return_value=True),
            mock.patch.object(overlay, "_runtime_dll_errors", return_value=[]),
            mock.patch.object(fans.psutil, "Process", return_value=owner),
            mock.patch.object(fans.psutil, "process_iter", side_effect=fans.psutil.AccessDenied()),
            mock.patch.object(fans.threading, "Thread"),
            mock.patch.object(fans, "write_status") as publish,
        ):
            self.assertEqual(fans.worker("unused-mocked.json", 7, 1), 1)
        computer.Open.assert_not_called()
        self.assertIn("Cannot verify which hardware monitoring tools are running", publish.call_args.kwargs["reason"])

    def test_sensor_signature_ignores_identity_and_nan_instability(self):
        sample = dict(cpu_temp=50, gpu_core_temp=45, gpu_hotspot_temp=60, gpu_memory_temp=60,
                      fans=[dict(id="fan-a", rpm=800)])
        self.assertEqual(fans._sensor_signature(dict(sample)), fans._sensor_signature(dict(sample)))
        self.assertEqual(fans._sensor_signature(dict(sample, cpu_temp=float("nan"))),
                         fans._sensor_signature(dict(sample, cpu_temp=float("nan"))))
        self.assertNotEqual(fans._sensor_signature(sample),
                            fans._sensor_signature(dict(sample, cpu_temp=51)))
        self.assertNotEqual(fans._sensor_signature(sample),
                            fans._sensor_signature(dict(sample, fans=[dict(id="fan-a", rpm=801)])))

    def run_frozen_worker(self, frozen, stop_at_reads=12, tick=0.0):
        import overlay
        import thermal_policy
        computer, controls = fixture()
        owner = mock.Mock()
        owner.create_time.return_value = 1
        owner.is_running.return_value = True
        clock = [100.0]
        reads = [0]

        def read(_computer):
            reads[0] += 1
            if reads[0] >= stop_at_reads:
                owner.is_running.return_value = False
            return frozen

        stop = mock.Mock()
        stop.is_set.return_value = False

        def wait(seconds):
            clock[0] += tick
            return False

        stop.wait.side_effect = wait
        updates = []
        original_update = thermal_policy.CaseAirflowPolicy.update

        def spy(self, data, now):
            updates.append((now, len(self.history)))
            return original_update(self, data, now)

        reports = []
        original_write = fans.write_status

        def publish(path, state, **details):
            reports.append(dict(state=state, **details))
            return original_write(path, state, **details)

        real_check = fans._check_case_owner

        def check(stop_event, owner_process, heartbeat):
            # Production refreshes the watchdog on every owner poll; without
            # this the frozen mock heartbeat would expire together with the
            # sensor stream and mask the stale-sensor fault under test.
            heartbeat.last_seen = clock[0]
            return real_check(stop_event, owner_process, heartbeat)

        modules = {"clr": mock.Mock(), "LibreHardwareMonitor": mock.Mock(),
                   "LibreHardwareMonitor.Hardware": NS(Computer=lambda: computer)}
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict("sys.modules", modules), \
             mock.patch.object(fans, "make_shared_computer", return_value=computer), \
             mock.patch.object(overlay, "_is_admin", return_value=True), \
             mock.patch.object(overlay, "_runtime_dll_errors", return_value=[]), \
             mock.patch.object(fans.psutil, "Process", return_value=owner), \
             mock.patch.object(fans.psutil, "process_iter", return_value=[]), \
             mock.patch.object(fans.threading, "Thread"), \
             mock.patch.object(fans.threading, "Event", return_value=stop), \
             mock.patch.object(fans.time, "monotonic", side_effect=lambda: clock[0]), \
             mock.patch.object(fans, "_check_case_owner", side_effect=check), \
             mock.patch.object(fans.CaseAirflowPolicy, "update", spy), \
             mock.patch.object(fans, "write_status", side_effect=publish), \
             mock.patch.object(overlay, "read_sensors", side_effect=read):
            path = os.path.join(directory, "status.json")
            result = fans.worker(path, 7, 1,
                                 full_rpm={name: 1200 for name in fans.INDEPENDENT_TARGETS})
        return result, controls, reports, updates, reads[0]

    def test_frozen_sensor_snapshot_never_earns_cooling_credit(self):
        # One dict object returned for every sample: only the first may feed
        # the airflow history, and no command may fall below the last one.
        frozen = dict(cpu_temp=50, gpu_core_temp=45, gpu_hotspot_temp=60, gpu_memory_temp=60)
        result, controls, reports, updates, total_reads = self.run_frozen_worker(frozen)
        self.assertEqual(result, 0)
        self.assertGreaterEqual(total_reads, 10)
        self.assertEqual(len(updates), 1)
        commands = [call.args[0] for call in controls[0].SetSoftware.call_args_list]
        self.assertTrue(commands)
        self.assertTrue(all(command >= commands[0] for command in commands))
        for control in controls[:2]:
            self.assertEqual([call.args[0] for call in control.SetSoftware.call_args_list], commands)
        self.assertFalse(any("assist" in report.get("reason", "") for report in reports))

    def test_transient_missing_command_feedback_rides_through(self):
        result, controls, reports = self.run_feedback_worker({2: None, 3: None})
        self.assertEqual(result, 0)
        self.assertTrue(any("command feedback unavailable" in report.get("reason", "")
                            for report in reports))
        for control in controls[:2]:
            control.SetDefault.assert_called_once()

    def test_persistent_missing_command_feedback_faults_with_restore(self):
        result, controls, reports = self.run_feedback_worker(
            {read: None for read in range(2, 10 ** 6)}, stop_at_reads=10 ** 9)
        self.assertEqual(result, 1)
        self.assertIn("command feedback unavailable", reports[-1]["reason"])
        self.assertTrue(reports[-1]["restore_confirmed"])
        for control in controls[:2]:
            control.SetDefault.assert_called_once()

    def run_feedback_worker(self, script, stop_at_reads=12, tick=2.0):
        """Frozen sensors with scripted per-read control_pct overrides.

        script maps 1-based overlay.read_sensors counts to a control_pct value
        (None = missing feedback); unmapped reads keep the hardware value.
        """
        import overlay
        import thermal_policy
        computer, controls = fixture()
        owner = mock.Mock()
        owner.create_time.return_value = 1
        owner.is_running.return_value = True
        clock = [100.0]
        reads = [0]
        frozen = dict(cpu_temp=50, gpu_core_temp=45, gpu_hotspot_temp=60, gpu_memory_temp=60)

        def read(_computer):
            reads[0] += 1
            if reads[0] >= stop_at_reads:
                owner.is_running.return_value = False
            # Advance temperatures slightly so the freshness signature never
            # goes stale: only the scripted control_pct may look frozen.
            return dict(frozen, cpu_temp=50 + reads[0] * 0.1)

        stop = mock.Mock()
        stop.is_set.return_value = False

        def wait(seconds):
            clock[0] += tick
            return False

        stop.wait.side_effect = wait
        original_readings = fans.CaseFanSession.readings

        def readings(session):
            result = original_readings(session)
            if reads[0] in script:
                for fan in result:
                    if fan["name"] in fans.INDEPENDENT_TARGETS:
                        fan["control_pct"] = script[reads[0]]
            return result

        reports = []
        original_write = fans.write_status

        def publish(path, state, **details):
            reports.append(dict(state=state, **details))
            return original_write(path, state, **details)

        real_check = fans._check_case_owner

        def check(stop_event, owner_process, heartbeat):
            heartbeat.last_seen = clock[0]
            return real_check(stop_event, owner_process, heartbeat)

        modules = {"clr": mock.Mock(), "LibreHardwareMonitor": mock.Mock(),
                   "LibreHardwareMonitor.Hardware": NS(Computer=lambda: computer)}
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict("sys.modules", modules), \
             mock.patch.object(fans, "make_shared_computer", return_value=computer), \
             mock.patch.object(overlay, "_is_admin", return_value=True), \
             mock.patch.object(overlay, "_runtime_dll_errors", return_value=[]), \
             mock.patch.object(fans.psutil, "Process", return_value=owner), \
             mock.patch.object(fans.psutil, "process_iter", return_value=[]), \
             mock.patch.object(fans.threading, "Thread"), \
             mock.patch.object(fans.threading, "Event", return_value=stop), \
             mock.patch.object(fans.time, "monotonic", side_effect=lambda: clock[0]), \
             mock.patch.object(fans, "_check_case_owner", side_effect=check), \
             mock.patch.object(fans.CaseFanSession, "readings", readings), \
             mock.patch.object(fans, "write_status", side_effect=publish), \
             mock.patch.object(overlay, "read_sensors", side_effect=read):
            path = os.path.join(directory, "status.json")
            result = fans.worker(path, 7, 1,
                                 full_rpm={name: 1200 for name in fans.INDEPENDENT_TARGETS})
        return result, controls, reports

    def test_frozen_gap_snapshot_holds_curves_until_stale_degradation(self):
        # A frozen Hotspot-Core split must not bypass the ten-second gap hold
        # that fresh samples get in airflow.update: identical repeats hold the
        # temperature curves until stale degradation takes over.
        gap = dict(cpu_temp=50, gpu_core_temp=45, gpu_hotspot_temp=85, gpu_memory_temp=60)
        result, controls, reports, updates, total_reads = self.run_frozen_worker(gap)
        self.assertEqual(result, 0)
        self.assertGreaterEqual(total_reads, 10)
        self.assertEqual(len(updates), 1)
        commands = [call.args[0] for call in controls[0].SetSoftware.call_args_list]
        self.assertTrue(commands)
        self.assertLess(commands[0], 100)
        self.assertTrue(all(command == commands[0] for command in commands))
        self.assertFalse(any("Large GPU hotspot gap" in report.get("reason", "")
                             for report in reports))
        self.assertFalse(any("Stale case sensor readings" in report.get("reason", "")
                             for report in reports))

    def test_frozen_sensor_snapshot_degrades_then_raises_terminal_fault(self):
        # The same frozen dict for >6 s forces full airflow; past 15 s the
        # worker must fail loudly instead of holding the last command forever.
        frozen = dict(cpu_temp=50, gpu_core_temp=45, gpu_hotspot_temp=60, gpu_memory_temp=60)
        result, controls, reports, _updates, total_reads = self.run_frozen_worker(
            frozen, stop_at_reads=10 ** 9, tick=2.0)
        self.assertEqual(result, 1)
        self.assertGreaterEqual(total_reads, 10)
        self.assertIn("Case sensor readings stopped updating", reports[-1]["reason"])
        degraded = [report for report in reports if report.get("reason") ==
                    "Stale case sensor readings: full airflow"]
        self.assertTrue(degraded)
        self.assertTrue(reports[-1]["restore_confirmed"])
        for control in controls[:2]:
            control.SetDefault.assert_called_once()

    def test_worker_restores_after_read_or_status_failure_and_after_owner_dies(self):
        import overlay
        for failure in ("read", "write", None):
            computer, controls = fixture()
            owner = mock.Mock()
            owner.create_time.return_value = 1
            # Owner dies after takeover, independent of how often safety guards run.
            owner.is_running.side_effect = (lambda: not controls[0].SetSoftware.called) if not failure else None
            owner.is_running.return_value = True
            modules = {"clr": mock.Mock(), "LibreHardwareMonitor": mock.Mock(),
                       "LibreHardwareMonitor.Hardware": NS(Computer=lambda: computer)}
            write_status = fans.write_status
            def publish(path, state, **details):
                if failure == "write" and state == "checking":
                    raise PermissionError("status remains locked after retries")
                return write_status(path, state, **details)
            with tempfile.TemporaryDirectory() as directory, mock.patch.dict("sys.modules", modules), \
                 mock.patch.object(fans, "make_shared_computer", return_value=computer), \
                 mock.patch.object(overlay, "_is_admin", return_value=True), \
                 mock.patch.object(overlay, "_runtime_dll_errors", return_value=[]), \
                 mock.patch.object(fans.psutil, "process_iter", return_value=[]), \
                 mock.patch.object(fans.psutil, "Process", return_value=owner), \
                 mock.patch.object(fans.threading, "Thread"), \
                 mock.patch.object(fans, "write_status", side_effect=publish), \
                 mock.patch.object(overlay, "read_sensors", side_effect=[
                     dict(cpu_temp=50, gpu_core_temp=45, gpu_hotspot_temp=60, gpu_memory_temp=60),
                     RuntimeError("read failed") if failure == "read" else {}]):
                path = os.path.join(directory, "status.json")
                result = fans.worker(path, 7, 1)
                self.assertEqual(result, 1 if failure else 0)
                for control in controls[:2]:
                    control.SetDefault.assert_called_once()
                computer.Close.assert_called_once()
                with fans.open_status_file(path) as stream:
                    status = json.load(stream)
                self.assertTrue(status["restore_confirmed"])
                self.assertEqual(status["state"], "error" if failure else "stopped")

    def test_main_failure_report_validates_as_error(self):
        argv = ["case_fans.py", "--status", "unused.json",
                "--owner-pid", "7", "--owner-created", "1"]
        written = {}

        def write(path, state, **details):
            written.update(state=state, **details)

        with (mock.patch.object(fans.sys, "argv", argv),
              mock.patch.object(fans, "WorkerMutex", side_effect=RuntimeError("mutex held")),
              mock.patch.object(fans, "write_status", side_effect=write)):
            self.assertEqual(fans.main(), 1)
        self.assertEqual(written["state"], "error")
        self.assertEqual(written["profile"], fans.PROFILE)
        self.assertIn("mutex held", written["reason"])
        now = time.time()
        reason, _pid = fan_common.check_status(
            fans.CASE_STATUS_POLICY, dict(written), exited=True,
            worker_pid=None, process_pid=os.getpid(), started=now - 1, now=now)
        self.assertIsNone(reason)


if __name__ == "__main__":
    unittest.main()
