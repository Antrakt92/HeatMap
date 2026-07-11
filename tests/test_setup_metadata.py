import unittest

import setup


class SetupMetadataTests(unittest.TestCase):
    def test_lhm_uses_exact_net472_runtime_path(self):
        _runtime_lock, entries = setup.load_runtime_lock()
        package = entries["lib/librehardwaremonitorlib.dll"]

        self.assertEqual(package["version"], "0.9.5")
        self.assertEqual(
            package["package_path"],
            "runtimes/win-x64/lib/net472/LibreHardwareMonitorLib.dll",
        )
        self.assertNotEqual(package["package_path"], "lib/net4/LibreHardwareMonitorLib.dll")

    def test_hidsharp_version_and_path_match_pinned_hash(self):
        _runtime_lock, entries = setup.load_runtime_lock()
        package = entries["lib/hidsharp.dll"]

        self.assertEqual(package["version"], "2.6.4")
        self.assertEqual(package["package_path"], "lib/net35/HidSharp.dll")
        self.assertEqual(
            setup._runtime_package_url(package["package"], package["version"]),
            "https://api.nuget.org/v3-flatcontainer/hidsharp/2.6.4/hidsharp.2.6.4.nupkg",
        )


if __name__ == "__main__":
    unittest.main()
