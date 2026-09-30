import ctypes
import struct
from ctypes import wintypes
from unittest import TestCase, mock

import storage_identity as storage


class StorageIdentityTests(TestCase):
    def test_temperature_property_uses_primary_not_hottest_or_thresholds(self):
        raw = bytearray(72)
        struct.pack_into('<IIhhH', raw, 0, 40, 72, 85, 82, 3)
        struct.pack_into('<Hhhh', raw, 24, 0, 45, 82, -32768)
        struct.pack_into('<Hhhh', raw, 40, 1, 45, -274, -32768)
        struct.pack_into('<Hhhh', raw, 56, 2, 51, -274, -32768)
        primary, readings = storage._property_temperatures(raw)
        self.assertEqual(primary, 45)
        self.assertEqual([item['temp'] for item in readings], [45, 45, 51])
        # A missing composite reading must not borrow a secondary sensor.
        struct.pack_into('<h', raw, 26, -32768)
        self.assertIsNone(storage._property_temperatures(raw)[0])
        for truncated in (raw[:10], raw[:40]):
            with self.assertRaises(ValueError):
                storage._property_temperatures(truncated)

    def test_descriptor_model_offset_and_termination_are_validated(self):
        raw = bytearray(64)
        struct.pack_into('<II', raw, 0, 36, 64)
        struct.pack_into('<I', raw, 16, 36)
        raw[36:40] = b'SSD\0'
        self.assertEqual(storage._property_model(raw), 'SSD')
        struct.pack_into('<I', raw, 16, 500)
        with self.assertRaises(ValueError):
            storage._property_model(raw)

    def test_property_queries_use_metadata_access_and_close_even_on_parse_failure(self):
        kernel = mock.Mock()
        kernel.CreateFileW.return_value = 123
        kernel.DeviceIoControl.return_value = True
        with mock.patch.object(storage, '_kernel', kernel), self.assertRaises(ValueError):
            storage.disk_properties(3)
        kernel.CreateFileW.assert_called_once_with(r'\\.\PhysicalDrive3', 0, 7, None, 3, 0, None)
        kernel.CloseHandle.assert_called_once_with(123)

    def test_physical_disk_inventory_includes_unmounted_disks_and_ignores_other_aliases(self):
        kernel = mock.Mock()
        aliases = 'PhysicalDrive0\0C:\0PhysicalDrive3\0PhysicalDriveX\0PhysicalDrive4294967295\0\0'
        def query(name, buffer, size):
            self.assertIsNone(name)
            for index, value in enumerate(aliases):
                buffer[index] = value
            return len(aliases)
        kernel.QueryDosDeviceW.side_effect = query
        with mock.patch.object(storage, '_kernel', kernel):
            self.assertEqual(storage.physical_disk_numbers(), {0, 3})
        kernel.CreateFileW.assert_not_called()

    def test_failed_inventory_is_not_a_verified_empty_set(self):
        kernel = mock.Mock()
        kernel.QueryDosDeviceW.return_value = 0
        with mock.patch.object(storage, '_kernel', kernel), mock.patch.object(storage.ctypes, 'get_last_error', return_value=5):
            self.assertIsNone(storage.physical_disk_numbers())

    def test_single_extent_uses_metadata_only_access_and_closes_handle(self):
        kernel = mock.Mock()
        kernel.CreateFileW.return_value = 123

        def ioctl(handle, code, input_buffer, input_size, output, size, returned, overlapped):
            self.assertEqual(code, 0x00560000)
            record = ctypes.cast(output, ctypes.POINTER(storage._VolumeExtents)).contents
            record.count = 1
            record.extent.disk_number = 7
            ctypes.cast(returned, ctypes.POINTER(wintypes.DWORD)).contents.value = size
            return True

        kernel.DeviceIoControl.side_effect = ioctl
        with mock.patch.object(storage, '_kernel', kernel):
            self.assertEqual(storage.volume_disk_number('C:'), 7)
        kernel.CreateFileW.assert_called_once_with('\\\\.\\C:', 0, 7, None, 3, 0, None)
        kernel.CloseHandle.assert_called_once_with(123)

    def test_unknown_multi_extent_or_truncated_results_never_guess(self):
        for ok, count, length in [(False, 1, 32), (True, 2, 32), (True, 0, 32), (True, 1, 4)]:
            with self.subTest(ok=ok, count=count, length=length):
                kernel = mock.Mock()
                kernel.CreateFileW.return_value = 123

                def ioctl(*args):
                    record = ctypes.cast(args[4], ctypes.POINTER(storage._VolumeExtents)).contents
                    record.count = count
                    record.extent.disk_number = 0
                    ctypes.cast(args[6], ctypes.POINTER(wintypes.DWORD)).contents.value = length
                    return ok

                kernel.DeviceIoControl.side_effect = ioctl
                with mock.patch.object(storage, '_kernel', kernel):
                    self.assertIsNone(storage.volume_disk_number('C:'))
                kernel.CloseHandle.assert_called_once_with(123)

    def test_invalid_names_and_access_denied_do_not_query(self):
        kernel = mock.Mock()
        kernel.CreateFileW.return_value = ctypes.c_void_p(-1).value
        with mock.patch.object(storage, '_kernel', kernel):
            for name in ('', 'C:\\', '\\\\server\\share', 'not-a-drive'):
                self.assertIsNone(storage.volume_disk_number(name))
            kernel.CreateFileW.assert_not_called()
            self.assertIsNone(storage.volume_disk_number('C:'))
        kernel.DeviceIoControl.assert_not_called()
        kernel.CloseHandle.assert_not_called()

    def test_exception_still_closes_handle(self):
        kernel = mock.Mock()
        kernel.CreateFileW.return_value = 123
        kernel.DeviceIoControl.side_effect = OSError('synthetic failure')
        with mock.patch.object(storage, '_kernel', kernel), self.assertRaises(OSError):
            storage.volume_disk_number('C:')
        kernel.CloseHandle.assert_called_once_with(123)
