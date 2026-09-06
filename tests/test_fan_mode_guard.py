"""Primary output-mode verification using mocked public LHM reports only."""
import unittest
from types import SimpleNamespace as NS
from unittest import mock

import case_fans as fans
from test_case_fans import fixture


def report(mode=0x07):
    text = "LPC IT87XX\n\nChip ID: 0x8688\nChip Version: 0x2\nBase Address: 0x0A40\nGPIO Address: 0x0000\n\n"
    text += "Environment Controller Registers Bank 0\n\n"
    text += "      " + " ".join(f"{col:02X}" for col in range(16)) + "\n\n"
    for row in range(0, 0xB0, 16):
        text += f" {row:02X}  " + "".join(f" {mode if row + col == 0x13 else col:02X}"
                                        for col in range(16)) + "\n"
    return text + "\n\nGPIO Registers\n\n 01 02\n\n"


class FanModeReportTests(unittest.TestCase):
    def test_reads_correct_register_and_ignores_later_bank(self):
        for value in (0, 6, 7, 0xA6, 255):
            with self.subTest(value=value):
                text = report(value)
                self.assertEqual(fans.parse_primary_fan_mode(text.replace("\n", "\r\n")), value)
                later = text.replace("Bank 0", "Bank 1").split("Environment Controller", 1)[1]
                text = text.replace("GPIO Registers", "Environment Controller" + later + "GPIO Registers")
                self.assertEqual(fans.parse_primary_fan_mode(text), value)

    def test_rejects_missing_unreadable_malformed_duplicate_or_foreign_report(self):
        valid = report()
        row = next(line for line in valid.splitlines() if line.startswith(" 10 "))
        cases = [None, "", valid.replace("0x8688", "0x8792"), valid + valid,
                 valid.replace("Bank 0", "Bank 1"), valid.replace(row, ""),
                 valid.replace(row, row + "\n" + row), valid.replace(row, row + " 00"),
                 valid.replace(row, row.replace(" 07", " ??")),
                 valid.replace(row, row.replace(" 07", " 0x07")),
                 valid.replace(" 20 ", " 10 "), valid.replace("00 01 02 03", "00 01 03 02"),
                 valid.replace("Chip ID: 0x8688", "Chip ID: 0x8688\nChip ID: 0x8688")]
        for text in cases:
            with self.subTest(text=text):
                with self.assertRaises(RuntimeError):
                    fans.parse_primary_fan_mode(text)

    def test_only_unique_primary_on_same_computer_is_used(self):
        computer, _ = fixture()
        primary, secondary = computer.Hardware[0].SubHardware
        secondary.GetReport = mock.Mock(side_effect=AssertionError("secondary must not be read"))
        self.assertEqual(fans.read_primary_fan_mode(computer), 7)
        secondary.GetReport.assert_not_called()
        computer.Hardware[0].SubHardware.append(primary)
        with self.assertRaises(RuntimeError):
            fans.read_primary_fan_mode(computer)
        self.assertEqual(primary.GetReport.call_count, 1)


class FanModeWorkerTests(unittest.TestCase):
    def run_worker(self, before, after=None):
        import overlay
        computer, controls = fixture()
        primary = computer.Hardware[0].SubHardware[0]
        primary.GetReport.side_effect = [before, before if after is None else after]
        primary.trace = mock.Mock()
        for label, method in (("report", primary.GetReport), ("write", controls[0].SetSoftware),
                              ("restore", controls[0].SetDefault), ("close_sub", primary.Close),
                              ("update", primary.Update), ("close_computer", computer.Close)):
            primary.trace.attach_mock(method, label)
        owner = mock.Mock()
        owner.create_time.return_value = 1
        owner.is_running.side_effect = [True, False]
        modules = {"clr": mock.Mock(), "LibreHardwareMonitor": mock.Mock(),
                   "LibreHardwareMonitor.Hardware": NS(Computer=lambda: computer)}
        with (mock.patch.dict("sys.modules", modules),
              mock.patch.object(overlay, "_is_admin", return_value=True),
              mock.patch.object(overlay, "_runtime_dll_errors", return_value=[]),
              mock.patch.object(overlay, "read_sensors", return_value={}),
              mock.patch.object(fans, "require_hardware_access"),
              mock.patch.object(fans.psutil, "Process", return_value=owner),
              mock.patch.object(fans.threading, "Thread"),
              mock.patch.object(fans, "write_status") as publish):
            result = fans.worker("unused-mocked-mode.json", 7, 1)
        computer.Close.assert_called_once_with()
        return result, publish.call_args.kwargs, controls, primary

    def test_disabled_selected_bits_or_failed_report_refuse_before_any_write(self):
        for before in (report(0), report(1), report(2), report(4), "", RuntimeError("ISA report failure")):
            with self.subTest(before=before):
                result, status, controls, _ = self.run_worker(before)
                self.assertEqual(result, 1)
                self.assertFalse(status["restore_confirmed"])
                for control in controls:
                    control.SetSoftware.assert_not_called()
                    control.SetDefault.assert_not_called()

    def test_mode_restore_mismatch_or_unreadable_report_is_unconfirmed(self):
        for after in (report(2), report(4), "", RuntimeError("ISA report failure")):
            with self.subTest(after=after):
                result, status, controls, primary = self.run_worker(report(7), after)
                self.assertEqual(result, 1)
                self.assertFalse(status["restore_confirmed"])
                self.assertTrue(status["restore_errors"])
                for control in controls[:2]:
                    control.SetSoftware.assert_called_once_with(100.0)
                    control.SetDefault.assert_called_once_with()
                self.assertEqual(primary.GetReport.call_count, 2)
                primary.Close.assert_called_once_with()
                primary.Update.assert_called_once_with()

    def test_unselected_mode_bits_do_not_invalidate_restoration(self):
        result, status, controls, primary = self.run_worker(report(7), report(0x86))
        self.assertEqual(result, 0)
        self.assertTrue(status["restore_confirmed"])
        self.assertEqual(status["restore_errors"], [])
        self.assertEqual(primary.GetReport.call_count, 2)
        self.assertEqual(primary.trace.mock_calls, [mock.call.report(), mock.call.write(100.0),
                         mock.call.restore(), mock.call.close_sub(), mock.call.update(),
                         mock.call.report(), mock.call.close_computer()])
        for control in controls[2:]:
            control.SetSoftware.assert_not_called()
            control.SetDefault.assert_not_called()
