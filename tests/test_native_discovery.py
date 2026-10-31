import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import native_discovery as discovery
import volume_probe


FIRMWARE = r'''
Scope (\_SB) {
    Device (GPU0) { Name (HHID, "QCOM0C36") }
    Device (CPU0) { Name (_HID, "ACPI0007") }
    ThermalZone (HOT0) { Name (_HID, "QCOM0C58") Name (_UID, Zero) }
    ThermalZone (HOT1) { Name (_HID, "QCOM0C59") Name (_UID, Zero) }
    ThermalZone (GFX7) {
        Name (_HID, "QCOM0C91") Name (_UID, Zero)
        Name (_TZD, Package (One) { \_SB.GPU0 })
    }
    ThermalZone (BOARD) {
        Name (_HID, "QCOM0C5E")
        Name (_TZD, Package (One) { \_SB.CPU0 })
    }
    ThermalZone (MIXED) {
        Name (_HID, "QCOM0C5E")
        Name (_TZD, Package (2) { \_SB.CPU0, \_SB.GPU0 })
    }
}
'''


class NativeDiscoveryTests(unittest.TestCase):
    def test_uses_firmware_associations_not_hardcoded_zone_numbers(self):
        profile = discovery.parse_thermal_profile(FIRMWARE, "SDM8380")
        self.assertEqual(profile["cpu_zones"], [r"\_SB.HOT0", r"\_SB.HOT1"])
        self.assertEqual(profile["gpu_zones"], [r"\_SB.GFX7"])
        self.assertNotIn(r"\_SB.BOARD", profile["cpu_zones"])
        self.assertEqual(profile["cpu_label"], "SoC")

    def test_other_firmware_uses_explicit_processor_association_with_zone_label(self):
        profile = discovery.parse_thermal_profile(FIRMWARE, "OTHER")
        self.assertEqual(profile["cpu_zones"], [r"\_SB.BOARD"])
        self.assertEqual(profile["cpu_label"], "Thermal zone")

    def test_ambiguous_duplicate_zone_definitions_are_not_selected(self):
        text = FIRMWARE.replace('ThermalZone (HOT1)', 'ThermalZone (HOT0)')
        self.assertEqual(discovery.parse_thermal_profile(text, "SDM8380")["cpu_zones"], [])

    def test_unassociated_hotter_board_sensor_cannot_become_cpu_or_gpu_temperature(self):
        profile = discovery.parse_thermal_profile(FIRMWARE, "SDM8380")
        zones = [{"name": r"\_SB.HOT0", "temp": 42}, {"name": r"\_SB.HOT1", "temp": 44},
                 {"name": r"\_SB.GFX7", "temp": 39}, {"name": r"\_SB.BOARD", "temp": 90}]
        result = discovery.component_temperatures(zones, profile)
        self.assertEqual(result["cpu_temp"], 44)
        self.assertEqual(result["gpu_temp"], 39)
        self.assertEqual(discovery.component_temperatures(zones[-1:], profile), {})

    def test_bad_and_old_firmware_caches_are_rejected(self):
        profile = discovery.parse_thermal_profile(FIRMWARE, "SDM8380")
        profile.update(dsdt_sha256="a" * 64, schema_version=discovery.PROFILE_VERSION)
        self.assertTrue(discovery._valid_profile(profile, "a" * 64))
        self.assertFalse(discovery._valid_profile(profile, "b" * 64))
        profile["cpu_zones"] = 12
        self.assertFalse(discovery._valid_profile(profile, "a" * 64))
        self.assertFalse(discovery._valid_profile([], "a" * 64))

    def test_cached_tool_is_verified_and_mismatched_download_never_replaces_it(self):
        with tempfile.TemporaryDirectory() as folder:
            tool = Path(folder) / "tools/acpica-20260408/iasl.exe"
            tool.parent.mkdir(parents=True)
            tool.write_bytes(b"old")
            with mock.patch.object(discovery, "data_directory", return_value=Path(folder)), \
                    mock.patch.object(discovery.urllib.request, "urlopen", return_value=io.BytesIO(b"bad")):
                with self.assertRaisesRegex(OSError, "verification"):
                    discovery.prepare_disassembler()
            self.assertEqual(tool.read_bytes(), b"old")

    def test_offline_prepared_tool_never_downloads_again(self):
        with tempfile.TemporaryDirectory() as folder:
            tool = Path(folder) / "tools/acpica-20260408/iasl.exe"
            tool.parent.mkdir(parents=True)
            tool.write_bytes(b"verified fixture")
            with mock.patch.object(discovery, "data_directory", return_value=Path(folder)), \
                    mock.patch.object(discovery, "TOOL_SIZE", len(b"verified fixture")), \
                    mock.patch.object(discovery, "TOOL_SHA256", hashlib.sha256(b"verified fixture").hexdigest()), \
                    mock.patch.object(discovery.urllib.request, "urlopen", side_effect=AssertionError):
                self.assertEqual(discovery.prepare_disassembler(), tool)

    def test_volume_capacity_precedes_identity_and_bounded_native_device_query(self):
        from types import SimpleNamespace
        order = []
        with mock.patch.object(volume_probe.psutil, "disk_usage", return_value=SimpleNamespace(total=100, free=40)), \
                mock.patch.object(volume_probe, "volume_disk_number", side_effect=lambda _: order.append("identity") or 2), \
                mock.patch.object(volume_probe, "disk_properties", side_effect=lambda *a, **kw: order.append(kw) or
                                  {"name": "KIOXIA Model", "disk_number": 2}) as properties:
            result = volume_probe._query("C:\\", lambda _: order.append("capacity"), include_model=True)
        self.assertEqual(order[:2], ["capacity", "identity"])
        properties.assert_called_once_with(2, include_vendor=True)
        validated = volume_probe._validate(result, "C:\\")
        self.assertEqual(validated["volume"]["device_name"], "KIOXIA Model")

    def test_model_without_proven_disk_identity_is_rejected(self):
        result = {"volume": {"name": "C:", "total_bytes": 100, "free_bytes": 40, "device_name": "SSD"}}
        with self.assertRaises(ValueError):
            volume_probe._validate(result, "C:\\")


if __name__ == "__main__":
    unittest.main()
