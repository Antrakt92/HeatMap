"""Hardware exclusions without starting, terminating, or inspecting real tools."""
from types import SimpleNamespace as NS
import unittest
from unittest import mock

import psutil

import hardware_access_guard as guard


def process(name):
    return NS(info={"name": name})


class HardwareAccessGuardTests(unittest.TestCase):
    def test_official_ryzen_master_executable_blocks_sensor_access(self):
        # File table in AMD's signed 3.1.1.5502 MSI uses this spaced basename.
        with mock.patch.object(guard.psutil, "process_iter", return_value=[
            process("AMD Ryzen Master.exe"),
        ]):
            with self.assertRaisesRegex(guard.HardwareAccessConflict, "amd ryzen master"):
                guard.require_hardware_access()

    def test_known_conflicts_are_exact_case_insensitive_sorted_unique_names(self):
        expected = sorted({
            "fancontrol.exe", "siv.exe", "gcc.exe", "easytune.exe",
            "cpuz.exe", "cpuz_x64.exe", "hwinfo32.exe", "hwinfo64.exe",
            "amdryzenmaster.exe", "ryzenmaster.exe", "amd-ryzen-master.exe",
            "amd ryzen master.exe",
        })
        inventory = [process(name.upper()) for name in reversed(expected)]
        inventory += [process("CPUZ.exe"), process("FanControl.EXE")]
        with mock.patch.object(guard.psutil, "process_iter", return_value=inventory) as scan:
            self.assertEqual(guard.hardware_conflicts(), expected)
        self.assertEqual(scan.call_args.args, (["name"],))
        self.assertEqual(set(scan.call_args.kwargs), {"ad_value"})

    def test_adrenalin_drivers_desktop_apps_and_substrings_do_not_conflict(self):
        names = ["AMDRSServ.exe", "AMDRSSrcExt.exe", "RadeonSoftware.exe", "atiesrxx.exe",
                 "atieclxx.exe", "dwm.exe", "explorer.exe", "notepad.exe", "HeatMap.exe",
                 "cpuz.exe.backup", "my-fancontrol.exe", "hwinfo64-helper.exe", "gcc"]
        with mock.patch.object(guard.psutil, "process_iter", return_value=[process(name) for name in names]):
            self.assertEqual(guard.hardware_conflicts(), [])
            self.assertIsNone(guard.require_hardware_access())

    def test_each_call_detects_newly_started_tools_and_their_exit(self):
        with mock.patch.object(guard.psutil, "process_iter", side_effect=(
            [process("explorer.exe")], [process("CPUZ_X64.exe")], [process("explorer.exe")],
        )):
            guard.require_hardware_access()
            with self.assertRaisesRegex(guard.HardwareAccessConflict, "cpuz_x64.exe"):
                guard.require_hardware_access()
            guard.require_hardware_access()

    def test_conflict_message_names_tools_and_explains_close_then_restart(self):
        with mock.patch.object(guard.psutil, "process_iter", return_value=[process("HwInfo64.exe")]):
            with self.assertRaises(guard.HardwareAccessConflict) as caught:
                guard.require_hardware_access()
        self.assertIn("hwinfo64.exe", str(caught.exception))
        self.assertIn("Close", str(caught.exception))
        self.assertIn("restart HeatMap", str(caught.exception))

    def test_process_disappearing_before_info_read_is_skipped(self):
        vanished = mock.Mock()
        type(vanished).info = mock.PropertyMock(side_effect=psutil.NoSuchProcess(123))
        with mock.patch.object(guard.psutil, "process_iter", return_value=[vanished, process("cpuz.exe")]):
            self.assertEqual(guard.hardware_conflicts(), ["cpuz.exe"])

    def test_process_disappearing_during_iteration_does_not_hide_later_conflict(self):
        class Inventory:
            def __init__(self):
                self.items = iter((psutil.NoSuchProcess(123), process("hwinfo32.exe")))

            def __iter__(self):
                return self

            def __next__(self):
                item = next(self.items)
                if isinstance(item, Exception):
                    raise item
                return item

        with mock.patch.object(guard.psutil, "process_iter", return_value=Inventory()):
            self.assertEqual(guard.hardware_conflicts(), ["hwinfo32.exe"])

    def test_malformed_or_unreadable_inventory_is_not_treated_as_clear(self):
        for info in (None, [], {}, {"name": None}, {"name": "  "},
                     {"name": True}, {"name": 123}, {"name": object()}):
            with self.subTest(info=info), mock.patch.object(
                guard.psutil, "process_iter", return_value=[NS(info=info)],
            ):
                with self.assertRaises(guard.HardwareAccessConflict):
                    guard.require_hardware_access()

    def test_empty_secure_system_kernel_name_is_skipped_without_skipping_real_tools(self):
        with mock.patch.object(guard.psutil, "process_iter", return_value=[process(""), process("CPUZ.exe")]):
            self.assertEqual(guard.hardware_conflicts(), ["cpuz.exe"])
        with mock.patch.object(guard.psutil, "process_iter", return_value=[process("")]):
            self.assertIsNone(guard.require_hardware_access())

    def test_enumeration_errors_fail_closed_without_exposing_exception_details(self):
        for error in (psutil.AccessDenied(42), OSError("private inventory detail")):
            with self.subTest(error=type(error).__name__), mock.patch.object(
                guard.psutil, "process_iter", side_effect=error,
            ):
                with self.assertRaises(guard.HardwareAccessConflict) as caught:
                    guard.require_hardware_access()
                self.assertIn("Cannot verify", str(caught.exception))
                self.assertNotIn("private inventory detail", str(caught.exception))

    def test_error_midway_through_inventory_does_not_return_partial_success(self):
        def inventory():
            yield process("notepad.exe")
            raise psutil.AccessDenied(42)

        with mock.patch.object(guard.psutil, "process_iter", return_value=inventory()):
            with self.assertRaises(guard.HardwareAccessConflict):
                guard.hardware_conflicts()


if __name__ == "__main__":
    unittest.main()
