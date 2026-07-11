import os
import unittest


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEDULER_HELPER = os.path.join(ROOT_DIR, "tools", "test_task_scheduler_integration.ps1")
LAUNCHER_HELPER = os.path.join(ROOT_DIR, "tools", "test_launcher_integration.ps1")
WORKFLOW = os.path.join(ROOT_DIR, ".github", "workflows", "windows-integration.yml")


class WindowsIntegrationAssetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(SCHEDULER_HELPER, "r", encoding="utf-8") as stream:
            cls.scheduler_helper = stream.read()
        with open(LAUNCHER_HELPER, "r", encoding="utf-8") as stream:
            cls.launcher_helper = stream.read()
        with open(WORKFLOW, "r", encoding="utf-8") as stream:
            cls.workflow = stream.read()

    def test_scheduler_fixture_is_unique_least_privilege_and_disposable(self):
        self.assertIn("[Guid]::NewGuid()", self.scheduler_helper)
        self.assertIn("_build_autostart_task_xml", self.scheduler_helper)
        self.assertIn("_classify_autostart_task", self.scheduler_helper)
        self.assertIn('classification -eq "safe_current"', self.scheduler_helper)
        self.assertIn("finally {", self.scheduler_helper)
        self.assertIn("Unregister-ScheduledTask", self.scheduler_helper)
        self.assertNotIn("-RunLevel Highest", self.scheduler_helper)
        self.assertNotIn('Register-ScheduledTask -TaskName "HWMonitorOverlay"', self.scheduler_helper)

    def test_scheduler_fixture_uses_in_memory_registration_and_export(self):
        self.assertIn("Register-ScheduledTask -TaskName $taskName -Xml $productionXml", self.scheduler_helper)
        self.assertIn("Export-ScheduledTask -TaskName $taskName", self.scheduler_helper)
        self.assertNotIn("Set-Content", self.scheduler_helper)

    def test_launcher_fixture_intercepts_uac_endpoint_and_covers_required_cases(self):
        self.assertIn("class FakePowerShellCapture", self.launcher_helper)
        self.assertIn("%HEATMAP_FIXTURE_POWERSHELL%", self.launcher_helper)
        self.assertIn('HEATMAP_FIXTURE_PREFLIGHT_MODE = "warning"', self.launcher_helper)
        self.assertIn('HEATMAP_FIXTURE_PREFLIGHT_MODE = "failure"', self.launcher_helper)
        self.assertIn("NoPythonExitCode", self.launcher_helper)
        self.assertIn("could not find a usable Python interpreter", self.launcher_helper)
        self.assertIn("HeatMap_launcher_*", self.launcher_helper)
        self.assertIn("HeatMap_preflight_*", self.launcher_helper)
        self.assertIn("first invalid Python & shims", self.launcher_helper)
        self.assertIn("O'Brien & Unicode Ж", self.launcher_helper)

    def test_windows_workflow_runs_both_real_behavior_helpers(self):
        self.assertIn("runs-on: windows-latest", self.workflow)
        self.assertIn("tools/test_task_scheduler_integration.ps1", self.workflow)
        self.assertIn("tools/test_launcher_integration.ps1", self.workflow)


if __name__ == "__main__":
    unittest.main()
