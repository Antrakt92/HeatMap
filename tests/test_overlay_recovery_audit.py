"""Bounded fake-owner recovery; never opens hardware or starts fan workers."""
import sys
from types import SimpleNamespace, ModuleType
import unittest
from unittest import mock

import overlay
from hardware_access_guard import HardwareAccessConflict
from test_sensor_lifecycle import sensor_app


class OverlayRecoveryAuditTests(unittest.TestCase):
    def run_sensor(self, app, access, initialize, sample=None):
        with (mock.patch.object(overlay, 'require_hardware_access', side_effect=access),
              mock.patch.object(overlay, 'init_hardware_monitor', side_effect=initialize) as start,
              mock.patch.object(overlay, 'read_sensors', return_value=sample or {'cpu_temp': 43}) as read,
              mock.patch.object(overlay.time, 'monotonic', side_effect=lambda: app._stop_event.now),
              mock.patch.object(overlay.psutil, 'cpu_percent'),
              mock.patch.object(app, '_cache_sensor_diagnostics')):
            app.sensor_loop()
        return start, read

    def test_installer_clear_resumes_in_same_owner_after_stable_interval(self):
        old, new = mock.Mock(), mock.Mock()
        app = sensor_app(12, old)
        app.fan_worker = mock.Mock(process=None)
        app.gpu_fan_worker = mock.Mock(process=None)
        checks = []
        def access(*_):
            checks.append(app._stop_event.now)
            if len(checks) == 1:
                raise HardwareAccessConflict('installer', kind='driver_change')
            return 'full'
        opened = []
        def initialize():
            old.Close.assert_called_once_with()
            self.assertFalse(app.lock.locked())
            opened.append(app._stop_event.now)
            return new
        start, read = self.run_sensor(app, access, initialize)
        self.assertEqual(len(opened), 1)
        self.assertGreaterEqual(opened[0] - checks[1], 15)
        self.assertGreater(read.call_count, 0)
        self.assertIsNone(app._hardware_pause_reason)
        app.fan_worker.start.assert_not_called()
        app.gpu_fan_worker.start.assert_not_called()
        app.fan_worker.stop.assert_called_once_with()
        old.Close.assert_called_once_with()
        new.Close.assert_called_once_with()

    def test_monitor_close_failure_never_reopens_native_handle(self):
        computer = mock.Mock()
        computer.Close.side_effect = RuntimeError('native close failure')
        app = sensor_app(20, computer)
        start, read = self.run_sensor(app,
            lambda *_: (_ for _ in ()).throw(HardwareAccessConflict('installer', kind='driver_change')),
            lambda: self.fail('reopened after failed close'))
        start.assert_not_called()
        read.assert_not_called()
        self.assertIn('close failed', app._hardware_pause_reason)
        computer.Close.assert_called_once_with()

    def test_partial_init_close_failure_hard_stops_future_retries(self):
        for stage in ('Open', 'check'):
            for error in (RuntimeError('native init failure'),
                          HardwareAccessConflict('native guard failure', kind='driver_change')):
                with self.subTest(stage=stage, error=type(error).__name__):
                    computer = mock.Mock()
                    computer.Close.side_effect = RuntimeError('native cleanup failure')
                    if stage == 'Open':
                        computer.Open.side_effect = error
                    clr = ModuleType('clr')
                    clr.AddReference = mock.Mock()
                    hardware = ModuleType('LibreHardwareMonitor.Hardware')
                    hardware.Computer = mock.Mock(return_value=computer)
                    hardware.HardwareType = mock.Mock()
                    hardware.SensorType = mock.Mock()
                    app = sensor_app(40)
                    with (mock.patch.dict(sys.modules, {'clr': clr,
                              'LibreHardwareMonitor.Hardware': hardware}),
                          mock.patch.object(overlay.os.path, 'exists', return_value=True),
                          mock.patch.object(overlay, 'require_hardware_access', return_value='full'),
                          mock.patch.object(overlay, '_check_lhm_cpu_temperature',
                              side_effect=error if stage == 'check' else None),
                          mock.patch.object(overlay.psutil, 'cpu_percent'),
                          mock.patch.object(overlay.time, 'monotonic', side_effect=lambda: app._stop_event.now),
                          mock.patch.object(overlay, 'log')):
                        app.sensor_loop()
                    hardware.Computer.assert_called_once_with()
                    computer.Close.assert_called_once_with()
                    self.assertIn('initialization cleanup failed', app._hardware_pause_reason)
                    self.assertIsNone(app.computer)

    def test_verified_fan_restoration_allows_sensor_recovery_without_control(self):
        app = sensor_app(12)
        for attribute in ('fan_worker', 'gpu_fan_worker'):
            worker = mock.Mock(process=mock.Mock())
            worker.process.poll.return_value = 0
            worker.poll.return_value = {'state': 'stopped', 'restore_confirmed': True,
                'restore_errors': [], 'recovery_pending': False}
            setattr(app, attribute, worker)
        access = mock.Mock(side_effect=[HardwareAccessConflict('installer', kind='driver_change')] + ['full'] * 30)
        start, read = self.run_sensor(app, access, lambda: mock.Mock())
        start.assert_called_once()
        self.assertIsNone(app._hardware_pause_reason)
        app.fan_worker.start.assert_not_called()
        app.gpu_fan_worker.start.assert_not_called()

    def test_reappearing_conflict_resets_clear_hysteresis(self):
        app = sensor_app(20)
        checks = []
        opened = []
        def access(*_):
            now = app._stop_event.now
            checks.append(now)
            if len(checks) == 1 or now == 110:
                raise HardwareAccessConflict('inventory', kind='inventory_unverified')
            return 'full'
        def initialize():
            opened.append(app._stop_event.now)
            return mock.Mock()
        self.run_sensor(app, access, initialize)
        self.assertEqual(len(opened), 1)
        self.assertGreaterEqual(opened[0], 128)

    def test_persistent_installer_never_opens_monitor_and_shutdown_stops_wait(self):
        app = sensor_app(4)
        start, read = self.run_sensor(app,
            lambda *_: (_ for _ in ()).throw(HardwareAccessConflict('installer', kind='driver_change')),
            lambda: self.fail('opened during installation'))
        start.assert_not_called()
        read.assert_not_called()
        self.assertIn('installer', app._hardware_pause_reason)
        self.assertTrue(app._stop_event.is_set())

    def test_unconfirmed_restore_stays_hard_paused_even_when_guard_clears(self):
        for status in ({'state': 'error', 'restore_confirmed': False, 'recovery_pending': True},
                       {'state': 'stopped', 'restore_confirmed': True, 'restore_errors': ['failure']}):
            with self.subTest(status=status):
                app = sensor_app(15)
                app.fan_worker = mock.Mock(process=mock.Mock())
                app.fan_worker.process.poll.return_value = 0
                app.fan_worker.poll.return_value = status
                calls = []
                def access(*_):
                    calls.append(1)
                    if len(calls) == 1:
                        raise HardwareAccessConflict('installer', kind='driver_change')
                    return 'full'
                start, read = self.run_sensor(app, access, lambda: self.fail('unsafe reopen'))
                start.assert_not_called()
                self.assertIn('restoration is unconfirmed', app._hardware_pause_reason)

    def test_running_fan_owner_prevents_reopen(self):
        app = sensor_app(15)
        app.gpu_fan_worker = mock.Mock(process=mock.Mock())
        app.gpu_fan_worker.process.poll.return_value = None
        access = mock.Mock(side_effect=[HardwareAccessConflict('installer', kind='driver_change')] + ['full'] * 20)
        start, read = self.run_sensor(app, access, lambda: self.fail('parallel owner'))
        start.assert_not_called()
        self.assertIn('restoration is unconfirmed', app._hardware_pause_reason)

    def test_storage_owner_survives_pause_then_probes_fresh_reading(self):
        app = sensor_app(3)
        app.config = {}
        app._storage_snapshot = {}
        app._hardware_pause_reason = 'transient installer'
        original_wait = app._stop_event.wait
        def wait(seconds):
            app._hardware_pause_reason = None
            return original_wait(seconds)
        app._stop_event.wait = wait
        with (mock.patch.object(overlay, 'physical_disk_numbers', return_value={1}),
              mock.patch.object(overlay, 'require_hardware_access', return_value='full'),
              mock.patch.object(overlay, 'probe_disk', return_value={'disk_number': 1, 'temp': 38}) as read,
              mock.patch.object(overlay.time, 'monotonic', side_effect=lambda: app._stop_event.now)):
            app.storage_loop()
        self.assertGreater(read.call_count, 0)
        self.assertEqual(app._storage_snapshot[1][1]['temp'], 38)

    def test_storage_guard_failure_does_not_kill_worker(self):
        app = sensor_app(2)
        app.config = {}
        app._storage_snapshot = {}
        with (mock.patch.object(overlay, 'physical_disk_numbers', return_value={1}),
              mock.patch.object(overlay, 'require_hardware_access', side_effect=[HardwareAccessConflict('install'), 'full']),
              mock.patch.object(overlay, 'probe_disk', return_value={'disk_number': 1, 'temp': 36}) as read,
              mock.patch.object(overlay.time, 'monotonic', side_effect=lambda: app._stop_event.now)):
            app.storage_loop()
        read.assert_called_once()
        self.assertEqual(app._storage_snapshot[1][1]['temp'], 36)

    def test_paused_high_ram_uses_one_snapshot_for_warnings_alerts_and_display(self):
        app = sensor_app(1)
        app.disk_frame = mock.Mock()
        app.disk_frame.winfo_children.return_value = []
        app.disk_labels = {}
        app.rows = {name: mock.Mock() for name in ('ram_gb', 'ram_pct', 'cpu_temp')}
        app.health_label = mock.Mock()
        app._set_health_panel = mock.Mock()
        app._check_alerts = mock.Mock()
        app._service_fan_heartbeats = mock.Mock()
        app._update_storage_rows = mock.Mock()
        app._fit_content = mock.Mock()
        with mock.patch.object(overlay.psutil, 'virtual_memory', return_value=SimpleNamespace(
                used=31 * 1024 ** 3, total=32 * 1024 ** 3, percent=97)) as memory:
            app._show_sensor_error(text='--', volume_data={'volumes': []})
        memory.assert_called_once_with()
        self.assertEqual(app.rows['ram_pct'].config.call_args.kwargs['text'], '97%')
        self.assertEqual(app._check_alerts.call_args.args[0]['ram_pct'], 97)
        self.assertNotIn('cpu_temp', app._check_alerts.call_args.args[0])
        self.assertTrue(any('RAM' in finding.text for finding in app.thermal_findings))
        self.assertEqual(app._set_health_panel.call_args.args[1], 2)

    def test_invalid_windows_memory_stays_unavailable_in_display_and_advice(self):
        for percent, used, total in ((float('nan'), 8, 32), (101, 8, 32),
                                     (25, float('inf'), 32), (25, 8, 0), (25, 40, 32)):
            with self.subTest(percent=percent, used=used, total=total):
                app = sensor_app(1)
                app.disk_frame = mock.Mock()
                app.disk_frame.winfo_children.return_value = []
                app.disk_labels = {}
                app.rows = {name: mock.Mock() for name in ('ram_gb', 'ram_pct')}
                app._service_fan_heartbeats = mock.Mock()
                app._update_storage_rows = mock.Mock()
                app._fit_content = mock.Mock()
                with mock.patch.object(overlay.psutil, 'virtual_memory', return_value=SimpleNamespace(
                        used=used, total=total, percent=percent)):
                    app._show_sensor_error(text='--')
                self.assertEqual(app.rows['ram_pct'].config.call_args.kwargs['text'], '--')
                self.assertFalse(any('RAM' in finding.text for finding in app.thermal_findings))

    def test_windows_ram_survives_hardware_error_display(self):
        app = sensor_app(1)
        app.disk_frame = mock.Mock()
        app.disk_frame.winfo_children.return_value = []
        app.disk_labels = {}
        app.rows = {name: mock.Mock() for name in ('ram_gb', 'ram_pct', 'cpu_temp')}
        app._service_fan_heartbeats = mock.Mock()
        app._update_storage_rows = mock.Mock()
        app._fit_content = mock.Mock()
        with mock.patch.object(overlay.psutil, 'virtual_memory', return_value=SimpleNamespace(
                used=8 * 1024 ** 3, total=32 * 1024 ** 3, percent=25)):
            app._show_sensor_error(text='--')
        self.assertEqual(app.rows['ram_gb'].config.call_args.kwargs['text'], '8.0/32.0 GiB')
        self.assertEqual(app.rows['ram_pct'].config.call_args.kwargs['text'], '25%')
        self.assertEqual(app.rows['cpu_temp'].config.call_args.kwargs['text'], '--')


if __name__ == '__main__':
    unittest.main()
