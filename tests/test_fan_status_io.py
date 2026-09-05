import io
import json
import os
import tempfile
import threading
import unittest
from unittest import mock

import case_fans


class FanStatusIOTests(unittest.TestCase):
    def test_busy_reader_uses_verified_snapshot_only_until_it_expires(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "status.json")
            client = case_fans.FanWorkerClient(directory)
            client.status_path = path
            client.started = 50
            client.process = mock.Mock(pid=os.getpid(), stdin=io.StringIO())
            client.process.poll.return_value = None
            with mock.patch.object(case_fans.time, "time", return_value=100):
                case_fans.write_status(path, "active", command_pct=80)
                self.assertEqual(client.poll()["command_pct"], 80)
            with mock.patch.object(case_fans, "open_status_file", side_effect=PermissionError("busy")):
                with mock.patch.object(case_fans.time, "time", return_value=109):
                    self.assertEqual(client.poll()["state"], "active")
                with mock.patch.object(case_fans.time, "time", return_value=111):
                    self.assertEqual(client.poll()["state"], "error")

    @unittest.skipUnless(os.name == "nt", "Windows file sharing regression")
    def test_our_reader_keeps_complete_snapshot_during_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "состояние.json")
            case_fans.write_status(path, "checking")
            with case_fans.open_status_file(path) as reader, mock.patch.object(case_fans.time, "sleep") as sleep:
                case_fans.write_status(path, "active", command_pct=90)
                self.assertEqual(json.load(reader)["state"], "checking")
                sleep.assert_not_called()
            with case_fans.open_status_file(path) as reader:
                self.assertEqual(json.load(reader)["command_pct"], 90)

    def test_permanent_denial_is_bounded_and_keeps_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "status.json")
            case_fans.write_status(path, "checking")
            error = PermissionError("denied")
            error.winerror = 5
            with mock.patch.object(case_fans, "replace_status_file", side_effect=error) as replace, \
                 mock.patch.object(case_fans.time, "sleep") as sleep:
                with self.assertRaises(PermissionError):
                    case_fans.write_status(path, "active", command_pct=80)
            self.assertEqual(replace.call_count, 7)
            self.assertAlmostEqual(sum(c.args[0] for c in sleep.call_args_list), 0.63)
            with open(path, encoding="utf-8") as reader:
                self.assertEqual(json.load(reader)["state"], "checking")

    @unittest.skipUnless(os.name == "nt", "Windows file sharing regression")
    def test_missing_status_reader_retries_are_bounded(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(case_fans.time, "sleep") as sleep:
            with self.assertRaises(FileNotFoundError):
                case_fans.open_status_file(os.path.join(directory, "missing.json"))
            self.assertEqual(sleep.call_count, 3)
            self.assertAlmostEqual(sum(c.args[0] for c in sleep.call_args_list), 0.035)

    def test_non_lock_io_error_is_not_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "status.json")
            with mock.patch.object(case_fans, "replace_status_file", side_effect=OSError("disk full")), \
                 mock.patch.object(case_fans.time, "sleep") as sleep:
                with self.assertRaises(OSError):
                    case_fans.write_status(path, "active", command_pct=80)
            sleep.assert_not_called()

    def test_replacefile_remove_contention_retries_original_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "status.json")
            case_fans.write_status(path, "checking")
            error = OSError("Unable to remove the file to be replaced")
            error.winerror = 1175
            replace_status = case_fans.replace_status_file
            attempts = []
            def replace(temporary, destination):
                attempts.append(destination)
                if len(attempts) == 1:
                    raise error
                return replace_status(temporary, destination)
            with mock.patch.object(case_fans, "replace_status_file", side_effect=replace), \
                 mock.patch.object(case_fans.time, "sleep") as sleep:
                case_fans.write_status(path, "active", command_pct=80)
            sleep.assert_called_once_with(0.01)
            with case_fans.open_status_file(path) as stream:
                self.assertEqual(json.load(stream)["command_pct"], 80)

    def test_overlapping_reads_and_writes_never_publish_partial_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "status.json")
            case_fans.write_status(path, "checking", sequence=0, marker="0" * 1000)
            client = case_fans.FanWorkerClient(directory)
            client.status_path = path
            client.started = case_fans.time.time()
            client.process = mock.Mock(pid=os.getpid(), stdin=io.StringIO())
            client.process.poll.return_value = None
            self.assertEqual(client.poll()["sequence"], 0)
            finished = threading.Event()
            errors = []
            def read():
                try:
                    while not finished.is_set():
                        snapshot = client.poll()
                        self.assertIn(snapshot["state"], ("active", "checking"))
                        self.assertEqual(snapshot["marker"], str(snapshot["sequence"]) * 1000)
                except Exception as exc:
                    errors.append(exc)
            reader = threading.Thread(target=read)
            reader.start()
            try:
                for number in range(1, 301):
                    case_fans.write_status(path, "active", command_pct=80, sequence=number, marker=str(number) * 1000)
            finally:
                finished.set()
                reader.join(5)
            self.assertFalse(reader.is_alive())
            self.assertEqual(errors, [])

    @unittest.skipUnless(os.name == "nt", "Windows file sharing regression")
    def test_transient_native_read_lock_does_not_stop_status_writer(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "status.json")
            case_fans.write_status(path, "checking")
            reader = open(path, encoding="utf-8")
            release = threading.Timer(0.05, reader.close)
            release.start()
            try:
                case_fans.write_status(path, "active", command_pct=80)
            finally:
                release.join()
                reader.close()
            with open(path, encoding="utf-8") as stream:
                self.assertEqual(json.load(stream)["command_pct"], 80)


if __name__ == "__main__":
    unittest.main()
