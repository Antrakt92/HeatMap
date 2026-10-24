import logging
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import overlay
from test_shutdown_audit import shutdown_app


class LifecycleLoggingTests(unittest.TestCase):
    def test_file_log_retains_lifecycle_info_and_exception_traceback(self):
        previous_handlers = list(overlay.log.handlers)
        previous_level = overlay.log.level
        with tempfile.TemporaryDirectory() as directory:
            try:
                path = Path(directory) / "HeatMap.log"
                with (mock.patch.object(overlay, "_get_log_path", return_value=str(path)),
                      mock.patch.object(overlay.logging, "basicConfig"),
                      mock.patch.object(overlay.log, "propagate", False)):
                    overlay._configure_logging()
                    overlay.log.info("HeatMap starting version=test pid=123")
                    try:
                        raise RuntimeError("synthetic failure")
                    except RuntimeError:
                        overlay.log.exception("HeatMap terminated unexpectedly")
                    text = path.read_text(encoding="utf-8")
            finally:
                for handler in list(overlay.log.handlers):
                    if handler not in previous_handlers:
                        overlay.log.removeHandler(handler)
                        handler.close()
                overlay.log.setLevel(previous_level)
            self.assertIn("[INFO] HeatMap starting", text)
            self.assertIn("RuntimeError: synthetic failure", text)

    def test_main_logs_start_failure_and_still_cleans_up(self):
        app = mock.Mock()
        app.run.side_effect = RuntimeError("synthetic event loop failure")
        with (
            mock.patch.object(overlay, "_runtime_dll_errors", return_value=[]),
            mock.patch.object(overlay, "acquire_single_instance", return_value=True),
            mock.patch.object(overlay, "release_single_instance") as release,
            mock.patch.object(overlay, "_is_admin", return_value=True),
            mock.patch.object(overlay, "OverlayApp", return_value=app),
            mock.patch.object(overlay.log, "info") as info,
            mock.patch.object(overlay.log, "exception") as exception,
        ):
            with self.assertRaisesRegex(RuntimeError, "synthetic event loop failure"):
                overlay.main()
        self.assertTrue(any("starting" in call.args[0] for call in info.call_args_list))
        exception.assert_called_once_with("HeatMap terminated unexpectedly")
        app.quit.assert_called_once_with()
        release.assert_called_once_with()

    def test_normal_shutdown_is_logged_only_once(self):
        app = shutdown_app()
        with mock.patch.object(overlay.log, "info") as info, mock.patch.object(overlay, "release_single_instance"):
            app.quit()
            app.quit()
        self.assertEqual(sum("shutdown requested" in call.args[0] for call in info.call_args_list), 1)


if __name__ == "__main__":
    unittest.main()
