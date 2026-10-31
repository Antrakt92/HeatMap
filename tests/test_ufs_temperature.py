import ctypes
import struct
import unittest
from unittest import mock

import storage_identity as storage
import volume_probe


def reply(value=114, attribute=0x18):
    return struct.pack('<12I', 48, 48, 5, 2, attribute, 0, 40, 4, 0, 0, 0, 0) + value.to_bytes(4, 'big')


class UfsTemperatureTests(unittest.TestCase):
    def test_reads_current_case_attribute_in_big_endian_with_correct_offset(self):
        self.assertEqual(storage._ufs_temperature(reply()), 34)
        self.assertEqual(storage._ufs_temperature(reply(115)), 35)

    def test_unavailable_values_bounds_and_wrong_protocol_never_become_current_temperature(self):
        for raw in (reply(0), reply(80), reply(255), reply(attribute=0x19), reply(attribute=0x1A),
                    reply()[:51], reply()[:8] + struct.pack('<I', 3) + reply()[12:]):
            with self.subTest(raw=raw):
                self.assertIsNone(storage._ufs_temperature(raw))

    def test_native_query_is_read_only_and_validates_driver_response(self):
        def ioctl(_handle, code, incoming, incoming_size, outgoing, outgoing_size, returned, _overlapped):
            self.assertEqual(code, 0x002D1400)
            header = struct.unpack('<12I', ctypes.string_at(incoming, 48))
            self.assertEqual(header[:6], (50, 0, 5, 2, 0x18, 0))
            self.assertEqual(header[7], 512)
            data = reply()
            ctypes.memmove(outgoing, data, len(data))
            ctypes.cast(returned, ctypes.POINTER(storage.wintypes.DWORD))[0] = len(data)
            return True
        with mock.patch.object(storage._kernel, 'DeviceIoControl', side_effect=ioctl):
            self.assertEqual(storage._read_ufs_temperature(123), 34)

    def test_temperature_without_proven_volume_device_mapping_is_rejected(self):
        data = {'volume': {'name': 'C:', 'total_bytes': 100, 'free_bytes': 40, 'temp': 34}}
        with self.assertRaises(ValueError):
            volume_probe._validate(data, 'C:\\')
        data['volume'].update(disk_number=0, device_name='KIOXIA',
                              temperature_source='Windows UFS case temperature attribute')
        validated = volume_probe._validate(data, 'C:\\')['volume']
        self.assertEqual(validated['temp'], 34)
        self.assertEqual(validated['temperature_source'], 'Windows UFS case temperature attribute')


if __name__ == '__main__':
    unittest.main()
