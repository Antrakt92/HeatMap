"""Worker trust boundaries and interrupted fan ownership; hardware-free."""
import io
import json
import unittest
from unittest.mock import Mock, patch

import case_fans
import gpu_fans
from test_gpu_fans import FakeAdapter


class CaseStatusBoundaryTests(unittest.TestCase):
    def poll(self, **changes):
        client = case_fans.FanWorkerClient('unused', shared=True)
        client.started = 100
        client.process = Mock(pid=7, stdin=io.StringIO())
        client.process.poll.return_value = 0
        status = dict(profile=case_fans.PROFILE, state='error', pid=7, time=101,
                      reason='Controller stopped', restore_confirmed=False,
                      restore_errors=[], control_attempted=True)
        status.update(changes)
        with patch.object(case_fans, 'open_status_file', return_value=io.StringIO(json.dumps(status))), \
                patch.object(case_fans.time, 'time', return_value=200):
            return client.poll()

    def test_malformed_diagnostics_never_become_verified_worker_reports(self):
        for changes in ({'reason': []}, {'restore_confirmed': 'false'},
                        {'restore_errors': False}, {'restore_errors': [None]},
                        {'control_attempted': 'false'}, {'profile': gpu_fans.PROFILE}):
            with self.subTest(changes=changes):
                report = self.poll(**changes)
                self.assertEqual(report['state'], 'error')
                self.assertIn('Invalid', report['reason'])
                self.assertIsNot(report.get('restore_confirmed'), True)
                self.assertEqual(report['controlled_channels'], list(case_fans.ALL_TARGETS))

    def test_old_terminal_report_cannot_certify_this_launch_restored_firmware(self):
        report = self.poll(time=90, state='stopped', restore_confirmed=True)
        self.assertEqual(report['state'], 'error')
        self.assertNotIn('restore_confirmed', report)

    def test_current_terminal_report_remains_valid_after_exit(self):
        report = self.poll(state='stopped', restore_confirmed=True)
        self.assertEqual(report['state'], 'stopped')
        self.assertTrue(report['restore_confirmed'])

    def test_failed_owner_lookup_is_reported_without_launching(self):
        for kind in (case_fans.FanWorkerClient, gpu_fans.GpuWorkerClient):
            client = kind('unused')
            with self.subTest(kind=kind.__name__), patch.object(case_fans.os, 'makedirs'), \
                    patch.object(gpu_fans.Path, 'mkdir'), \
                    patch.object(case_fans.psutil, 'Process', side_effect=case_fans.psutil.AccessDenied()), \
                    patch.object(case_fans.subprocess, 'Popen') as launch:
                client.start()
            launch.assert_not_called()
            self.assertEqual(client.poll()['state'], 'error')


class GpuJournalOwnershipTests(unittest.TestCase):
    def test_edit_during_journal_flush_is_preserved_without_optional_guard(self):
        adapter = FakeAdapter()
        journal = Mock()
        session = gpu_fans.GpuSession(adapter, journal)

        def external_edit(*_args):
            adapter.state['points'][0][1] = 29

        journal.before_write.side_effect = external_edit
        with self.assertRaisesRegex(RuntimeError, 'outside HeatMap'):
            session.apply(85)
        self.assertEqual(adapter.events, [])
        self.assertFalse(session.touched)
        self.assertEqual(adapter.state['points'][0][1], 29)


if __name__ == '__main__':
    unittest.main()
