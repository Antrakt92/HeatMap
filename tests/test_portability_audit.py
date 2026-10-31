"""Cross-machine failures found while auditing the native monitoring port."""
import unittest
import ctypes
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest import mock

import launch
import native_discovery as discovery
import overlay
import windows_telemetry as telemetry
import windows_native
from test_native_discovery import FIRMWARE


class PortabilityAuditTests(unittest.TestCase):
    def test_arm_cannot_edit_the_original_computers_cpu_fan_calibration(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app._windows_only = True
        app._show_info_notice = mock.Mock()
        with mock.patch.object(overlay.simpledialog, 'askinteger', side_effect=AssertionError):
            app.configure_cpu_reference()
        app._show_info_notice.assert_called_once()

    def test_x64_batch_forwarding_preserves_paths_with_spaces_apostrophes_and_ampersands(self):
        with tempfile.TemporaryDirectory(prefix="HeatMap O'Brien & Ж ") as folder:
            launcher = Path(folder) / "run_as_admin.bat"
            launcher.write_bytes(b"@echo off\r\nexit /b 7\r\n")
            with mock.patch.object(launch, "__file__", str(Path(folder) / "launch.py")), \
                    mock.patch.object(launch, "windows_only_mode", return_value=False):
                self.assertEqual(launch.main(), 7)

    def test_arm_display_save_preserves_x64_cooling_settings_without_starting_controls(self):
        config = dict(overlay._default_config(), case_fans_enabled=True,
                      case_fans_shared_enabled=True, case_fans_b850_enabled=True,
                      gpu_fans_enabled=True)
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        with mock.patch.object(overlay, "windows_only_mode", return_value=True), \
                mock.patch.object(overlay, "load_config_result", return_value=(config, None)), \
                mock.patch.object(overlay, "require_hardware_access", side_effect=AssertionError), \
                mock.patch.object(overlay.tk, "Tk", side_effect=RuntimeError("window reached")):
            with self.assertRaisesRegex(RuntimeError, "window reached"):
                app._initialize()
        app.config["x"] = 123
        with mock.patch.object(overlay, "save_config", return_value=(True, "")) as save:
            app._save_config(update_status=False)
        saved = save.call_args.args[0]
        self.assertEqual(saved["x"], 123)
        self.assertTrue(saved["case_fans_enabled"])
        self.assertTrue(saved["gpu_fans_enabled"])
        self.assertFalse(app.config["case_fans_enabled"])
        self.assertFalse(app.config["gpu_fans_enabled"])
        self.assertIsNone(app.fan_worker.process)

    def test_default_python_launcher_reaches_existing_x64_launcher(self):
        with mock.patch.object(launch, "windows_only_mode", return_value=False), \
                mock.patch.object(launch.subprocess, "run", return_value=SimpleNamespace(returncode=7)) as run:
            self.assertEqual(launch.main(), 7)
        self.assertIn("run_as_admin.bat", str(run.call_args.args[0]))

    def test_gpu_utilization_cannot_come_from_an_unrelated_adapter(self):
        samples = {"pid_1_luid_0x0_0x1_phys_0_eng_0_engtype_3D": 12,
                   "pid_2_luid_0x0_0x2_phys_0_eng_0_engtype_3D": 95}
        self.assertEqual(telemetry.gpu_utilization(samples, adapter_luid="luid_0x00000000_0x00000001"), 12)
        self.assertIsNone(telemetry.gpu_utilization(samples))

    def test_linked_physical_gpu_does_not_replace_selected_physical_engine_load(self):
        samples = {"pid_1_luid_0x0_0x1_phys_0_eng_0_engtype_3D": 12,
                   "pid_2_luid_0x0_0x1_phys_1_eng_0_engtype_3D": 95}
        self.assertEqual(telemetry.gpu_utilization(samples, adapter_luid="luid_0x0_0x1"), 12)
        self.assertIsNone(telemetry.gpu_utilization(samples))

    def test_native_dependency_check_uses_the_production_pin_source(self):
        with tempfile.TemporaryDirectory() as folder:
            constraints = Path(folder) / 'constraints.txt'
            constraints.write_text('psutil==7.9.1\n', encoding='utf-8')
            with mock.patch.object(windows_native, 'CONSTRAINTS_PATH', constraints):
                self.assertEqual(windows_native._psutil_pin(), '7.9.1')
            constraints.write_text('psutil==7.9.1\npsutil==7.9.2\n', encoding='utf-8')
            with mock.patch.object(windows_native, 'CONSTRAINTS_PATH', constraints), self.assertRaises(ValueError):
                windows_native._psutil_pin()

    def test_wddm_clock_remains_available_when_pdh_collection_fails(self):
        reader = telemetry.WindowsTelemetry.__new__(telemetry.WindowsTelemetry)
        reader.query = 1
        reader.counters = {}
        reader.api = mock.Mock()
        reader.api.PdhCollectQueryData.return_value = 1
        with mock.patch.object(telemetry, "sample_gpu", return_value={"gpu_clock": 280}):
            sample = reader.sample()
        self.assertEqual(sample["gpu_clock"], 280)
        self.assertEqual(sample["thermal_zones"], [])

    def test_relative_firmware_references_are_resolved(self):
        text = FIRMWARE.replace(r"\_SB.GPU0 })", "GPU0 })")
        self.assertEqual(discovery.parse_thermal_profile(text, "SDM8380")["gpu_zones"], [r"\_SB.GFX7"])

    def test_mixed_absolute_and_unresolved_references_are_not_treated_as_gpu_only(self):
        text = FIRMWARE.replace(r"\_SB.GPU0 })", r"\_SB.GPU0, UNKNOWN })")
        self.assertEqual(discovery.parse_thermal_profile(text, "SDM8380")["gpu_zones"], [])

    def test_quoted_fake_firmware_objects_are_not_discovered(self):
        text = r'''Scope (\_SB) {
            Name (TEXT, "ThermalZone (FAKE) { Name (_HID, \"QCOM0C58\") }")
        }'''
        self.assertEqual(discovery.parse_thermal_profile(text, "SDM8380")["cpu_zones"], [])

    def test_unusable_evidence_cache_is_rejected(self):
        profile = discovery.parse_thermal_profile(FIRMWARE, "SDM8380")
        profile.update(dsdt_sha256="a" * 64, schema_version=discovery.PROFILE_VERSION)
        profile["evidence"][r"\_SB.HOT0"] = ["invalid cached value"]
        self.assertFalse(discovery._valid_profile(profile, "a" * 64))

    def test_cache_write_failure_keeps_discovered_temperatures_available(self):
        table = bytearray(36)
        table[16:24] = b"SDM8380 "
        def disassemble(arguments, **kwargs):
            Path(arguments[-1]).with_suffix('.dsl').write_text(FIRMWARE, encoding='utf-8')
        with tempfile.TemporaryDirectory() as folder, \
                mock.patch.object(discovery, "data_directory", return_value=Path(folder)), \
                mock.patch.object(discovery, "read_dsdt", return_value=bytes(table)), \
                mock.patch.object(discovery, "prepare_disassembler", return_value=Path(folder) / 'iasl.exe'), \
                mock.patch.object(discovery.subprocess, "run", side_effect=disassemble), \
                mock.patch.object(discovery.os, "replace", side_effect=OSError("cache is read-only")):
            profile = discovery.discover_thermal_profile()
        self.assertEqual(profile['gpu_zones'], [r'\_SB.GFX7'])
        self.assertEqual(discovery.component_temperatures([{'name': r'\_SB.GFX7', 'temp': 37}], profile)['gpu_temp'], 37)

    def test_optional_pdh_open_failure_does_not_disable_wddm_clock(self):
        api = mock.Mock()
        api.PdhOpenQueryW.return_value = 1
        with mock.patch.object(telemetry, "discover_thermal_profile", return_value={}), \
                mock.patch.object(telemetry.ctypes, "WinDLL", return_value=api), \
                mock.patch.object(telemetry, "sample_gpu", return_value={"gpu_clock": 280}):
            reader = telemetry.WindowsTelemetry()
            sample = reader.sample()
        self.assertEqual(sample['gpu_clock'], 280)
        self.assertEqual(sample['thermal_zones'], [])
        self.assertEqual(api.PdhOpenQueryW.call_count, 1)

    def test_optional_pdh_provider_reopens_after_backoff_while_gpu_clock_keeps_updating(self):
        api = mock.Mock()
        def open_query(_source, _data, pointer):
            if api.PdhOpenQueryW.call_count == 1:
                return 1
            ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p))[0] = 123
            return 0
        api.PdhOpenQueryW.side_effect = open_query
        api.PdhAddEnglishCounterW.return_value = 0
        api.PdhCollectQueryData.return_value = 0
        with mock.patch.object(telemetry, 'discover_thermal_profile', return_value={}), \
                mock.patch.object(telemetry.ctypes, 'WinDLL', return_value=api), \
                mock.patch.object(telemetry, 'sample_gpu', return_value={'gpu_clock': 280}), \
                mock.patch.object(telemetry.time, 'monotonic', return_value=0) as clock:
            reader = telemetry.WindowsTelemetry()
            clock.return_value = 29
            self.assertEqual(reader.sample()['gpu_clock'], 280)
            self.assertEqual(api.PdhOpenQueryW.call_count, 1)
            clock.return_value = 31
            self.assertEqual(reader.sample()['gpu_clock'], 280)
            self.assertEqual(api.PdhOpenQueryW.call_count, 2)
            self.assertEqual(reader.query.value, 123)
            reader.close()

    def test_comment_markers_in_quoted_firmware_text_do_not_break_real_objects(self):
        text = FIRMWARE.replace('Device (GPU0)', 'Name (INFO, "http://local/{not-a-brace}") Device (GPU0)')
        self.assertEqual(discovery.parse_thermal_profile(text, 'SDM8380')['gpu_zones'], [r'\_SB.GFX7'])


if __name__ == "__main__":
    unittest.main()
