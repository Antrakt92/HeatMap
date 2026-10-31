import ctypes as c
import unittest
from unittest import mock

import windows_gpu as gpu


class WindowsGpuTests(unittest.TestCase):
    def setUp(self):
        gpu._gpu_api.cache_clear()
        self.addCleanup(gpu._gpu_api.cache_clear)

    def test_current_frequency_is_not_replaced_with_maximum(self):
        self.assertEqual(gpu.current_mhz(280_000_000, 1_107_000_000), 280)
        self.assertEqual(gpu.current_mhz(280_000_000, 0), 280)
        self.assertEqual(gpu.current_mhz(0, 1_107_000_000), 0)
        self.assertIsNone(gpu.current_mhz(0, 0))
        self.assertIsNone(gpu.current_mhz(1 << 63, 1_107_000_000))

    def api(self, fail_node=False):
        api = mock.Mock()
        def enumerate_adapters(pointer):
            enumeration = c.cast(pointer, c.POINTER(gpu._Enumeration)).contents
            enumeration.count = 2
            enumeration.adapters[0].handle = 123
            enumeration.adapters[1].handle = 456
            return 0
        def query_info(pointer):
            request = c.cast(pointer, c.POINTER(gpu._Query)).contents
            if request.kind == 15:
                c.cast(request.data, c.POINTER(gpu.w.UINT))[0] = 4 if request.handle == 456 else 3
                return 0
            if request.kind == 8:
                name = c.create_unicode_buffer("Adreno test")
                c.memmove(request.data, name, c.sizeof(name))
                return 0
            if request.kind == 61:
                if fail_node:
                    raise OSError("driver reset")
                node = c.cast(request.data, c.POINTER(gpu._Node)).contents
                node.frequency, node.maximum = 280_000_000, 1_107_000_000
                return 0
            return 1
        api.D3DKMTEnumAdapters2.side_effect = enumerate_adapters
        api.D3DKMTQueryAdapterInfo.side_effect = query_info
        return api

    def test_skips_software_adapter_and_closes_every_opened_handle(self):
        api = self.api()
        with mock.patch.object(gpu.c, "WinDLL", return_value=api):
            result = gpu.sample_gpu()
        self.assertEqual(result["gpu_clock"], 280)
        self.assertEqual(result["gpu_clock_adapter"], "Adreno test")
        self.assertEqual(api.D3DKMTCloseAdapter.call_count, 2)

    def test_api_binding_is_reused_but_hardware_is_enumerated_and_closed_each_sample(self):
        api = self.api()
        with mock.patch.object(gpu.c, "WinDLL", return_value=api) as dll:
            gpu.sample_gpu()
            gpu.sample_gpu()
        self.assertEqual(dll.call_count, 1)
        self.assertEqual(api.D3DKMTEnumAdapters2.call_count, 2)
        self.assertEqual(api.D3DKMTCloseAdapter.call_count, 4)

    def test_driver_failure_still_releases_all_handles(self):
        api = self.api(fail_node=True)
        with mock.patch.object(gpu.c, "WinDLL", return_value=api):
            with self.assertRaises(OSError):
                gpu.sample_gpu()
        self.assertEqual(api.D3DKMTCloseAdapter.call_count, 2)


if __name__ == "__main__":
    unittest.main()
