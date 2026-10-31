"""Failed construction must release fan owners without saving partial settings."""
from contextlib import ExitStack
from types import SimpleNamespace
from unittest import mock

import overlay
from test_ui_layout import TkTestCase


class StartupCleanupTests(TkTestCase):
    def setUp(self):
        mode = mock.patch.object(overlay, "windows_only_mode", return_value=False)
        mode.start()
        self.addCleanup(mode.stop)

    def check_failed_startup(self, stage, error, *, terminal=None, cancellation_error=False):
        real_tk = overlay.tk.Tk
        roots, threads = [], []
        clock, waits = SimpleNamespace(now=100.0), []
        fans = [mock.Mock(), mock.Mock()]
        case, gpu = fans

        def hidden_root():
            root = real_tk()
            root.withdraw()
            root.destroy = mock.Mock(wraps=root.destroy)
            if cancellation_error:
                root.after_cancel = mock.Mock(side_effect=overlay.tk.TclError('callback cannot be cancelled'))
            roots.append(root)
            return root

        def wait_for_restoration(*, timeout):
            self.assertTrue(all(worker.stop.called for worker in fans),
                            'Request both restorations before waiting for either owner')
            waits.append(timeout)
            if len(waits) == 1:
                clock.now += 10

        for worker in fans:
            worker.process = mock.Mock()
            worker.process.wait.side_effect = wait_for_restoration
            worker.poll.return_value = terminal or {
                'state': 'stopped', 'restore_confirmed': True,
                'restore_errors': [], 'control_attempted': True}

        def fake_thread(**kwargs):
            state = {'started': False}
            thread = SimpleNamespace(target=kwargs['target'])

            def start():
                if stage == 'sensor-start' and thread is threads[0]:
                    raise error
                state['started'] = True

            thread.start = mock.Mock(side_effect=start)
            thread.is_alive = mock.Mock(side_effect=lambda: state['started'])
            thread.join = mock.Mock(side_effect=lambda **_kwargs: state.update(started=False))
            threads.append(thread)
            return thread

        config = overlay._default_config()
        config.update(case_fans_enabled=True, gpu_fans_enabled=True)
        app = object.__new__(overlay.OverlayApp)
        with ExitStack() as stack:
            for name in ('_schedule_embed', '_poll_screen_change', '_poll_peek_edge',
                         '_poll_desktop_visibility', '_check_alerts'):
                stack.enter_context(mock.patch.object(overlay.OverlayApp, name))
            stack.enter_context(mock.patch.object(overlay.tk, 'Tk', side_effect=hidden_root))
            stack.enter_context(mock.patch.object(overlay, '_get_monitor_areas', return_value=(
                ((0, 0, 1280, 720), (0, 0, 1280, 720)),)))
            stack.enter_context(mock.patch.object(overlay, 'load_config_result', return_value=(config, None)))
            stack.enter_context(mock.patch.object(overlay, 'is_pawnio_driver_installed', return_value=True))
            stack.enter_context(mock.patch.object(overlay, 'require_hardware_access', return_value='full'))
            stack.enter_context(mock.patch.object(overlay, '_gpu_control_available', return_value=True))
            stack.enter_context(mock.patch.object(overlay, '_retire_unsupported_case_fan_profile', return_value=False))
            stack.enter_context(mock.patch.object(overlay, '_retire_broken_shared_module', return_value=False))
            stack.enter_context(mock.patch.object(overlay, 'FanWorkerClient', return_value=case))
            stack.enter_context(mock.patch.object(overlay, 'GpuWorkerClient', return_value=gpu))
            stack.enter_context(mock.patch.object(overlay.threading, 'Thread', side_effect=fake_thread))
            stack.enter_context(mock.patch.object(overlay.time, 'monotonic', side_effect=lambda: clock.now))
            save = stack.enter_context(mock.patch.object(overlay, 'save_config'))
            logger = stack.enter_context(mock.patch.object(overlay, 'log'))
            stack.enter_context(mock.patch.object(overlay.OverlayApp, 'update_ui',
                                                 side_effect=error if stage == 'ui-update' else None))
            try:
                with self.assertRaises(type(error)) as raised:
                    overlay.OverlayApp.__init__(app)
                self.assertIs(raised.exception, error, 'Cleanup must preserve the startup failure')
                for worker in fans:
                    worker.start.assert_called_once_with()
                    worker.stop.assert_called_once_with()
                    worker.process.wait.assert_called_once()
                    timeout = worker.process.wait.call_args.kwargs['timeout']
                    self.assertGreater(timeout, 0)
                    self.assertLessEqual(timeout, 15)
                    worker.poll.assert_called_once_with()
                self.assertLessEqual(waits[1], 5, 'Fan owners must share one restoration deadline')
                self.assertFalse(app.running)
                self.assertTrue(app._stop_event.is_set())
                for thread in threads:
                    if thread is not threads[0] or stage != 'sensor-start':
                        thread.join.assert_called_once()
                roots[0].destroy.assert_called_once_with()
                save.assert_not_called()
                if terminal is not None:
                    failures = [call for call in logger.error.call_args_list
                                if 'unconfirmed' in str(call.args[0])]
                    self.assertEqual(len(failures), 2,
                                     'Process exit alone must not confirm either restoration')
            finally:
                # Clean the real interpreter even when the intentional pre-fix
                # assertions fail. All worker/process objects above are fakes.
                for root in roots:
                    if not root.destroy.called:
                        for callback in root.tk.call('after', 'info'):
                            overlay.tk.Misc.after_cancel(root, callback)
                        root.destroy()

    def test_sensor_thread_start_failure_stops_already_started_fans(self):
        self.check_failed_startup('sensor-start', RuntimeError('sensor thread cannot start'))

    def test_first_ui_update_failure_stops_workers_and_destroys_tk(self):
        self.check_failed_startup('ui-update', RuntimeError('first UI update failed'))

    def test_keyboard_interrupt_during_first_ui_update_still_restores_fans(self):
        self.check_failed_startup('ui-update', KeyboardInterrupt('startup interrupted'))

    def test_exited_workers_with_unconfirmed_reports_are_logged_as_unsafe(self):
        for state in ('error', 'stopped'):
            with self.subTest(state=state):
                self.check_failed_startup('ui-update', RuntimeError('first UI update failed'),
                    terminal={'state': state, 'restore_confirmed': False,
                              'control_attempted': True, 'restore_errors': []})

    def test_callback_cancellation_error_cannot_skip_window_destroy(self):
        self.check_failed_startup('ui-update', RuntimeError('first UI update failed'),
                                  cancellation_error=True)
