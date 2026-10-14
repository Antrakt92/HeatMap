"""B850 CPU tach mapping must be board/chip specific and independent of fan control."""
import unittest
from types import SimpleNamespace as NS
from unittest import mock

import overlay
from test_overlay_helpers import _FakeHardware, _FakeSensor, _fake_lhm_modules


class B850FanReadingsTests(unittest.TestCase):
    def test_sensorless_exact_b850_controller_requests_monitor_reinitialization(self):
        modules, hardware, _sensor_type = _fake_lhm_modules()
        chip = _FakeHardware("ITE", hardware.Motherboard)
        chip.Identifier = "/lpc/it8696e/0"
        board = _FakeHardware("Gigabyte B850 AORUS ELITE WIFI7 ICE", hardware.Motherboard,
                              sub_hardware=[chip])
        with mock.patch.dict("sys.modules", modules):
            data = overlay.read_sensors(NS(Hardware=[board]))
        self.assertTrue(data.get(overlay.SENSOR_REINIT_KEY))
        self.assertEqual(data[overlay.SENSOR_STATUS_KEY], overlay.SENSOR_STATUS_PARTIAL)

    def test_sensorless_other_board_does_not_inherit_b850_retry(self):
        modules, hardware, _sensor_type = _fake_lhm_modules()
        chip = _FakeHardware("ITE", hardware.Motherboard)
        chip.Identifier = "/lpc/it8696e/0"
        board = _FakeHardware("Other motherboard", hardware.Motherboard, sub_hardware=[chip])
        with mock.patch.dict("sys.modules", modules):
            data = overlay.read_sensors(NS(Hardware=[board]))
        self.assertFalse(data.get(overlay.SENSOR_REINIT_KEY))

    def read(self, board_name="Gigabyte B850 AORUS ELITE WIFI7 ICE", chip="it8696e", reverse=False):
        _, hardware, sensor_type = _fake_lhm_modules()
        readings = []
        for index, rpm in ((0, 1600), (1, 0), (4, 1650)):
            sensor = _FakeSensor(f"Fan #{index + 1}", sensor_type.Fan, rpm)
            sensor.Identifier = f"/lpc/{chip}/0/fan/{index}"
            readings.append(sensor)
        if reverse:
            readings.reverse()
        controller = _FakeHardware("ITE", hardware.Motherboard, sensors=readings)
        controller.Identifier = f"/lpc/{chip}/0"
        board = _FakeHardware(board_name, hardware.Motherboard, sub_hardware=[controller])
        data = overlay._empty_sensor_data()
        overlay._read_hardware_block(board, hardware, sensor_type, data, {})
        return data

    def test_cpu_and_optional_tach_map_by_identity_not_enumeration(self):
        for reverse in (False, True):
            data = self.read(reverse=reverse)
            self.assertFalse(data.get(overlay.SENSOR_REINIT_KEY))
            self.assertEqual((data["cpu_fan"], data["cpu_optional_fan"]), (1600, 1650))
            self.assertEqual(data["cpu_fan_id"], "/lpc/it8696e/0/fan/0")
            self.assertEqual(data["cpu_optional_fan_id"], "/lpc/it8696e/0/fan/4")
            self.assertIsNone(data["cpu_fan_pct"])
            self.assertIsNone(data["cpu_optional_fan_pct"])
            self.assertEqual(next(f["name"] for f in data["fans"] if f["id"].endswith('/1')), "System Fan #1")

    def test_other_board_or_chip_does_not_inherit_cpu_header_mapping(self):
        for kwargs in ({"board_name": "Gigabyte B550 AORUS PRO AC"}, {"chip": "it8688e"}):
            data = self.read(**kwargs)
            self.assertIsNone(data["cpu_fan"])
            self.assertIsNone(data["cpu_optional_fan"])

    def test_explicit_upstream_labels_are_preserved(self):
        self.assertEqual(overlay._board_fan_name("Gigabyte B850 AORUS ELITE WIFI7 ICE",
                                               "System Fan", "/lpc/it8696e/0/fan/0"), "System Fan")
