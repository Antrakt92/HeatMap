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
        self.assertIn("where.exe\" python.exe", self.launcher_lower)
        self.assertIn("call :probe_candidate", self.launcher_lower)
        self.assertIn("every candidate must have adjacent pythonw.exe and pass setup.py --preflight", self.launcher_lower)
        self.assertNotIn("call :try_python_pair", self.launcher_lower)
        self.assertNotIn("path_python_seen", self.launcher_lower)

    def test_launcher_runs_preflight_before_elevation(self):
        probe_call_pos = self.launcher_lower.find("call :probe_candidate")
        preflight_pos = self.launcher_lower.find("setup_path%\" --preflight")
        runas_pos = self.launcher_lower.find("-verb runas")

        self.assertNotEqual(probe_call_pos, -1)
        self.assertNotEqual(preflight_pos, -1)
        self.assertNotEqual(runas_pos, -1)
        self.assertLess(probe_call_pos, runas_pos)

    def test_launcher_continues_after_candidate_preflight_failure(self):
        self.assertIn(":candidate_failed", self.launcher_lower)
        self.assertIn("candidate failed preflight", self.launcher_lower)
        self.assertIn("if not defined py_exe call :try_path_python", self.launcher_lower)

    def test_launcher_uses_pythonw_for_elevated_overlay(self):
        self.assertIn("pythonw.exe", self.launcher_lower)
        self.assertIn("start-process", self.launcher_lower)
        self.assertIn("-filepath", self.launcher_lower)
        self.assertIn("-workingdirectory", self.launcher_lower)
        self.assertIn("-verb runas", self.launcher_lower)
        self.assertIn("$env:heatmap_pyw_exe", self.launcher_lower)
        self.assertIn("$env:heatmap_overlay_path", self.launcher_lower)
        self.assertNotIn("-filepath '%pyw_exe%'", self.launcher_lower)
        self.assertIn("%systemroot%\\system32\\windowspowershell", self.launcher_lower)

    def test_launcher_preserves_warning_without_blocking_elevation(self):
        self.assertIn("pathlib.path(sys.argv[1]).read_text", self.launcher_lower)
        self.assertIn("last_preflight_warning.txt", self.launcher_lower)
        self.assertNotIn("call :show_warning_from_log", self.launcher_lower)
        self.assertNotIn("heatmap preflight warning", self.launcher_lower)
        self.assertNotIn("messagebox]::show($msg, 'heatmap preflight warning'", self.launcher_lower)

    def test_launcher_hands_paths_to_powershell_via_environment(self):
        self.assertIn("$env:heatmap_precheck_log", self.launcher_lower)
        self.assertIn("$env:heatmap_pyw_exe", self.launcher_lower)
        self.assertIn("$env:heatmap_overlay_path", self.launcher_lower)
        self.assertNotIn("-literalpath '%precheck_log%'", self.launcher_lower)
        self.assertNotIn("-filepath '%pyw_exe%'", self.launcher_lower)

    def test_launcher_has_no_bare_pythonw_fallback(self):
        self.assertNotIn("start-process pythonw", self.launcher_lower)
        self.assertNotIn("start-process 'pythonw", self.launcher_lower)
        self.assertNotIn('start-process "pythonw', self.launcher_lower)


if __name__ == "__main__":
    unittest.main()
