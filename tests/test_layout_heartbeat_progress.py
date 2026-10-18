"""Layout progress renews ownership; a blocked Tk call must still expire it."""
import threading
import unittest
from unittest import mock

import fan_common
import overlay
from case_fans import FanWorkerClient
from gpu_fans import GpuWorkerClient
from test_ui_layout import TkTestCase, layout_app


class LayoutHeartbeatProgressTests(TkTestCase):
    def test_slow_layout_passes_do_not_starve_either_owner(self):
        with layout_app() as app:
            now = [0.0]
            with mock.patch.object(fan_common.time, 'monotonic', side_effect=lambda: now[0]):
                owners = [fan_common.OwnerHeartbeat(threading.Event()) for _ in range(2)]
                workers = [mock.Mock(), mock.Mock()]
                for worker, owner in zip(workers, owners):
                    def beat(owner=owner):
                        self.assertFalse(owner.expired(), 'Owner expired while layout was progressing')
                        owner.last_seen = now[0]
                    worker.heartbeat.side_effect = beat
                app.fan_worker, app.gpu_fan_worker = workers
                original = app.root.update_idletasks

                def slow_layout():
                    now[0] += 4
                    original()

                with mock.patch.object(app.root, 'update_idletasks', side_effect=slow_layout):
                    app._fit_content()
                self.assertGreater(now[0], fan_common.HEARTBEAT_TIMEOUT_SECONDS)
                self.assertTrue(all(not owner.expired() for owner in owners))
                self.assertTrue(all(worker.heartbeat.call_count >= 4 for worker in workers))

    def test_a_single_blocked_layout_call_still_expires_ownership(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.running = True
        app._stop_event = threading.Event()
        app.root = mock.Mock()
        now = [0.0]
        with mock.patch.object(fan_common.time, 'monotonic', side_effect=lambda: now[0]):
            owner = fan_common.OwnerHeartbeat(threading.Event())
            app.fan_worker = mock.Mock()
            observations = []

            def beat():
                observations.append(owner.expired())
                owner.last_seen = now[0]

            def blocked():
                now[0] += fan_common.HEARTBEAT_TIMEOUT_SECONDS + 1
                self.assertTrue(owner.expired())

            app.fan_worker.heartbeat.side_effect = beat
            app.root.update_idletasks.side_effect = blocked
            app._flush_layout()
        self.assertEqual(observations, [False, True])

    def test_shutdown_never_renews_ownership(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.running = False
        app.fan_worker = mock.Mock()
        app._service_fan_heartbeats()
        app.fan_worker.heartbeat.assert_not_called()

    def test_failed_pipe_does_not_starve_other_owner(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.running = True
        app.gpu_fan_worker, app.fan_worker = mock.Mock(), mock.Mock()
        app.gpu_fan_worker.heartbeat.side_effect = OSError('pipe unavailable')
        app._service_fan_heartbeats()
        app.fan_worker.heartbeat.assert_called_once_with()

    def test_stop_event_disables_layout_heartbeats(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.running = True
        app._stop_event = threading.Event()
        app._stop_event.set()
        app.fan_worker = mock.Mock()
        app._service_fan_heartbeats()
        app.fan_worker.heartbeat.assert_not_called()


class ClientHeartbeatTests(unittest.TestCase):
    def test_only_live_case_and_gpu_workers_receive_keepalive(self):
        for kind in (FanWorkerClient, GpuWorkerClient):
            with self.subTest(client=kind.__name__):
                client = kind('unused')
                client.heartbeat()
                client.process = mock.Mock()
                client.process.poll.return_value = None
                client.heartbeat()
                client.process.stdin.write.assert_called_once_with('alive\n')
                client.process.stdin.flush.assert_called_once_with()
                client.process.poll.return_value = 1
                client.heartbeat()
                client.process.stdin.write.assert_called_once()

    def test_closed_stdin_is_a_safe_noop(self):
        client = FanWorkerClient('unused')
        client.process = mock.Mock()
        client.process.poll.return_value = None
        client.process.stdin.write.side_effect = ValueError('closed pipe')
        client.heartbeat()
