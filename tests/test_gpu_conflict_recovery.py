import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import gpu_fans
from test_gpu_fans import FakeAdapter


class ConflictRecoveryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'recovery.json'
        self.journal = gpu_fans.RecoveryJournal(self.path, {'name': 'test GPU'})
        self.adapter = FakeAdapter()
        self.adapter.state['zero_rpm'] = False
        self.session = gpu_fans.GpuSession(self.adapter, self.journal)
        self.session.apply(85)
        self.saved = self.path.read_bytes()
        # Reproduce the reported driver reset: original knots, changed Zero RPM.
        self.adapter.state = copy.deepcopy(self.session.baseline)
        self.adapter.state['zero_rpm'] = True
        self.external = self.adapter.snapshot()
        self.events = list(self.adapter.events)

    def test_explicit_retry_archives_conflict_without_hardware_writes(self):
        self.assertFalse(self.journal.recover(self.adapter, accept_external=True))
        self.assertEqual(self.adapter.snapshot(), self.external)
        self.assertEqual(self.adapter.events, self.events)
        self.assertFalse(self.path.exists())
        archives = list(self.path.parent.glob('recovery.conflict-*.json'))
        self.assertEqual(len(archives), 1)
        self.assertEqual(archives[0].read_bytes(), self.saved)
        new_session = gpu_fans.GpuSession(self.adapter, self.journal)
        new_session.apply(85)
        self.assertEqual(new_session.restore(), [])
        self.assertEqual(self.adapter.snapshot(), self.external)

    def test_automatic_restart_preserves_conflict_and_sends_no_commands(self):
        with self.assertRaisesRegex(RuntimeError, 'settings preserved'):
            self.journal.recover(self.adapter)
        self.assertEqual(self.adapter.events, self.events)
        self.assertEqual(self.path.read_bytes(), self.saved)

    def test_explicit_retry_does_not_bypass_identity_or_schema_checks(self):
        for mutate in (lambda saved: saved.update(gpu={'name': 'other GPU'}),
                       lambda saved: saved.update(baseline={})):
            saved = json.loads(self.saved)
            mutate(saved)
            self.path.write_text(json.dumps(saved), encoding='utf-8')
            with self.assertRaisesRegex(RuntimeError, 'invalid'):
                self.journal.recover(self.adapter, accept_external=True)
            self.assertEqual(self.adapter.events, self.events)
            self.assertTrue(self.path.exists())

    def test_invalid_or_changing_current_settings_cannot_be_adopted(self):
        for snapshots in (({}, {}), (self.external, self.session.baseline)):
            with self.subTest(snapshots=snapshots), patch.object(
                    self.adapter, 'snapshot', side_effect=snapshots):
                with self.assertRaises(RuntimeError):
                    self.journal.recover(self.adapter, accept_external=True)
            self.assertEqual(self.path.read_bytes(), self.saved)
            self.assertEqual(self.adapter.events, self.events)

    def test_archive_failure_leaves_recovery_and_hardware_untouched(self):
        with patch.object(Path, 'rename', side_effect=PermissionError('denied')):
            with self.assertRaises(OSError):
                self.journal.recover(self.adapter, accept_external=True)
        self.assertEqual(self.path.read_bytes(), self.saved)
        self.assertEqual(self.adapter.events, self.events)

    def test_explicit_retry_still_restores_recognized_heatmap_curve(self):
        self.adapter.state = copy.deepcopy(self.session.expected)
        self.assertTrue(self.journal.recover(self.adapter, accept_external=True))
        self.assertEqual(self.adapter.snapshot(), self.session.baseline)
        self.assertFalse(list(self.path.parent.glob('recovery.conflict-*.json')))

    def test_conflict_diagnostics_capture_exact_expected_and_observed_settings(self):
        with self.assertRaisesRegex(RuntimeError, 'outside HeatMap'):
            self.session.check()
        self.assertEqual(self.session.conflict, {
            'expected': self.session.expected, 'observed': self.external})
        self.adapter.state['zero_rpm'] = False
        self.assertEqual(self.session.conflict['observed'], self.external)


class ExplicitRetryRoutingTests(unittest.TestCase):
    def test_live_conflict_reaches_diagnostics_without_overwriting_driver(self):
        from test_gpu_fan_startup_audit import GpuStartupAuditTests, sample
        external = []

        def edit(now, adapter):
            if adapter.writes and not external:
                adapter.state['points'][0][1] = 24
                external.append(adapter.snapshot())

        result, adapter, _, reports, journal = GpuStartupAuditTests().run_worker(
            lambda *_: sample(gpu_core_temp=70, gpu_fan=2000), on_wait=edit)
        self.assertEqual(result, 1)
        self.assertTrue(journal)
        self.assertEqual(adapter.snapshot(), external[0])
        self.assertEqual(reports[-1]['settings_conflict']['observed'], external[0])
        self.assertIn('OFF/ON', reports[-1]['reason'])
        self.assertFalse(reports[-1]['restore_confirmed'])

    def test_client_accepts_external_only_for_explicit_start(self):
        for explicit in (False, True):
            with self.subTest(explicit=explicit), tempfile.TemporaryDirectory() as directory, \
                    patch.dict('os.environ', {'LOCALAPPDATA': directory}), \
                    patch.object(gpu_fans.subprocess, 'Popen') as popen:
                client = gpu_fans.GpuWorkerClient(directory)
                if explicit:
                    client.start(accept_external=True)
                else:
                    client.start()
                command = popen.call_args.args[0]
                self.assertEqual('--accept-external' in command, explicit)

    def test_only_menu_on_requests_acceptance(self):
        import overlay
        app = Mock()
        app.config = {'gpu_fans_enabled': False}
        app.lock = __import__('threading').Lock()
        app._hardware_pause_reason = None
        app.gpu_fan_worker.process = None
        overlay.OverlayApp.toggle_gpu_fans(app)
        app.gpu_fan_worker.start.assert_called_once_with(accept_external=True)
        overlay.OverlayApp.toggle_gpu_fans(app)
        app.gpu_fan_worker.stop.assert_called_once()


if __name__ == '__main__':
    unittest.main()
