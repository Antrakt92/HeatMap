import os
import unittest


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAUNCHER_PATH = os.path.join(ROOT_DIR, "run_as_admin.bat")


class LauncherPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(LAUNCHER_PATH, "r", encoding="utf-8") as f:
            cls.launcher = f.read()
        cls.launcher_lower = cls.launcher.lower()

    def test_launcher_uses_expected_interpreter_priority(self):
        self.assertIn("setlocal", self.launcher_lower)
        self.assertIn("%app_dir%.venv\\scripts", self.launcher_lower)
        self.assertIn("%app_dir%venv\\scripts", self.launcher_lower)
        self.assertIn("where python.exe", self.launcher_lower)
        self.assertIn("path_python_seen", self.launcher_lower)

    def test_launcher_runs_preflight_before_elevation(self):
        preflight_pos = self.launcher_lower.find("setup_path%\" --preflight")
        runas_pos = self.launcher_lower.find("-verb runas")

        self.assertNotEqual(preflight_pos, -1)
        self.assertNotEqual(runas_pos, -1)
        self.assertLess(preflight_pos, runas_pos)

    def test_launcher_uses_pythonw_for_elevated_overlay(self):
        self.assertIn("pythonw.exe", self.launcher_lower)
        self.assertIn("start-process", self.launcher_lower)
        self.assertIn("-filepath", self.launcher_lower)
        self.assertIn("-workingdirectory", self.launcher_lower)
        self.assertIn("-verb runas", self.launcher_lower)

    def test_launcher_has_no_bare_pythonw_fallback(self):
        self.assertNotIn("start-process pythonw", self.launcher_lower)
        self.assertNotIn("start-process 'pythonw", self.launcher_lower)
        self.assertNotIn('start-process "pythonw', self.launcher_lower)


if __name__ == "__main__":
    unittest.main()
