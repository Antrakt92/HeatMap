"""Hardware exclusions without starting, terminating, or inspecting real tools."""
from types import SimpleNamespace as NS
import unittest
from unittest import mock
from pathlib import Path
import tempfile

import psutil

import hardware_access_guard as guard


def process(name):
    return NS(info={"name": name})


class HardwareAccessGuardTests(unittest.TestCase):
    def test_gnu_compiler_layout_does_not_block_hardware(self):
        for layout in ('mingw', 'w64devkit'):
            with self.subTest(layout=layout), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                binary = root / 'bin' / 'gcc.exe'
                binary.parent.mkdir()
                binary.touch()
                if layout == 'mingw':
                    support = root / 'libexec/gcc/x86_64-w64-mingw32/15.1.0'
                    library = root / 'lib/gcc/x86_64-w64-mingw32/15.1.0'
                else:
                    support, library = binary.parent, root / 'lib'
                support.mkdir(parents=True, exist_ok=True)
                library.mkdir(parents=True, exist_ok=True)
                (support / 'cc1.exe').touch()
                (library / 'libgcc.a').touch()
                compiler = mock.Mock(info={'name': 'GCC.exe'})
                compiler.exe.return_value = str(binary)
                with mock.patch.object(guard.psutil, 'process_iter', return_value=[compiler]):
                    self.assertEqual(guard.hardware_conflicts(), [])
                # A suggestive path alone cannot authorize access.
                (library / 'libgcc.a').unlink()
                with mock.patch.object(guard.psutil, 'process_iter', return_value=[compiler]):
                    self.assertEqual(guard.hardware_conflicts(), ['gcc.exe'])

    def test_gigabyte_unknown_or_unreadable_gcc_still_blocks(self):
        for path in (r'C:\Program Files\GIGABYTE\Control Center\GCC.exe', '', None):
            candidate = mock.Mock(info={'name': 'gcc.exe'})
            candidate.exe.return_value = path
            with mock.patch.object(guard.psutil, 'process_iter', return_value=[candidate]):
                self.assertEqual(guard.hardware_conflicts(), ['gcc.exe'])
        candidate.exe.side_effect = psutil.AccessDenied(42)
        with mock.patch.object(guard.psutil, 'process_iter', return_value=[candidate]):
            self.assertEqual(guard.hardware_conflicts(), ['gcc.exe'])

    def test_gcc_only_allows_restricted_monitor_not_fan_control(self):
        candidate = mock.Mock(info={'name': 'gcc.exe'})
        candidate.exe.return_value = None
        with mock.patch.object(guard.psutil, 'process_iter', return_value=[candidate]):
            self.assertEqual(guard.require_hardware_access('monitor'), 'gcc')
            with self.assertRaises(guard.HardwareAccessConflict):
                guard.require_hardware_access()
        with mock.patch.object(guard.psutil, 'process_iter', return_value=[candidate, process('hwinfo64.exe')]):
            with self.assertRaises(guard.HardwareAccessConflict):
                guard.require_hardware_access('monitor')

    def test_ryzen_master_allows_monitoring_but_keeps_control_exclusive(self):
        for name in guard.RYZEN_MASTER_PROCESS_NAMES:
            for names in ([name], [name, 'gcc.exe']):
                with self.subTest(names=names), mock.patch.object(
                    guard, 'hardware_conflicts', return_value=sorted(names)
                ):
                    self.assertEqual(guard.require_hardware_access('monitor'), 'ryzen_master')
                    with self.assertRaises(guard.HardwareAccessConflict):
                        guard.require_hardware_access()
        for other in ('atisetup.exe', 'pnputil.exe', 'fancontrol.exe'):
            with mock.patch.object(guard, 'hardware_conflicts',
                                   return_value=['amd ryzen master.exe', other]):
                with self.assertRaises(guard.HardwareAccessConflict):
                    guard.require_hardware_access('monitor')

    def test_non_gcc_tools_never_inspect_executable_paths(self):
        candidate = mock.Mock(info={'name': 'cpuz.exe'})
        with mock.patch.object(guard.psutil, 'process_iter', return_value=[candidate]):
            self.assertEqual(guard.hardware_conflicts(), ['cpuz.exe'])
        candidate.exe.assert_not_called()

    def test_gcc_exit_during_identity_check_does_not_latch_a_conflict(self):
        candidate = mock.Mock(info={'name': 'gcc.exe'})
        candidate.exe.side_effect = psutil.NoSuchProcess(42)
        with mock.patch.object(guard.psutil, 'process_iter', return_value=[candidate, process('cpuz.exe')]):
            self.assertEqual(guard.hardware_conflicts(), ['cpuz.exe'])

    def test_official_ryzen_master_executable_blocks_control_access(self):
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
            "atisetup.exe",
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

    def test_amd_driver_installation_blocks_even_gcc_monitor_mode(self):
        with mock.patch.object(guard.psutil, 'process_iter', return_value=[process('AtiSetup.exe')]):
            with self.assertRaisesRegex(guard.HardwareAccessConflict, 'atisetup.exe'):
                guard.require_hardware_access('monitor')

    def test_pnputil_only_blocks_live_driver_installation(self):
        candidate = mock.Mock(info={'name': 'pnputil.exe'})
        for args, blocked in ((['pnputil.exe', '/enum-devices'], False),
                              (['pnputil.exe', '/add-driver', 'amd.inf'], False),
                              (['pnputil.exe', '/ADD-DRIVER', 'amd.inf', '/INSTALL'], True)):
            candidate.cmdline.return_value = args
            with mock.patch.object(guard.psutil, 'process_iter', return_value=[candidate]):
                self.assertEqual(guard.hardware_conflicts(), ['pnputil.exe'] if blocked else [])

    def test_unreadable_pnputil_is_guarded_and_exited_pnputil_is_ignored(self):
        candidate = mock.Mock(info={'name': 'pnputil.exe'})
        for error, expected in ((psutil.AccessDenied(1), ['pnputil.exe']),
                                (psutil.NoSuchProcess(1), [])):
            candidate.cmdline.side_effect = error
            with mock.patch.object(guard.psutil, 'process_iter', return_value=[candidate]):
                self.assertEqual(guard.hardware_conflicts(), expected)

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
