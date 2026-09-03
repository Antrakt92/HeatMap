import ctypes
import threading
import unittest
from unittest import mock

import overlay


class ClockStopEvent:
    def __init__(self, iterations):
        self.now = 100.0
        self.remaining = iterations

    def is_set(self):
        return self.remaining <= 0

    def wait(self, seconds):
        self.now += seconds
        self.remaining -= 1
        return self.is_set()

    def set(self):
        self.remaining = 0


def sensor_app(iterations, computer=None):
    app = overlay.OverlayApp.__new__(overlay.OverlayApp)
    app.computer = computer
    app.running = True
    app.lock = threading.Lock()
    app.sensor_data = {}
    app._sensor_start_time = 0
    app._stop_event = ClockStopEvent(iterations)
    return app


class SensorLifecycleTests(unittest.TestCase):
    def test_window_construction_does_not_wait_for_hardware_open(self):
        with (
            mock.patch.object(overlay, "load_config_result", return_value=({}, None)),
            mock.patch.object(overlay, "is_pawnio_driver_installed", return_value=True),
            mock.patch.object(overlay, "init_hardware_monitor") as initialize,
            mock.patch.object(overlay.tk, "Tk", side_effect=RuntimeError("window reached")),
        ):
            with self.assertRaisesRegex(RuntimeError, "window reached"):
                overlay.OverlayApp()
        initialize.assert_not_called()

    def test_failed_reinitialization_keeps_retrying_without_holding_ui_lock(self):
        for failure in ("hint", "exception"):
            with self.subTest(failure=failure):
                old = mock.Mock()
                recovered = mock.Mock()
                app = sensor_app(35, old)
                attempts = []

                def initialize():
                    self.assertFalse(app.lock.locked())
                    attempts.append(app._stop_event.now)
                    return None if len(attempts) == 1 else recovered

                def sample(computer, update_storage=True):
                    if computer is old:
                        if failure == "exception":
                            raise RuntimeError("synthetic device failure")
                        return {overlay.SENSOR_REINIT_KEY: True}
                    return {"cpu_temp": 45}

                with (
                    mock.patch.object(overlay, "read_sensors", side_effect=sample),
                    mock.patch.object(overlay, "init_hardware_monitor", side_effect=initialize),
                    mock.patch.object(overlay.psutil, "cpu_percent"),
                    mock.patch.object(overlay.time, "monotonic", side_effect=lambda: app._stop_event.now),
                    mock.patch.object(overlay, "log"),
                ):
                    app.sensor_loop()

                self.assertEqual(len(attempts), 2)
                self.assertGreaterEqual(attempts[1] - attempts[0], overlay.SENSOR_INIT_RETRY_SECONDS)
                old.Close.assert_called_once_with()
                recovered.Close.assert_called_once_with()
                self.assertIsNone(app.computer)

    def test_warmup_does_not_reopen_hardware_every_sample(self):
        app = sensor_app(20)
        computers = []
        attempts = []

        def initialize():
            attempts.append(app._stop_event.now)
            computer = mock.Mock()
            computers.append(computer)
            return computer

        with (
            mock.patch.object(overlay, "read_sensors", return_value={overlay.SENSOR_REINIT_KEY: True}),
            mock.patch.object(overlay, "init_hardware_monitor", side_effect=initialize),
            mock.patch.object(overlay.psutil, "cpu_percent") as prime,
            mock.patch.object(overlay.time, "monotonic", side_effect=lambda: app._stop_event.now),
            mock.patch.object(overlay, "log"),
        ):
            app.sensor_loop()

        self.assertEqual(len(attempts), 2)
        self.assertGreaterEqual(attempts[1] - attempts[0], overlay.SENSOR_INIT_RETRY_SECONDS)
        prime.assert_called_once_with(interval=0)
        for computer in computers:
            computer.Close.assert_called_once_with()

    def test_shutdown_during_open_closes_unpublished_monitor(self):
        app = sensor_app(10)
        computer = mock.Mock()

        def initialize():
            app._stop_event.set()
            return computer

        with (
            mock.patch.object(overlay, "init_hardware_monitor", side_effect=initialize),
            mock.patch.object(overlay, "read_sensors") as sample,
            mock.patch.object(overlay.psutil, "cpu_percent"),
            mock.patch.object(overlay, "log"),
        ):
            app.sensor_loop()

        sample.assert_not_called()
        computer.Close.assert_called_once_with()
        self.assertIsNone(app.computer)

    def test_worker_window_result_has_pointer_width(self):
        result_pointer = overlay.user32.SendMessageTimeoutW.argtypes[-1]
        self.assertEqual(ctypes.sizeof(result_pointer._type_), ctypes.sizeof(ctypes.c_void_p))

    def test_inactive_peek_does_not_poll_cursor_or_schedule_timer(self):
        for enabled, topmost in ((False, False), (True, True)):
            with self.subTest(enabled=enabled, topmost=topmost):
                app = sensor_app(1)
                app.peek_enabled = enabled
                app.topmost = topmost
                app._cursor_was_at_peek_edge = False
                app.root = mock.Mock()
                with mock.patch.object(overlay.user32, "GetCursorPos", return_value=False) as cursor:
                    app._poll_peek_edge()
                cursor.assert_not_called()
                app.root.after.assert_not_called()

    def test_peek_timer_restarts_once_and_cancels_when_disabled(self):
        app = sensor_app(1)
        app.peek_enabled = True
        app.topmost = False
        app.root = mock.Mock()
        app.root.after.side_effect = ["timer-1", "timer-2"]

        app._schedule_peek_poll()
        app._schedule_peek_poll()
        self.assertEqual(app._peek_poll_after_id, "timer-2")
        app.root.after_cancel.assert_called_once_with("timer-1")
        app.peek_enabled = False
        app._schedule_peek_poll()

        self.assertIsNone(app._peek_poll_after_id)
        self.assertEqual(app.root.after.call_count, 2)
        self.assertEqual(app.root.after_cancel.call_args.args, ("timer-2",))


if __name__ == "__main__":
    unittest.main()
