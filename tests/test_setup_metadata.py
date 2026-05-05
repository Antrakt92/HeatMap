import unittest

import setup


class SetupMetadataTests(unittest.TestCase):
    def test_lhm_uses_exact_net472_runtime_path(self):
        package = setup.PACKAGES["LibreHardwareMonitorLib"]

        self.assertEqual(package["version"], "0.9.5")
        self.assertEqual(
            package["dlls"],
            ["runtimes/win-x64/lib/net472/LibreHardwareMonitorLib.dll"],
        )
        self.assertNotEqual(package["dlls"], ["lib/net4/LibreHardwareMonitorLib.dll"])

    def test_hidsharp_version_and_path_match_pinned_hash(self):
        package = setup.PACKAGES["HidSharp"]

        self.assertEqual(package["version"], "2.6.4")
        self.assertEqual(
            package["url"],
            "https://www.nuget.org/api/v2/package/HidSharp/2.6.4",
        )
        self.assertEqual(package["dlls"], ["lib/net35/HidSharp.dll"])


if __name__ == "__main__":
    unittest.main()
