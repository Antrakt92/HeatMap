"""Regression coverage for selecting a backend without x64 hardware access."""
import ctypes
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

import launch
import overlay
import setup
import windows_native


class WindowsNativeTests(unittest.TestCase):
    def setUp(self):
        prepare = mock.patch.object(launch, "prepare_disassembler")
        prepare.start()
        self.addCleanup(prepare.stop)
    def test_native_arm_does_not_query_x64_runtime(self):
        with mock.patch.object(windows_native.platform, "machine", return_value="ARM64"):
            self.assertTrue(windows_native.windows_only_mode())

    def test_emulated_x64_selects_arm_host_backend(self):
        def query(_handle, process, native):
            ctypes.cast(native, ctypes.POINTER(ctypes.c_ushort))[0] = 0xAA64
            return True
        with mock.patch.object(windows_native.platform, "machine", return_value="AMD64"), \
                mock.patch.object(ctypes.windll.kernel32, "IsWow64Process2", side_effect=query):
            self.assertTrue(windows_native.windows_only_mode())

    def test_real_x64_keeps_hardware_backend(self):
        def query(_handle, process, native):
            ctypes.cast(native, ctypes.POINTER(ctypes.c_ushort))[0] = 0x8664
            return True
        with mock.patch.object(windows_native.platform, "machine", return_value="AMD64"), \
                mock.patch.object(ctypes.windll.kernel32, "IsWow64Process2", side_effect=query):
            self.assertFalse(windows_native.windows_only_mode())

    def test_arm_preflight_never_imports_clr_or_verifies_x64_bundle(self):
        with mock.patch.object(setup, "windows_only_mode", return_value=True), \
                mock.patch.object(setup, "_check_preflight_dependencies", side_effect=AssertionError), \
                mock.patch.object(setup, "verify_lib_manifest", side_effect=AssertionError), \
                mock.patch.object(setup, "check_windows_dependencies", return_value=(True, [])):
            self.assertEqual(setup.run_preflight(), (True, []))

    def test_native_samples_never_open_hardware_and_preserve_missing_temperatures(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app._windows_only = True
        app.running = True
        app._stop_event = threading.Event()
        app.lock = threading.Lock()
        memory = SimpleNamespace(percent=25, used=4 * 1024 ** 3, total=16 * 1024 ** 3)
        with mock.patch.object(app._stop_event, "wait", side_effect=lambda _: app._stop_event.set()), \
                mock.patch.object(overlay, "WindowsTelemetry") as telemetry, \
                mock.patch.object(overlay.psutil, "cpu_percent", return_value=37), \
                mock.patch.object(overlay.psutil, "virtual_memory", return_value=memory), \
                mock.patch.object(overlay, "require_hardware_access", side_effect=AssertionError), \
                mock.patch.object(overlay, "init_hardware_monitor", side_effect=AssertionError):
            telemetry.return_value.sample.return_value = {"thermal_zones": [], "gpu_load": None, "cpu_clock": None}
            app.sensor_loop()
        self.assertEqual(app.sensor_data["cpu_load"], 37)
        self.assertEqual(app.sensor_data["ram_used_gb"], 4)
        self.assertIsNone(app.sensor_data["cpu_temp"])
        self.assertIsNone(app.sensor_data["gpu_temp"])
        self.assertEqual(app.sensor_data[overlay.SENSOR_STATUS_KEY], "windows_only")

    def test_arm_storage_does_not_probe_disks(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app._windows_only = True
        with mock.patch.object(overlay, "probe_disk", side_effect=AssertionError):
            app.storage_loop()

    def test_copied_config_cannot_enable_fan_controls(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app._windows_only = True
        app._show_info_notice = mock.Mock()
        app.toggle_case_fans()
        app.toggle_gpu_fans()
        self.assertEqual(app._show_info_notice.call_count, 2)

    def test_arm_startup_disables_copied_profiles_before_any_hardware_access(self):
        config = dict(overlay._default_config(), case_fans_enabled=True,
                      case_fans_shared_enabled=True, case_fans_b850_enabled=True,
                      gpu_fans_enabled=True)
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        with mock.patch.object(overlay, "windows_only_mode", return_value=True), \
                mock.patch.object(overlay, "load_config_result", return_value=(config, None)), \
                mock.patch.object(overlay, "require_hardware_access", side_effect=AssertionError), \
                mock.patch.object(overlay, "is_pawnio_driver_installed", side_effect=AssertionError), \
                mock.patch.object(overlay.tk, "Tk", side_effect=RuntimeError("window reached")):
            with self.assertRaisesRegex(RuntimeError, "window reached"):
                app._initialize()
        self.assertIsNone(app._driver_status)
        self.assertFalse(app.config["case_fans_enabled"])
        self.assertFalse(app.config["gpu_fans_enabled"])
        self.assertTrue(config["case_fans_enabled"])
        self.assertIsNone(app.fan_worker.process)
        self.assertIsNone(app.gpu_fan_worker.process)

    def test_arm_launch_failure_does_not_spawn_or_fall_through_to_uac(self):
        with mock.patch.object(launch, "windows_only_mode", return_value=True), \
                mock.patch.object(launch, "check_windows_dependencies", return_value=(False, ["missing psutil"])), \
                mock.patch.object(launch.subprocess, "Popen") as popen:
            self.assertEqual(launch.main(), 1)
        popen.assert_not_called()

    def test_x64_launcher_hands_back_to_existing_launcher(self):
        with mock.patch.object(launch, "windows_only_mode", return_value=False), \
                mock.patch.object(launch.subprocess, "Popen") as popen:
            self.assertEqual(launch.main(native_only=True), 20)
        popen.assert_not_called()

    def test_arm_launcher_uses_pythonw_without_shell_or_elevation(self):
        with mock.patch.object(launch, "windows_only_mode", return_value=True), \
                mock.patch.object(launch, "check_windows_dependencies", return_value=(True, [])), \
                mock.patch.object(launch.subprocess, "Popen") as popen:
            self.assertEqual(launch.main(), 0)
        args, kwargs = popen.call_args
        self.assertTrue(args[0][0].endswith("pythonw.exe"))
        self.assertNotIn("shell", kwargs)


if __name__ == "__main__":
    unittest.main()
