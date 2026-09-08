import json
import os
from pathlib import Path
import tempfile
import unittest
from contextlib import ExitStack
from unittest import mock

import setup
from tools import commission_gpu_fans, commission_shared_fans


class RestoreLockIdentityTests(unittest.TestCase):
    def test_casing_alias_cannot_acquire_a_second_restore_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / 'HeatMap'
            app.mkdir()
            with mock.patch.object(setup.tempfile, 'gettempdir', return_value=directory):
                with setup._runtime_restore_lock(str(app)):
                    with self.assertRaisesRegex(setup.SetupError, 'already running'):
                        with setup._runtime_restore_lock(str(app).swapcase()):
                            self.fail('Two restores acquired the same runtime through path aliases')
                # Normal completion must release the canonical lock.
                with setup._runtime_restore_lock(str(app).swapcase()):
                    pass


class CommissioningRecoveryTests(unittest.TestCase):
    def run_commission(self, module, *, restart_error=None, close_update=None,
                       terminal=None, verification_error=None):
        with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
            live_config = {'alerts_enabled': False}
            client = mock.Mock(status_path='fake-status.json')
            client.poll.return_value = terminal if terminal is not None else dict(
                state='stopped', restore_confirmed=True, restore_errors=[])
            saved_configs = []

            def verify(_client, samples, **_kwargs):
                if verification_error:
                    raise verification_error
                samples.append({'verified_full_rpm': {'System Fan #1': 1200}})
                return dict(state='stopped', restore_confirmed=True, restore_errors=[])

            def close():
                if close_update:
                    live_config.update(close_update)

            def save(config):
                saved_configs.append(dict(config))
                return True, ''

            stack.enter_context(mock.patch.dict(os.environ, LOCALAPPDATA=directory))
            stack.enter_context(mock.patch.object(module.sys, 'argv', ['commission', '--enable']))
            stack.enter_context(mock.patch.object(module.sys, 'executable',
                                                 str(Path(directory) / 'python-env' / 'python.exe')))
            stack.enter_context(mock.patch.object(module.overlay, '_is_admin', return_value=True))
            stack.enter_context(mock.patch.object(module.overlay, 'load_config_result',
                                                 side_effect=lambda: (dict(live_config), '')))
            stack.enter_context(mock.patch.object(module.overlay, 'save_config', side_effect=save))
            stack.enter_context(mock.patch.object(module, 'close_previous_overlay', side_effect=close))
            restart = stack.enter_context(mock.patch.object(module.subprocess, 'Popen',
                                                           side_effect=restart_error))
            restart.return_value.pid = 42
            if module is commission_shared_fans:
                stack.enter_context(mock.patch.object(module, 'verified_module'))
                stack.enter_context(mock.patch.object(module, 'require_hardware_access'))
                stack.enter_context(mock.patch.object(module, 'FanWorkerClient', return_value=client))
                stack.enter_context(mock.patch.object(module, 'verify_worker', side_effect=verify))
            else:
                stack.enter_context(mock.patch.object(module, 'GpuWorkerClient', return_value=client))
                stack.enter_context(mock.patch.object(module, 'verify', side_effect=verify))
            result = module.main()
            reports = list(Path(directory).rglob('commission-*.json'))
            self.assertEqual(len(reports), 1, 'The result must remain reviewable even when restart fails')
            report = json.loads(reports[0].read_text(encoding='utf-8'))
            expected_pythonw = str(Path(module.sys.executable).with_name('pythonw.exe'))
            return result, report, saved_configs, restart.call_args, expected_pythonw

    def test_restart_failure_is_saved_and_returns_failure_for_both_tools(self):
        for module in (commission_gpu_fans, commission_shared_fans):
            with self.subTest(tool=module.__name__):
                result, report, _configs, _restart, _pythonw = self.run_commission(
                    module, restart_error=OSError('pythonw is unavailable'))
                self.assertEqual(result, 1)
                self.assertEqual(report['state'], 'error')
                self.assertIn('pythonw is unavailable', report['restart_error'])
                self.assertTrue(report['restore']['restore_confirmed'])

    def test_shared_activation_preserves_settings_saved_during_overlay_shutdown(self):
        result, _report, configs, _restart, _pythonw = self.run_commission(
            commission_shared_fans, close_update={'alerts_enabled': True, 'x': 842})
        self.assertEqual(result, 0)
        self.assertTrue(configs[0]['alerts_enabled'])
        self.assertEqual(configs[0]['x'], 842)

    def test_restart_uses_the_verified_running_python_environment(self):
        for module in (commission_gpu_fans, commission_shared_fans):
            with self.subTest(tool=module.__name__):
                result, report, _configs, restart, pythonw = self.run_commission(module)
                self.assertEqual(result, 0)
                self.assertEqual(restart.args[0][0], pythonw)
                self.assertEqual(report['overlay_restarted_pid'], 42)

    def test_shared_unconfirmed_baseline_blocks_restart_even_without_new_commands(self):
        result, report, _configs, restart, _pythonw = self.run_commission(
            commission_shared_fans,
            terminal=dict(state='error', control_attempted=False,
                          baseline=[{'name': 'System Fan #1'}], controlled_channels=[],
                          restore_confirmed=False, restore_errors=['restore failed']),
            verification_error=RuntimeError('restore not confirmed'))
        self.assertEqual(result, 1)
        self.assertIsNone(restart)
        self.assertNotIn('overlay_restarted_pid', report)

    def test_final_terminal_divergence_cannot_return_success_or_restart(self):
        terminals = [
            dict(state='error', restore_confirmed=False, restore_errors=[]),
            dict(state='stopped', restore_confirmed=True, restore_errors=['restore failed']),
            dict(state='stopped', restore_confirmed='yes', restore_errors=[]),
            dict(state='stopped', restore_confirmed=True),
            dict(state='stopped', restore_confirmed=False, restore_errors=[],
                 control_attempted=False, baseline=[], controlled_channels=[]),
        ]
        for module in (commission_gpu_fans, commission_shared_fans):
            for terminal in terminals:
                with self.subTest(tool=module.__name__, terminal=terminal):
                    result, report, _configs, restart, _pythonw = self.run_commission(
                        module, terminal=terminal)
                    self.assertEqual(result, 1)
                    self.assertEqual(report['state'], 'error')
                    self.assertIn('restoration not confirmed', report['reason'])
                    self.assertIsNone(restart)
                    self.assertEqual(report['terminal'], terminal)

    def test_uncertain_terminal_keeps_original_verification_failure_reason(self):
        for module in (commission_gpu_fans, commission_shared_fans):
            with self.subTest(tool=module.__name__):
                result, report, _configs, restart, _pythonw = self.run_commission(
                    module, terminal=dict(state='error', restore_confirmed=False, restore_errors=[]),
                    verification_error=RuntimeError('Original verification failure'))
                self.assertEqual(result, 1)
                self.assertEqual(report['reason'], 'Original verification failure')
                self.assertIsNone(restart)


if __name__ == '__main__':
    unittest.main()
