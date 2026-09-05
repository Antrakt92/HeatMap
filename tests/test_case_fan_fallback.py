"""Safe partial control on the two separate B550 fan-controller chips."""
from types import SimpleNamespace as NS
import unittest
from unittest import mock

import case_fans as fans


SYS1 = "System Fan #1"
SYS2 = "System Fan #2"
SYS4 = "System Fan #4"
PUMP5 = "System Fan #5 / Pump"
PUMP6 = "System Fan #6 / Pump"


def controller_topology(pumps=(82, 81)):
    primary = NS(Identifier="/lpc/it8688e/0", Sensors=[], Close=mock.Mock(), Update=mock.Mock())
    shared = NS(Identifier="/lpc/it8792e/0", Sensors=[], Close=mock.Mock(), Update=mock.Mock())
    channels = {}
    for chip, index, name, initial in (
        (primary, 0, "CPU Fan", 65),
        (primary, 1, SYS1, 60),
        (primary, 2, SYS2, 65),
        (primary, 3, "System Fan #3", 75),
        (shared, 0, PUMP5, pumps[0]),
        (shared, 1, PUMP6, pumps[1]),
        (shared, 2, SYS4, 70),
    ):
        sensor = NS(Name=name, SensorType="Control", Value=initial,
                    Identifier=f"{chip.Identifier}/control/{index}", Hardware=chip)
        tach = NS(Name=name, SensorType="Fan", Value=1000,
                  Identifier=f"{chip.Identifier}/fan/{index}", Hardware=chip)
        control = mock.Mock(MinSoftwareValue=0, MaxSoftwareValue=100)
        control.SetSoftware.side_effect = lambda value, s=sensor: setattr(s, "Value", value)
        control.SetDefault.side_effect = lambda s=sensor, value=initial: setattr(s, "Value", value)
        sensor.Control = control
        chip.Sensors.extend((sensor, tach))
        channels[name] = NS(chip=chip, sensor=sensor, tach=tach, control=control)
    board = NS(HardwareType="Motherboard", Model="B550_AORUS_PRO_AC",
               SubHardware=[primary, shared])
    return NS(Hardware=[board]), channels


class PartialControllerTests(unittest.TestCase):
    def assert_no_control_calls(self, channels, excluded):
        for name in excluded:
            with self.subTest(untouched=name):
                channels[name].control.SetSoftware.assert_not_called()
                channels[name].control.SetDefault.assert_not_called()

    def test_partial_profile_writes_and_restores_only_primary_chip_channels(self):
        computer, channels = controller_topology(pumps=(82, 81))
        selected = fans.select_controls(computer)
        self.assertEqual([item[0] for item in selected], [SYS1, SYS2])
        session = fans.CaseFanSession(selected)
        baseline = session.readings()

        session.apply(100)
        session.apply(70)
        self.assertEqual(session.restore(), [])

        self.assertEqual(session.readings(), baseline)
        for name in (SYS1, SYS2):
            self.assertEqual(channels[name].control.SetSoftware.call_args_list,
                             [mock.call(100), mock.call(70)])
            channels[name].control.SetDefault.assert_called_once_with()
        self.assert_no_control_calls(channels, (SYS4, PUMP5, PUMP6, "CPU Fan", "System Fan #3"))
        self.assertEqual((channels[PUMP5].sensor.Value, channels[PUMP6].sensor.Value), (82, 81))

    def test_full_profile_includes_sys4_only_with_valid_pump_guard(self):
        computer, channels = controller_topology(pumps=(100, 100))
        selected = fans.select_controls(computer)
        self.assertEqual([item[0] for item in selected], [SYS1, SYS2, SYS4])
        session = fans.CaseFanSession(selected)
        session.apply(100)
        self.assertEqual(session.restore(), [])
        for name in (SYS1, SYS2, SYS4):
            channels[name].control.SetSoftware.assert_called_once_with(100)
            channels[name].control.SetDefault.assert_called_once_with()
        self.assert_no_control_calls(channels, (PUMP5, PUMP6, "CPU Fan", "System Fan #3"))

    def test_missing_ambiguous_or_unreadable_pumps_leave_safe_primary_channels(self):
        for fault in ("missing", "duplicate", "unreadable", "nonfinite"):
            with self.subTest(fault=fault):
                computer, channels = controller_topology(pumps=(100, 100))
                pump = channels[PUMP5]
                if fault == "missing":
                    pump.chip.Sensors.remove(pump.sensor)
                elif fault == "duplicate":
                    pump.chip.Sensors.append(pump.sensor)
                else:
                    pump.sensor.Value = None if fault == "unreadable" else float("nan")
                self.assertEqual([item[0] for item in fans.select_controls(computer)], [SYS1, SYS2])
                self.assert_no_control_calls(channels, channels)

    def test_selected_sensor_and_owner_ids_are_required_even_with_correct_names(self):
        for pumps, names in (((82, 81), (SYS1, SYS2)), ((100, 100), (SYS1, SYS2, SYS4))):
            for name in names:
                for part in ("sensor", "tach", "chip"):
                    for identifier in (None, "/lpc/unrecognized/0/control/1"):
                        with self.subTest(pumps=pumps, name=name, part=part, identifier=identifier):
                            computer, channels = controller_topology(pumps=pumps)
                            target = getattr(channels[name], part)
                            if identifier is None:
                                del target.Identifier
                            else:
                                target.Identifier = identifier
                            with self.assertRaises(RuntimeError):
                                fans.select_controls(computer)
                            self.assert_no_control_calls(channels, channels)

    def test_identifiers_do_not_allow_sensor_to_be_rehomed_on_other_chip(self):
        computer, channels = controller_topology()
        channel = channels[SYS1]
        other = channels[SYS4].chip
        channel.chip.Sensors.remove(channel.sensor)
        other.Sensors.append(channel.sensor)
        channel.sensor.Hardware = other

        with self.assertRaises(RuntimeError):
            fans.select_controls(computer)
        self.assert_no_control_calls(channels, channels)

    def test_unselected_shared_channel_identity_does_not_block_primary_control(self):
        computer, channels = controller_topology()
        del channels[SYS4].sensor.Identifier
        del channels[SYS4].tach.Identifier
        self.assertEqual([item[0] for item in fans.select_controls(computer)], [SYS1, SYS2])

    def test_partial_selection_does_not_recheck_or_touch_unowned_shared_controller(self):
        computer, channels = controller_topology()
        selected = fans.select_controls(computer)
        channels[PUMP5].sensor.Value = None
        channels[PUMP6].sensor.Value = 0
        with mock.patch.object(fans, "verify_shared_controller", wraps=fans.verify_shared_controller) as guard:
            fans.verify_selected_shared_controller(computer, selected)
        guard.assert_not_called()
        self.assert_no_control_calls(channels, channels)

    def test_full_selection_rejects_changed_pump_guard_before_next_command(self):
        computer, channels = controller_topology(pumps=(100, 100))
        selected = fans.select_controls(computer)
        session = fans.CaseFanSession(selected)
        session.apply(100)
        channels[PUMP5].sensor.Value = 82
        channels[PUMP6].sensor.Value = 81

        with self.assertRaises(RuntimeError):
            fans.verify_selected_shared_controller(computer, selected)
        self.assertEqual(session.restore(), [])
        self.assert_no_control_calls(channels, (PUMP5, PUMP6, "CPU Fan", "System Fan #3"))


class PartialReferenceTests(unittest.TestCase):
    def test_only_exact_partial_and_full_reference_channel_sets_are_valid(self):
        for keys in ((SYS1, SYS2), (SYS1, SYS2, SYS4)):
            reference = {name: 1200 for name in keys}
            result = fans.full_rpm_reference(reference)
            self.assertEqual(result, reference)
            self.assertIsNot(result, reference)
        for keys in ((), (SYS1,), (SYS2,), (SYS4,), (SYS1, SYS4), (SYS2, SYS4),
                     (SYS1, SYS2, "CPU Fan"), (SYS1, SYS2, SYS4, PUMP5)):
            with self.subTest(keys=keys):
                self.assertIsNone(fans.full_rpm_reference({name: 1200 for name in keys}))
        for bad in (None, True, "1200", 199, 10001, float("nan"), float("inf")):
            with self.subTest(value=bad):
                self.assertIsNone(fans.full_rpm_reference({SYS1: bad, SYS2: 1200}))

    def test_partial_reference_allows_verified_primary_restart(self):
        baseline = [dict(name=name, rpm=1200, control_pct=60) for name in (SYS1, SYS2)]
        readings = [dict(name=name, rpm=1190, control_pct=100) for name in (SYS1, SYS2)]
        fans.verify_full_airflow(baseline, readings, {SYS1: 1200, SYS2: 1200})

    def test_partial_reference_cannot_bypass_unverified_sys4_acceleration(self):
        baseline = [dict(name=name, rpm=1200, control_pct=60) for name in (SYS1, SYS2, SYS4)]
        readings = [dict(name=name, rpm=1190, control_pct=100) for name in (SYS1, SYS2, SYS4)]
        reference = {SYS1: 1200, SYS2: 1200}
        with self.assertRaises(RuntimeError):
            fans.verify_full_airflow(baseline, readings, reference)
        readings[-1]["rpm"] = 1400
        fans.verify_full_airflow(baseline, readings, reference)

    def test_reference_does_not_hide_channel_swap_bad_feedback_or_low_rpm(self):
        baseline = [dict(name=name, rpm=1200, control_pct=60) for name in (SYS1, SYS2)]
        reference = {SYS1: 1200, SYS2: 1200}
        for fault in ("swapped", "feedback", "rpm"):
            with self.subTest(fault=fault):
                readings = [dict(name=name, rpm=1190, control_pct=100) for name in (SYS1, SYS2)]
                if fault == "swapped":
                    readings.reverse()
                elif fault == "feedback":
                    readings[0]["control_pct"] = 75
                else:
                    readings[0]["rpm"] = 900
                with self.assertRaises(RuntimeError):
                    fans.verify_full_airflow(baseline, readings, reference)


if __name__ == "__main__":
    unittest.main()
