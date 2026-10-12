"""Regressions for case fan takeover, independent feedback and recovery."""
from contextlib import nullcontext
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest import mock

import b850_fans as b850
import case_fans
import overlay
from test_b850_case_fans import Backend, sample


class CaseCouplingWorkerTests(unittest.TestCase):
    def run_worker(self, *, source=None, rpm=700, duration=8, read_delay_at=None,
                   interrupted=False, commission=False, minimum_rpm=850, journal_error=False):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = str(Path(directory.name) / "status.json")
        backend = Backend()
        backend.journal = Path(directory.name) / "b850-recovery.json"
        if interrupted:
            old_session = b850.B850Session(backend, backend.journal, nullcontext)
            old_session.apply(90)
            backend.writes.clear()
        backend.tachs = [NS(Name=f"Fan #{i + 1}", Identifier=f"{b850.CHIP}/fan/{i}",
                            SensorType="Fan", Value=rpm) for i in (1, 2, 3)]
        backend.readings = lambda: [dict(name=name, rpm=tach.Value,
            control_pct=round(backend.values[b850.DUTIES[index]] * 100 / 255))
            for index, name, tach in zip((1, 2, 3), b850.TARGETS, backend.tachs)]
        chip = NS(Identifier=b850.CHIP, Sensors=backend.tachs)
        computer = NS(Hardware=[NS(HardwareType="Motherboard", Name=b850.BOARD, SubHardware=[chip])],
                      Open=mock.Mock(), Close=mock.Mock())
        clock = NS(now=100.0, stopped=False, samples=0, expired=False)
        class Event:
            def is_set(self):
                return clock.stopped
            def set(self):
                clock.stopped = True
            def wait(self, seconds):
                clock.now += seconds
                if commission:
                    for tach in backend.tachs:
                        tach.Value = (850 if backend.values[b850.MODES[1]] & 0x80 else
                                      1250 if backend.values[b850.DUTIES[1]] == 255 else minimum_rpm)
                if clock.now >= 101 + duration:
                    clock.stopped = True
        def read(_computer, **_kwargs):
            clock.samples += 1
            if clock.samples == read_delay_at:
                clock.now += 20
                clock.expired = True
            data = sample(cpu_fan_id=f"{b850.CHIP}/fan/0", cpu_optional_fan_id=f"{b850.CHIP}/fan/4",
                          gpu_name="AMD Radeon RX 7900 XT", gpu_id="/gpu-amd/0",
                          gpu_name_match_count=1, fans=[])
            if source:
                data.update(source(clock.samples, clock.now))
            return data
        real_session = b850.B850Session
        real_journal_probe = b850.recovery_journal_exists
        def journal_probe(path):
            if journal_error:
                raise PermissionError("journal access denied")
            return real_journal_probe(path)
        owner = NS(create_time=lambda: 1, is_running=lambda: True)
        heartbeat = NS(listen=mock.Mock(), expired=lambda: clock.expired)
        with (mock.patch.dict(sys.modules, clr=NS(AddReference=mock.Mock())),
              mock.patch.object(overlay, "_is_admin", return_value=True),
              mock.patch.object(overlay, "_runtime_dll_errors", return_value=[]),
              mock.patch.object(overlay, "load_config_result", return_value=(dict(cpu_fan_reference_rpm=2000, gpu_fan_max_rpm=3442), None)),
              mock.patch.object(overlay, "read_sensors", side_effect=read),
              mock.patch("shared_fans.make_shared_computer", return_value=computer),
              mock.patch.object(b850, "RegisterBackend", return_value=backend),
              mock.patch.object(b850, "B850Session", side_effect=lambda a, b: real_session(a, b, nullcontext)),
              mock.patch.object(b850, "recovery_journal_exists", side_effect=journal_probe),
              mock.patch.object(b850, "IsaBus", nullcontext),
              mock.patch.object(b850, "require_hardware_access"),
              mock.patch.object(b850.psutil, "Process", return_value=owner),
              mock.patch.object(b850.threading, "Event", Event),
              mock.patch.object(b850.threading, "Thread"),
              mock.patch.object(b850.fan_common, "OwnerHeartbeat", return_value=heartbeat),
              mock.patch.object(b850.time, "monotonic", side_effect=lambda: clock.now),
              mock.patch.object(case_fans, "write_status") as publish,
              mock.patch.object(case_fans, "write_terminal_status", return_value=None) as terminal):
            result = b850.worker(path, 7, 1, {name: 1250 for name in b850.TARGETS}, commission)
        return result, publish, terminal.call_args.kwargs, backend

    def commands(self, publish):
        return [call.kwargs["command_pct"] for call in publish.call_args_list if "command_pct" in call.kwargs]

    def test_startup_uses_measured_fraction_instead_of_old_manual_register(self):
        result, publish, terminal, _backend = self.run_worker(rpm=1125)
        self.assertEqual(result, 0)
        self.assertEqual(self.commands(publish)[0], 90)
        self.assertTrue(terminal["restore_confirmed"])

    def test_same_named_gpu_switch_stops_and_restores_existing_case_owner(self):
        result, _publish, terminal, _backend = self.run_worker(
            source=lambda count, now: {"gpu_id": "/gpu-amd/1" if count >= 4 else "/gpu-amd/0"})
        self.assertEqual(result, 1)
        self.assertIn("identity", terminal["reason"])
        self.assertTrue(terminal["restore_confirmed"])

    def test_duplicate_selected_model_blocks_before_takeover(self):
        result, _publish, terminal, backend = self.run_worker(
            source=lambda count, now: {"gpu_name_match_count": 2})
        self.assertEqual(result, 1)
        self.assertFalse(terminal["control_attempted"])
        self.assertEqual(backend.writes, [])

    def test_second_previously_running_gpu_channel_stopping_forces_full_case_airflow(self):
        def source(count, now):
            return dict(gpu_hotspot_temp=85, gpu_fan=1400,
                        gpu_fans=[dict(id="/gpu-amd/0/fan/0", rpm=1400),
                                  dict(id="/gpu-amd/0/fan/1", rpm=1400 if count < 4 else 0)])
        result, publish, terminal, _backend = self.run_worker(source=source, duration=20)
        self.assertEqual(result, 0)
        reports = [call.kwargs for call in publish.call_args_list if "command_pct" in call.kwargs]
        self.assertTrue(any(row["command_pct"] == 100 and "stopped" in row["reason"] for row in reports))
        self.assertTrue(terminal["restore_confirmed"])

    def test_complete_identical_lhm_reads_do_not_prove_sensor_staleness(self):
        result, publish, terminal, _backend = self.run_worker(duration=30)
        self.assertEqual(result, 0)
        self.assertNotIn(100, self.commands(publish))
        self.assertTrue(terminal["restore_confirmed"])

    def test_cool_gpu_zero_rpm_remains_normal_after_earlier_rotation(self):
        result, publish, terminal, _backend = self.run_worker(
            source=lambda count, now: dict(gpu_fan=1400 if count < 4 else 0,
                gpu_fans=[dict(id="first", rpm=1400 if count < 4 else 0),
                          dict(id="second", rpm=1400 if count < 4 else 0)]), duration=20)
        self.assertEqual(result, 0)
        self.assertNotIn(100, self.commands(publish))
        self.assertTrue(terminal["restore_confirmed"])

    def test_disappearing_previously_running_gpu_channel_forces_full_airflow_while_hot(self):
        def source(count, now):
            channels = [dict(id="first", rpm=1400), dict(id="second", rpm=1400)]
            return dict(gpu_hotspot_temp=85, gpu_fan=1400, gpu_fans=channels if count < 4 else channels[:1])
        result, publish, terminal, _backend = self.run_worker(source=source)
        self.assertEqual(result, 0)
        reports = [call.kwargs for call in publish.call_args_list if "command_pct" in call.kwargs]
        self.assertTrue(any(row["command_pct"] == 100 and "feedback missing" in row["reason"] for row in reports))
        self.assertTrue(terminal["restore_confirmed"])

    def test_native_read_finishing_after_owner_timeout_sends_no_new_command(self):
        result, publish, terminal, _backend = self.run_worker(read_delay_at=4)
        self.assertEqual(result, 1)
        self.assertEqual(len(self.commands(publish)), 1)
        self.assertEqual(terminal["stop_cause"], "heartbeat_expired")
        self.assertTrue(terminal["restore_confirmed"])

    def test_transient_missing_cpu_identity_waits_with_its_missing_tach_feedback(self):
        result, publish, terminal, _backend = self.run_worker(
            source=lambda count, now: dict(cpu_fan=None, cpu_fan_id=None) if count == 1 else {})
        self.assertEqual(result, 0)
        waiting = [call for call in publish.call_args_list if call.kwargs.get("phase") == "waiting"]
        self.assertGreaterEqual(len(waiting), 2)
        self.assertTrue(terminal["restore_confirmed"])

    def test_interrupted_manual_owner_recovers_before_waiting_for_zero_tach(self):
        result, _publish, terminal, backend = self.run_worker(rpm=0, interrupted=True)
        self.assertEqual(result, 0)
        self.assertTrue(terminal["control_attempted"])
        self.assertFalse(backend.journal.exists())
        self.assertTrue(all(backend.values[b850.MODES[i]] & 0x80 for i in (1, 2, 3)))
        self.assertTrue(terminal["restore_confirmed"])
        self.assertFalse(terminal["recovery_pending"])

    def test_commissioning_never_lowers_to_minimum_while_full_temperature_protection_applies(self):
        result, publish, terminal, _backend = self.run_worker(
            source=lambda count, now: {"gpu_hotspot_temp": 90} if now >= 117 else {},
            duration=30, commission=True)
        self.assertEqual(result, 1)
        self.assertIn("Minimum-duty commissioning stopped", terminal["reason"])
        self.assertTrue(all(command == 100 for command in self.commands(publish)))
        self.assertFalse(any(call.args[1] == "active" for call in publish.call_args_list))
        self.assertTrue(terminal["restore_confirmed"])

    def test_commissioning_requires_real_minimum_rpm_response_after_settling(self):
        result, publish, terminal, _backend = self.run_worker(commission=True, duration=30, minimum_rpm=1230)
        self.assertEqual(result, 1)
        self.assertIn("minimum-duty RPM decrease", terminal["reason"])
        self.assertFalse(any(call.args[1] == "active" for call in publish.call_args_list))
        self.assertTrue(terminal["restore_confirmed"])

    def test_denied_journal_inspection_sends_no_command_and_remains_unknown(self):
        result, _publish, terminal, backend = self.run_worker(journal_error=True)
        self.assertEqual(result, 1)
        self.assertIn("journal access denied", terminal["reason"])
        self.assertIsNone(terminal["recovery_pending"])
        self.assertTrue(terminal["restore_errors"])
        self.assertFalse(terminal["restore_confirmed"])
        self.assertEqual(backend.writes, [])


class CaseCouplingPolicyTests(unittest.TestCase):
    def test_faster_secondary_gpu_tach_is_not_hidden_by_primary_channel(self):
        data = sample(gpu_fan=1000, gpu_fans=[dict(id="first", rpm=1000), dict(id="second", rpm=3200)])
        command, reason, fractions = b850.synchronized_demand(data, 2000, 3442)
        self.assertEqual(command, 93)
        self.assertEqual(reason, "GPU fans")
        self.assertEqual(fractions["GPU"]["source"], "RPM estimate")

    def test_missing_secondary_gpu_tach_does_not_earn_cooling_credit(self):
        data = sample(gpu_fan=1000, gpu_fan_pct=50,
                      gpu_fans=[dict(id="first", rpm=1000), dict(id="second", rpm=None)])
        command, reason, _fractions = b850.synchronized_demand(data, 2000, 3442)
        self.assertEqual(command, 100)
        self.assertIn("feedback", reason)


class CaseCouplingRestoreTests(unittest.TestCase):
    def test_interrupted_recovery_restores_other_owned_headers_after_one_mode_fault(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = Backend()
            journal = Path(directory) / "recovery.json"
            session = b850.B850Session(backend, journal, nullcontext)
            session.apply(90)
            native_write = backend.write
            def failing_write(register, value):
                if register == b850.MODES[1]:
                    raise OSError("persistent first header mode fault")
                native_write(register, value)
            backend.write = failing_write
            with self.assertRaisesRegex(RuntimeError, "recovery unconfirmed"):
                b850.B850Session(backend, journal, nullcontext)
            self.assertEqual(backend.values[b850.MODES[2]], session.baseline[b850.MODES[2]])
            self.assertEqual(backend.values[b850.MODES[3]], session.baseline[b850.MODES[3]])
            self.assertTrue(journal.exists())

    def test_silently_failed_handback_never_sends_low_retained_duty_to_manual_header(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = Backend()
            session = b850.B850Session(backend, Path(directory) / "recovery.json", nullcontext)
            session.apply(90)
            command = backend.values[b850.DUTIES[1]]
            native_write = backend.write
            backend.write = lambda register, value: None if register == b850.MODES[1] else native_write(register, value)
            self.assertTrue(session.restore())
            self.assertEqual(backend.values[b850.DUTIES[1]], command)
            self.assertEqual(backend.values[b850.MODES[2]], session.baseline[b850.MODES[2]])
            self.assertTrue(session.journal.exists())

    def test_failed_first_restore_write_does_not_leave_other_owned_headers_manual(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = Backend()
            session = b850.B850Session(backend, Path(directory) / "recovery.json", nullcontext)
            session.apply(90)
            native_write = backend.write
            def failing_write(register, value):
                if register == b850.MODES[1]:
                    raise OSError("persistent first header mode fault")
                native_write(register, value)
            backend.write = failing_write
            errors = session.restore()
            self.assertTrue(errors)
            self.assertEqual(backend.values[b850.MODES[2]], session.baseline[b850.MODES[2]])
            self.assertEqual(backend.values[b850.MODES[3]], session.baseline[b850.MODES[3]])
            self.assertTrue(session.journal.exists())

    def test_automatic_mode_is_confirmed_before_restoring_retained_manual_duty(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = Backend()
            session = b850.B850Session(backend, Path(directory) / "recovery.json", nullcontext)
            session.apply(90)
            start = len(backend.writes)
            self.assertEqual(session.restore(), [])
            restore = backend.writes[start:]
            for i in (1, 2, 3):
                mode_index = next(n for n, entry in enumerate(restore) if entry[0] == b850.MODES[i])
                duty_index = next(n for n, entry in enumerate(restore) if entry[0] == b850.DUTIES[i])
                self.assertLess(mode_index, duty_index)


if __name__ == "__main__":
    unittest.main()
