import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import amd_gpu_fan as native
import gpu_fans
from startup_readiness import StartupCancelled
from test_gpu_fans import FakeAdapter
import test_gpu_fan_startup_audit as startup_helpers


class NativeLifetimeTests(unittest.TestCase):
    def adapter(self):
        adapter = native.AmdGpuFan.__new__(native.AmdGpuFan)
        adapter.owned = []
        adapter.initialized = True
        adapter.system = Mock()
        adapter.dll = Mock()
        adapter.dll.ADLXTerminate.return_value = 0
        adapter.dll_handle = 123
        return adapter

    def test_terminate_precedes_unload_and_repeat_close_does_nothing(self):
        adapter = self.adapter()
        events = []
        adapter.dll.ADLXTerminate.side_effect = lambda: events.append('terminate') or 0
        with patch.object(native, 'free_library', side_effect=lambda handle: events.append(('unload', handle))):
            adapter.close()
            adapter.close()
        self.assertEqual(events, ['terminate', ('unload', 123)])
        self.assertIsNone(adapter.dll)
        self.assertIsNone(adapter.dll_handle)

    def test_termination_error_still_unloads_once(self):
        adapter = self.adapter()
        adapter.dll.ADLXTerminate.return_value = 1
        with patch.object(native, 'free_library') as unload:
            with self.assertRaises(native.AdlxError):
                adapter.close()
            adapter.close()
        unload.assert_called_once_with(123)

    def test_partial_initialization_releases_loaded_library(self):
        adapter = self.adapter()
        adapter.initialized = False
        dll = adapter.dll
        with patch.object(native, 'free_library') as unload:
            adapter.close()
        dll.ADLXTerminate.assert_not_called()
        unload.assert_called_once_with(123)

    def test_failed_native_release_cannot_be_repeated_on_same_pointer(self):
        interface = native.Interface(123)
        with patch.object(interface, 'call', side_effect=OSError('native release failed')) as release:
            with self.assertRaises(OSError):
                interface.close()
            interface.close()
        release.assert_called_once()
        self.assertFalse(interface.pointer)


class WriteBoundaryTests(unittest.TestCase):
    def test_worker_wires_cancellation_after_last_native_snapshot(self):
        result, adapter, _, reports, _ = startup_helpers.GpuStartupAuditTests().run_worker(
            lambda *_: startup_helpers.sample(gpu_core_temp=70, gpu_fan=2000), stop_on_snapshot=3)
        self.assertEqual(result, 0)
        self.assertEqual(adapter.writes, [])
        self.assertFalse(reports[-1]['control_attempted'])

    def test_external_edit_during_slow_guard_is_preserved(self):
        adapter = FakeAdapter()
        session = gpu_fans.GpuSession(adapter)

        def external_edit():
            adapter.state['points'][0][1] = 29

        with self.assertRaisesRegex(RuntimeError, 'outside HeatMap'):
            session.apply(85, before_write=external_edit)
        self.assertEqual(adapter.events, [])
        self.assertEqual(adapter.state['points'][0][1], 29)

    def test_cancel_after_final_ownership_read_prevents_takeover(self):
        adapter = FakeAdapter()
        session = gpu_fans.GpuSession(adapter)
        with self.assertRaises(StartupCancelled):
            session.apply(85, check_cancelled=Mock(side_effect=StartupCancelled('stopped')))
        self.assertEqual(adapter.events, [])
        self.assertFalse(session.touched)

    def test_cancel_recovery_preserves_journal_and_sends_no_restore_commands(self):
        adapter = FakeAdapter()
        with tempfile.TemporaryDirectory() as directory:
            journal = gpu_fans.RecoveryJournal(Path(directory) / 'recovery.json', {'name': 'GPU'})
            session = gpu_fans.GpuSession(adapter, journal)
            session.apply(85)
            prior = journal.path.read_bytes()
            current, events = adapter.snapshot(), list(adapter.events)
            with self.assertRaises(StartupCancelled):
                journal.recover(adapter, check_cancelled=Mock(side_effect=StartupCancelled('owner stopped')))
            self.assertEqual(journal.path.read_bytes(), prior)
            self.assertEqual(adapter.snapshot(), current)
            self.assertEqual(adapter.events, events)

    def test_external_edit_during_recovery_guard_is_preserved(self):
        adapter = FakeAdapter()
        with tempfile.TemporaryDirectory() as directory:
            journal = gpu_fans.RecoveryJournal(Path(directory) / 'recovery.json', {'name': 'GPU'})
            session = gpu_fans.GpuSession(adapter, journal)
            session.apply(85)
            events = list(adapter.events)

            def external_edit():
                adapter.state['points'][0][1] = 29

            with self.assertRaisesRegex(RuntimeError, 'restoration unconfirmed'):
                journal.recover(adapter, before_write=external_edit)
            self.assertEqual(adapter.events, events)
            self.assertEqual(adapter.state['points'][0][1], 29)
            self.assertTrue(journal.path.exists())


class StaleCoolingTests(unittest.TestCase):
    def test_repeated_samples_cannot_reduce_gpu_fan_command(self):
        def values(now, _adapter):
            return startup_helpers.sample(gpu_core_temp=75 if now < 5 else 65, gpu_fan=3000,
                          timestamp_ms=min(now, 13) * 1000)

        _result, _adapter, _baseline, reports, _journal = startup_helpers.GpuStartupAuditTests().run_worker(values, stop_at=19)
        commands = [report['command_pct'] for report in reports
                    if report['state'] == 'active' and report['time'] >= 13]
        self.assertTrue(commands)
        self.assertTrue(all(command == 100 for command in commands), commands)


if __name__ == '__main__':
    unittest.main()
