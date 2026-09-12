"""Mounted-volume pressure must not be hidden by a physical SSD percentage."""
from types import SimpleNamespace as NS
import threading
from unittest import TestCase, mock

import overlay
from thermal_policy import ThermalAdvisor
from test_thermal_advisor import sample
from test_sensor_lifecycle import sensor_app
from test_overlay_helpers import _sample_data, _update_ui_app


class VolumeSpaceTests(TestCase):
    def test_volume_freshness_accepts_normal_long_running_windows_uptime(self):
        data = dict(volumes=[], volume_errors=[])
        self.assertIs(overlay._fresh_volume_data((100000, data), 100001), data)
        self.assertIsNone(overlay._fresh_volume_data((100000, data), 100041))

    def test_fresh_volume_warning_survives_hardware_sensor_errors(self):
        app = _update_ui_app()
        app.health_label = mock.Mock()
        app._check_alerts = mock.Mock()
        data = dict(volumes=[dict(name='C:', used_pct=99, free_bytes=2**30)], volume_errors=[])
        app.sensor_data = dict(error='LHM read failed', **data)
        app._sensor_sample_time = 100
        app._volume_snapshot = (99, data)
        with mock.patch.object(overlay.time, 'monotonic', return_value=100):
            app.update_ui()
        self.assertTrue(any(item.key == 'volume:C:' for item in app.thermal_findings))
        self.assertTrue(any('Volume C:' in message for message in app.health_messages))
        self.assertIn('Fresh sensor data unavailable', app.health_messages)
        self.assertEqual(app.rows['cpu_temp'].options['text'], 'ERR')
        app._check_alerts.assert_called_once_with({})

    def test_old_or_unstamped_volume_values_are_not_reused_during_sensor_errors(self):
        data = dict(volumes=[dict(name='C:', used_pct=99, free_bytes=2**30)], volume_errors=[])
        for snapshot in (None, (50, data), (101, data)):
            with self.subTest(snapshot=snapshot):
                app = _update_ui_app()
                app.health_label = mock.Mock()
                app._check_alerts = mock.Mock()
                app.sensor_data = dict(error='LHM read failed', **data)
                app._sensor_sample_time = 100
                app._volume_snapshot = snapshot
                with mock.patch.object(overlay.time, 'monotonic', return_value=100):
                    app.update_ui()
                self.assertFalse(app.thermal_findings)
                self.assertFalse(any('Volume C:' in message for message in app.health_messages))
                app._check_alerts.assert_not_called()

    def test_new_thermal_sample_does_not_refresh_an_old_volume_measurement(self):
        app = _update_ui_app()
        data = dict(volumes=[dict(name='C:', used_pct=99, free_bytes=2**30)], volume_errors=[])
        app.sensor_data = dict(_sample_data(), **data)
        app._sensor_sample_time = 100
        app._volume_snapshot = (50, data)
        with mock.patch.object(overlay.time, 'monotonic', return_value=100):
            app.update_ui()
        self.assertFalse(any(item.key == 'volume:C:' for item in app.thermal_findings))

    def test_diagnostics_append_latest_volume_sample_independently_of_lhm_cache(self):
        data = dict(volumes=[dict(name='C:', used_pct=99, free_bytes=2**30)], volume_errors=[])
        for stamp in (99, 50):
            with self.subTest(stamp=stamp):
                app = overlay.OverlayApp.__new__(overlay.OverlayApp)
                app.running = True
                app.root = mock.Mock()
                app._stop_event = threading.Event()
                app._set_menu_label = mock.Mock()
                app.lock = threading.Lock()
                app.sensor_data = dict(error='LHM read failed', **data)
                app._sensor_diagnostics_snapshot = (90, 'Previously cached LHM inventory')
                app._volume_snapshot = (stamp, data)
                with mock.patch.object(overlay.time, 'monotonic', return_value=100):
                    app.copy_diagnostics()
                    app._diagnostics_thread.join(3)
                ok, report = app._diagnostics_results.get_nowait()
                self.assertTrue(ok)
                self.assertIn('Previously cached LHM inventory', report)
                self.assertIn('Latest volume space', report)
                if stamp == 99:
                    self.assertIn('"used_pct": 99', report)
                    self.assertIn('age: 1.0s', report)
                else:
                    self.assertIn('not current', report)
                    self.assertNotIn('"used_pct": 99', report)

    def test_volume_snapshot_is_published_even_when_hardware_reads_fail(self):
        app = sensor_app(2, mock.Mock())
        data = dict(volumes=[], volume_errors=['C: denied'])
        with mock.patch.object(overlay, 'require_hardware_access'), \
                mock.patch.object(overlay, '_read_volume_usage', return_value=data), \
                mock.patch.object(overlay, 'read_sensors', side_effect=OSError('LHM read failed')), \
                mock.patch.object(overlay, 'log'), \
                mock.patch.object(overlay.time, 'monotonic', side_effect=lambda: app._stop_event.now):
            app.sensor_loop()
        self.assertEqual(app._volume_snapshot, (100, data))
        self.assertIn('error', app.sensor_data)

    def test_sensor_loop_refreshes_volume_data_every_30_seconds_and_copies_diagnostics(self):
        app = sensor_app(17, mock.Mock())
        reports = [dict(volumes=[dict(name='C:', total_bytes=100, free_bytes=2, used_pct=98)], volume_errors=[]),
                   dict(volumes=[], volume_errors=['C: denied'])]
        with mock.patch.object(overlay, 'require_hardware_access'), \
                mock.patch.object(overlay, '_read_volume_usage', side_effect=reports) as volumes, \
                mock.patch.object(overlay, 'read_sensors', side_effect=lambda *a, **k: sample()), \
                mock.patch.object(overlay.time, 'monotonic', side_effect=lambda: app._stop_event.now):
            app.sensor_loop()
        self.assertEqual(volumes.call_count, 2)
        self.assertEqual(app.sensor_data['volumes'], [])
        self.assertEqual(app.sensor_data['volume_errors'], ['C: denied'])
        diagnostics = overlay.build_sensor_diagnostics(None, reports[0], is_admin=False, pawnio_installed=False)
        self.assertIn('free_bytes', diagnostics)
        self.assertIn('C:', diagnostics)

    def test_reads_fixed_drive_letters_once_and_uses_volume_capacity(self):
        partitions = [NS(mountpoint='C:\\', opts='rw,fixed'),
                      NS(mountpoint='c:\\', opts='rw,fixed'),
                      NS(mountpoint='D:\\', opts='rw,cdrom'),
                      NS(mountpoint='Z:\\', opts='rw,remote')]
        with mock.patch.object(overlay.psutil, 'disk_partitions', return_value=partitions), \
                mock.patch.object(overlay.psutil, 'disk_usage', return_value=NS(total=400, free=4)) as usage:
            result = overlay._read_volume_usage()
        self.assertEqual(result['volumes'], [dict(name='C:', total_bytes=400, free_bytes=4, used_pct=99.0)])
        self.assertEqual(result['volume_errors'], [])
        usage.assert_called_once_with('C:\\')

    def test_failed_volume_does_not_hide_another_volume_or_return_old_values(self):
        partitions = [NS(mountpoint='C:\\', opts='rw,fixed'), NS(mountpoint='D:\\', opts='rw,fixed')]
        with mock.patch.object(overlay.psutil, 'disk_partitions', return_value=partitions), \
                mock.patch.object(overlay.psutil, 'disk_usage', side_effect=[PermissionError('denied'), NS(total=100, free=30)]):
            result = overlay._read_volume_usage()
        self.assertEqual([v['name'] for v in result['volumes']], ['D:'])
        self.assertIn('C:', result['volume_errors'][0])

    def test_invalid_capacity_is_not_shown_as_healthy(self):
        for total, free in [(0, 0), (100, -1), (100, 101), (float('nan'), 10), (100, True)]:
            with self.subTest(total=total, free=free), \
                    mock.patch.object(overlay.psutil, 'disk_partitions', return_value=[NS(mountpoint='C:\\', opts='rw,fixed')]), \
                    mock.patch.object(overlay.psutil, 'disk_usage', return_value=NS(total=total, free=free)):
                result = overlay._read_volume_usage()
                self.assertEqual(result['volumes'], [])
                self.assertTrue(result['volume_errors'])

    def test_partition_enumeration_failure_is_explicit(self):
        with mock.patch.object(overlay.psutil, 'disk_partitions', side_effect=OSError('unavailable')):
            result = overlay._read_volume_usage()
        self.assertEqual(result['volumes'], [])
        self.assertTrue(result['volume_errors'])

    def test_nearly_full_partition_warns_even_when_physical_disk_reads_80_percent(self):
        data = sample(disks=[dict(name='SSD', temp=38, used_pct=80)],
                      volumes=[dict(name='C:', used_pct=98.9, total_bytes=400 * 2**30, free_bytes=4.2 * 2**30)])
        findings = ThermalAdvisor().evaluate(data, 0, overlay._METRIC_THRESHOLDS, overlay._disk_temperature_thresholds)
        self.assertEqual(findings[0].key, 'volume:C:')
        self.assertEqual(findings[0].severity, 2)
        self.assertIn('99% full', findings[0].text)
        self.assertIn('4.2 GiB free', findings[0].text)

    def test_volume_read_errors_are_visible(self):
        findings = ThermalAdvisor().evaluate(sample(volume_errors=['C: access denied']), 0,
                                             overlay._METRIC_THRESHOLDS, overlay._disk_temperature_thresholds)
        self.assertTrue(any('C: access denied' in item.text for item in findings))
