import ctypes
from ctypes import wintypes
from unittest import TestCase, mock

import storage_identity as storage


class StorageIdentityTests(TestCase):
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
