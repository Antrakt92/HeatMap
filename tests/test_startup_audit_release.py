import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import setup


class StartupAuditTests(unittest.TestCase):
    def test_restore_refuses_unknown_overlay_mutex_state(self):
        kernel = mock.Mock()
        kernel.CreateMutexW.return_value = 0
        kernel.GetLastError.return_value = 5
        with mock.patch.object(setup.ctypes.windll, "kernel32", kernel):
            with self.assertRaisesRegex(setup.SetupError, "cannot verify.*5"):
                setup._is_overlay_running()
        kernel.CloseHandle.assert_not_called()

    def test_manifest_reports_inaccessible_directory(self):
        with mock.patch.object(setup.os, "listdir", side_effect=PermissionError("access denied")):
            ok, messages = setup.verify_lib_manifest()
        self.assertFalse(ok)
        self.assertIn("cannot list DLL directory", " ".join(messages))

    def test_manifest_reports_file_disappearing_during_verification(self):
        with mock.patch.object(setup, "_sha256_file", side_effect=FileNotFoundError("removed")):
            ok, messages = setup.verify_lib_manifest()
        self.assertFalse(ok)
        self.assertIn("cannot read DLL", " ".join(messages))

    @unittest.skipUnless(os.name == "nt", "requires real cmd.exe expansion")
    def test_launcher_initialization_preserves_bang_paths_with_delayed_expansion(self):
        # Execute the production initialization only; never enter elevation or hardware paths.
        root = Path(setup.APP_DIR)
        for launcher_name, boundary, variable in (
            ("run_as_admin.bat", "call :probe_candidate", "APP_DIR"),
            ("enable_case_fans.bat", 'if not exist ', "HEATMAP_ACTIVATE_DIR"),
        ):
            with self.subTest(launcher=launcher_name), tempfile.TemporaryDirectory(prefix="HeatMap ! paths ") as directory:
                source = (root / launcher_name).read_text(encoding="utf-8")
                initialization = source.split(boundary, 1)[0]
                self.assertNotEqual(initialization, source)
                fixture = Path(directory) / launcher_name
                fixture.write_text(initialization + f"set {variable}\nexit /b 0\n", encoding="utf-8")
                result = subprocess.run(
                    [os.path.join(os.environ["SystemRoot"], "System32", "cmd.exe"),
                     "/d", "/v:on", "/c", launcher_name],
                    cwd=directory, capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"{variable}={directory}\\", result.stdout)


if __name__ == "__main__":
    unittest.main()
