"""B850 target identity, thermal/rotation arbitration and register restoration."""
import json
from pathlib import Path
import tempfile
import sys
from types import SimpleNamespace as NS
import unittest
from unittest import mock

import b850_fans as b850
import case_fans
import hardware_access_guard as access
import overlay
import enable_case_fans


def sample(**changes):
    result = dict(cpu_temp=50, gpu_core_temp=45, gpu_hotspot_temp=45, gpu_memory_temp=59,
                  cpu_fan=1400, cpu_optional_fan=1440, gpu_fan=0)
    result.update(changes)
    return result


class Bus:
    def __enter__(self):
        return self
    def __exit__(self, *_args):
        pass


class Backend:
    def __init__(self):
        self.values = {r: 67 for r in b850.REGISTERS}
        self.values[0x13] = 0x77
        self.values.update({r: 0xC0 for r in b850.MODES})
        self.writes = []
        self.fail_after = None
    def snapshot(self):
        return dict(self.values)
    def write(self, register, value):
        if self.fail_after is not None and len(self.writes) >= self.fail_after:
            self.fail_after = None
            raise OSError("injected partial native write")
        self.writes.append((register, value))
        self.values[register] = value


class B850PolicyTests(unittest.TestCase):
    def demand(self, **changes):
        return b850.synchronized_demand(sample(**changes), 2000, 3442)

    def test_cpu_leads_idle_and_gpu_leads_when_faster(self):
        value, reason, fractions = self.demand()
        self.assertEqual((value, reason), (72, "CPU fans"))
        self.assertEqual(fractions["CPU"]["source"], "RPM estimate")
        value, reason, _ = self.demand(gpu_fan=3100)
        self.assertEqual((value, reason), (91, "GPU fans"))

    def test_actual_duty_precedes_rpm_estimate(self):
        value, reason, fractions = self.demand(cpu_fan_pct=68, cpu_optional_fan_pct=69, gpu_fan_pct=80)
        self.assertEqual((value, reason), (80, "GPU fans"))
        self.assertEqual(fractions["GPU"]["source"], "controller")

    def test_each_thermal_boundary_can_force_full_speed_before_rpm_rises(self):
        for key, temperature in (("cpu_temp", 80), ("gpu_core_temp", 75),
                                 ("gpu_hotspot_temp", 90), ("gpu_memory_temp", 90)):
            with self.subTest(key=key):
                self.assertEqual(self.demand(**{key: temperature})[0], 100)
        self.assertEqual(self.demand(gpu_hotspot_temp=85)[0], 90)
        self.assertEqual(self.demand(gpu_hotspot_temp=84.9)[0], 72)

    def test_unknown_feedback_or_stopped_cpu_does_not_reduce_airflow(self):
        for key, value in (("cpu_temp", None), ("gpu_fan", None), ("cpu_fan", 0),
                           ("gpu_memory_temp", float("nan")), ("cpu_optional_fan", False)):
            with self.subTest(key=key):
                self.assertEqual(self.demand(**{key: value})[0], 100)
        self.assertEqual(b850.synchronized_demand(sample(), None, 3442)[0], 100)
        self.assertEqual(self.demand(cpu_fan=500, cpu_optional_fan=500)[0], b850.MIN_DUTY)


class B850SessionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.journal = Path(self.directory.name) / "recovery.json"
        self.backend = Backend()

    def session(self):
        return b850.B850Session(self.backend, self.journal, Bus)

    def test_only_sys123_written_and_exact_auto_modes_restored(self):
        baseline = self.backend.snapshot()
        session = self.session()
        session.apply(100)
        session.apply(72)
        self.assertTrue(all(r in b850.TARGET_REGISTERS for r, _ in self.backend.writes))
        for r in set(b850.REGISTERS) - set(b850.TARGET_REGISTERS):
            self.assertEqual(self.backend.values[r], baseline[r])
        self.assertEqual(session.restore(), [])
        self.assertEqual(self.backend.snapshot(), baseline)
        self.assertFalse(self.journal.exists())

    def test_partial_write_restores_and_interrupted_session_recovers(self):
        baseline = self.backend.snapshot()
        session = self.session()
        self.backend.fail_after = 3
        with self.assertRaises(OSError):
            session.apply(100)
        self.assertEqual(session.restore(), [])
        self.assertEqual(self.backend.snapshot(), baseline)
        session = self.session()
        session.apply(85)
        resumed = self.session()
        self.assertEqual(resumed.baseline, baseline)
        self.assertFalse(self.journal.exists())

    def test_external_settings_are_preserved_with_recovery_evidence(self):
        session = self.session()
        session.apply(72)
        self.backend.values[b850.DUTIES[1]] = 220
        current = self.backend.snapshot()
        with self.assertRaisesRegex(RuntimeError, "external settings"):
            session.apply(100)
        self.assertTrue(session.restore())
        self.assertEqual(self.backend.values[b850.DUTIES[1]], current[b850.DUTIES[1]])
        for i in (2, 3):
            self.assertEqual(self.backend.values[b850.MODES[i]], 0xC0)
        self.assertTrue(self.journal.exists())
        with self.assertRaisesRegex(RuntimeError, "conflict"):
            self.session()
        self.assertEqual(self.backend.values[b850.DUTIES[1]], current[b850.DUTIES[1]])

    def test_cpu_setting_change_does_not_prevent_owned_case_handback(self):
        session = self.session()
        session.apply(72)
        self.backend.values[b850.DUTIES[0]] = 211
        with self.assertRaises(RuntimeError):
            session.check()
        self.assertEqual(session.restore(), [])
        self.assertEqual(self.backend.values[b850.DUTIES[0]], 211)
        self.assertTrue(all(self.backend.values[b850.MODES[i]] == 0xC0 for i in (1, 2, 3)))

    def test_bad_journal_and_disabled_modes_refuse_without_writes(self):
        for fault in ("disabled", "manual", "bad_journal"):
            self.backend = Backend()
            self.journal.unlink(missing_ok=True)
            if fault == "disabled":
                self.backend.values[0x13] = 1
            elif fault == "manual":
                self.backend.values[b850.MODES[2]] = 0x40
            else:
                self.journal.write_text(json.dumps(dict(profile="foreign")), encoding="utf-8")
            with self.subTest(fault=fault), self.assertRaises(RuntimeError):
                self.session()
            self.assertEqual(self.backend.writes, [])


class B850IntegrationTests(unittest.TestCase):
    def test_restart_recognizes_junction_alias_but_not_unrelated_script(self):
        with mock.patch.object(enable_case_fans.os.path, "samefile", return_value=True):
            self.assertTrue(enable_case_fans.same_overlay_file("F:/GitHub/HeatMap/overlay.py", "C:/old/overlay.py"))
        with mock.patch.object(enable_case_fans.os.path, "samefile", side_effect=FileNotFoundError):
            self.assertFalse(enable_case_fans.same_overlay_file("another-app.py", "C:/old/overlay.py"))

    def test_target_tachs_need_exact_board_chip_channel_and_running_readings(self):
        tachs = [NS(Name=f"Fan #{i + 1}", Identifier=f"{b850.CHIP}/fan/{i}",
                    SensorType="Fan", Value=700) for i in (1, 2, 3)]
        chip = NS(Identifier=b850.CHIP, Sensors=tachs)
        board = NS(HardwareType="Motherboard", Name=b850.BOARD, SubHardware=[chip])
        computer = NS(Hardware=[board])
        self.assertEqual(b850.select_tachs(computer)[1], tachs)
        board.Name = "Gigabyte B850 AORUS ELITE WIFI7"
        with self.assertRaises(RuntimeError):
            b850.select_tachs(computer)
        board.Name = b850.BOARD
        tachs[1].Value = 0
        with self.assertRaises(RuntimeError):
            b850.select_tachs(computer)

    def test_b850_client_separate_profile_and_calibration(self):
        reference = {name: 1300 for name in b850.TARGETS}
        client = case_fans.FanWorkerClient("unused", reference, b850=True)
        self.assertEqual(client.full_rpm, reference)
        self.assertEqual(client.status_policy.profile, b850.PROFILE)
        self.assertEqual(overlay._case_fan_mode(dict(state="active", command_pct=72,
                                                    controlled_channels=list(b850.TARGETS))), "AUTO 72% · SYS 1/2/3")
        with self.assertRaises(ValueError):
            case_fans.FanWorkerClient("unused", b850=True, shared=True)

    def test_gcc_background_allowed_only_in_guarded_independent_scope(self):
        with mock.patch.object(access, "hardware_conflicts", return_value=["gcc.exe"]):
            self.assertEqual(access.require_hardware_access("independent_case_control"), "full")
            with self.assertRaises(access.HardwareAccessConflict):
                access.require_hardware_access("control")
        for conflict in ("atisetup.exe", "hwinfo64.exe", "fancontrol.exe"):
            with mock.patch.object(access, "hardware_conflicts", return_value=["gcc.exe", conflict]):
                with self.assertRaises(access.HardwareAccessConflict):
                    access.require_hardware_access("independent_case_control")


class B850WorkerTests(unittest.TestCase):
    def run_worker(self, *, fault=None, commission=False):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = str(Path(directory.name) / "status.json")
        backend = Backend()
        backend.tachs = [NS(Name=f"Fan #{i + 1}", Identifier=f"{b850.CHIP}/fan/{i}",
                            SensorType="Fan", Value=700) for i in (1, 2, 3)]
        backend.readings = lambda: [dict(name=name, rpm=tach.Value,
            control_pct=round(backend.values[b850.DUTIES[index]] * 100 / 255))
            for index, name, tach in zip((1, 2, 3), b850.TARGETS, backend.tachs)]
        chip = NS(Identifier=b850.CHIP, Sensors=backend.tachs)
        computer = NS(Hardware=[NS(HardwareType="Motherboard", Name=b850.BOARD, SubHardware=[chip])],
                      Open=mock.Mock(), Close=mock.Mock())
        clock = NS(now=100, stopped=False, samples=0)
        class Event:
            def is_set(self):
                return clock.stopped
            def set(self):
                clock.stopped = True
            def wait(self, seconds):
                clock.now += seconds
                for tach in backend.tachs:
                    tach.Value = 1250 if backend.values[b850.DUTIES[1]] == 255 else 850
                stop_at = 170 if fault == "permanent_zero" else 130 if commission else 108
                if clock.now >= stop_at:
                    clock.stopped = True
        def read(_computer, **_kwargs):
            clock.samples += 1
            if fault == "startup_zero" and clock.samples == 1:
                backend.tachs[0].Value = 0
            elif fault == "startup_zero":
                backend.tachs[0].Value = 700
            elif fault == "permanent_zero":
                backend.tachs[0].Value = 0
            data = sample(cpu_fan_id=f"{b850.CHIP}/fan/0", cpu_optional_fan_id=f"{b850.CHIP}/fan/4",
                          gpu_name="AMD Radeon RX 7900 XT", fans=[dict(name="System Fan #1", rpm=700 + clock.samples)])
            if fault == "identity":
                data["cpu_fan_id"] = "foreign"
            elif fault == "temperature":
                data["gpu_memory_temp"] = float("nan")
            elif fault == "read_failure" and clock.now >= 103:
                raise OSError("sensor read failed")
            elif not commission and clock.now >= 104:
                data["gpu_hotspot_temp"] = 90
            return data
        real_session = b850.B850Session
        owner = NS(create_time=lambda: 1, is_running=lambda: True)
        heartbeat = NS(listen=mock.Mock(), expired=lambda: False)
        with (mock.patch.dict(sys.modules, clr=NS(AddReference=mock.Mock())),
              mock.patch.object(overlay, "_is_admin", return_value=True),
              mock.patch.object(overlay, "_runtime_dll_errors", return_value=[]),
              mock.patch.object(overlay, "load_config_result", return_value=(dict(cpu_fan_reference_rpm=2000, gpu_fan_max_rpm=3442), None)),
              mock.patch.object(overlay, "read_sensors", side_effect=read),
              mock.patch("shared_fans.make_shared_computer", return_value=computer),
              mock.patch.object(b850, "RegisterBackend", return_value=backend),
              mock.patch.object(b850, "B850Session", side_effect=lambda a, b: real_session(a, b, Bus)),
              mock.patch.object(b850, "IsaBus", Bus),
              mock.patch.object(b850, "require_hardware_access"),
              mock.patch.object(b850.psutil, "Process", return_value=owner),
              mock.patch.object(b850.threading, "Event", Event),
              mock.patch.object(b850.threading, "Thread"),
              mock.patch.object(b850.fan_common, "OwnerHeartbeat", return_value=heartbeat),
              mock.patch.object(b850.time, "monotonic", side_effect=lambda: clock.now),
              mock.patch.object(case_fans, "write_status") as publish,
              mock.patch.object(case_fans, "write_terminal_status") as terminal):
            result = b850.worker(path, 7, 1, {name: 1250 for name in b850.TARGETS}, commission)
        computer.Close.assert_called_once_with()
        return result, publish, terminal.call_args.kwargs, backend

    def test_normal_start_tracks_cpu_then_hot_gpu_and_restores(self):
        result, publish, terminal, backend = self.run_worker()
        self.assertEqual(result, 0)
        commands = [call.kwargs["command_pct"] for call in publish.call_args_list if "command_pct" in call.kwargs]
        self.assertEqual(commands[0], 72)
        self.assertIn(100, commands)
        self.assertTrue(terminal["restore_confirmed"])
        self.assertEqual(terminal["restore_errors"], [])
        self.assertTrue(all(backend.values[r] == 0xC0 for r in b850.MODES))

    def test_invalid_startup_identity_or_temperature_sends_no_commands(self):
        for fault in ("identity", "temperature"):
            with self.subTest(fault=fault):
                result, _publish, terminal, backend = self.run_worker(fault=fault)
                self.assertEqual(result, 1)
                self.assertEqual(backend.writes, [])
                self.assertFalse(terminal["control_attempted"])

    def test_lost_native_reads_restore_owned_channels(self):
        result, _publish, terminal, _backend = self.run_worker(fault="read_failure")
        self.assertEqual(result, 1)
        self.assertTrue(terminal["control_attempted"])
        self.assertTrue(terminal["restore_confirmed"])

    def test_transient_startup_zero_waits_then_arms_without_bypassing_tach(self):
        result, publish, terminal, backend = self.run_worker(fault="startup_zero")
        self.assertEqual(result, 0)
        waiting = [call for call in publish.call_args_list if call.kwargs.get("phase") == "waiting"]
        self.assertTrue(waiting)
        self.assertFalse(waiting[0].kwargs["control_attempted"])
        self.assertTrue(backend.writes)
        self.assertTrue(terminal["restore_confirmed"])

    def test_persistent_startup_zero_times_out_without_commands(self):
        result, _publish, terminal, backend = self.run_worker(fault="permanent_zero")
        self.assertEqual(result, 1)
        self.assertFalse(terminal["control_attempted"])
        self.assertEqual(backend.writes, [])

    def test_explicit_commission_checks_full_and_minimum_endpoints(self):
        result, publish, terminal, _backend = self.run_worker(commission=True)
        self.assertEqual(result, 0)
        commands = [call.kwargs["command_pct"] for call in publish.call_args_list if "command_pct" in call.kwargs]
        self.assertEqual(commands[0], 100)
        self.assertIn(60, commands)
        active = [call for call in publish.call_args_list if call.args[1] == "active"]
        self.assertTrue(active)
        self.assertEqual(set(active[-1].kwargs["verified_full_rpm"]), set(b850.TARGETS))
        self.assertTrue(terminal["restore_confirmed"])


if __name__ == "__main__":
    unittest.main()
