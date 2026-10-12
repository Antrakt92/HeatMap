"""A stalled filesystem request must not own CPU/GPU sensor updates."""
import unittest
import json
from unittest import mock

import overlay
import volume_probe
import storage_probe
from test_sensor_lifecycle import sensor_app


class VolumeIsolationTests(unittest.TestCase):
    def test_capacity_is_sent_before_disk_identity_query(self):
        from types import SimpleNamespace as NS
        pipe = mock.Mock()

        def mapping(letter):
            self.assertEqual(letter, 'C:')
            self.assertEqual(pipe.send_bytes.call_count, 1)
            progress = json.loads(pipe.send_bytes.call_args.args[0])
            self.assertEqual(progress['phase'], 'capacity')
            self.assertEqual(progress['volume']['free_bytes'], 4)
            return 2

        with (mock.patch.object(volume_probe.psutil, 'disk_usage', return_value=NS(total=400, free=4)),
              mock.patch.object(volume_probe, 'volume_disk_number', side_effect=mapping)):
            volume_probe._worker('C:\\', pipe)
        self.assertEqual(pipe.send_bytes.call_count, 2)
        final = json.loads(pipe.send_bytes.call_args.args[0])
        self.assertEqual(final['volume']['disk_number'], 2)
        pipe.close.assert_called_once()

    def test_capacity_only_retry_never_calls_extent_lookup(self):
        from types import SimpleNamespace as NS
        pipe = mock.Mock()
        with (mock.patch.object(volume_probe.psutil, 'disk_usage', return_value=NS(total=400, free=4)),
              mock.patch.object(volume_probe, 'volume_disk_number') as mapping):
            volume_probe._worker(('C:\\', False), pipe)
        mapping.assert_not_called()
        final = json.loads(pipe.send_bytes.call_args.args[0])
        self.assertEqual(final['volume'], {'name': 'C:', 'total_bytes': 400, 'free_bytes': 4})
        self.assertNotIn('identity_error', final)

    def test_public_capacity_only_option_reaches_worker(self):
        with mock.patch.object(volume_probe, 'run_bounded_probe', return_value={'volume': {}}) as run:
            volume_probe.probe_volume('C:\\', include_identity=False)
        self.assertEqual(run.call_args.args[1], ('C:\\', False))

    def test_identity_failure_preserves_capacity_without_guessing_a_disk(self):
        context = mock.Mock()
        receive, send, process = mock.Mock(), mock.Mock(), mock.Mock()
        context.Pipe.return_value = (receive, send)
        context.Process.return_value = process
        receive.poll.return_value = True
        receive.recv_bytes.side_effect = [json.dumps({
            'phase': 'capacity', 'volume': {'name': 'C:', 'total_bytes': 400, 'free_bytes': 4},
        }).encode(), OSError('disk identity query timed out')]
        process.is_alive.return_value = False
        with (mock.patch.object(storage_probe.multiprocessing, 'get_context', return_value=context),
              mock.patch.object(storage_probe.time, 'monotonic', return_value=100)):
            result = volume_probe.probe_volume('C:\\')
        self.assertEqual(result['volume']['used_pct'], 99)
        self.assertNotIn('disk_number', result['volume'])
        self.assertNotIn('error', result)
        self.assertIn('disk identity query timed out', result['identity_error'])
        self.assertEqual(result['sample_time'], 100)

    def test_queued_final_identity_enriches_capacity_after_worker_exit(self):
        context = mock.Mock()
        receive, send, process = mock.Mock(), mock.Mock(), mock.Mock()
        context.Pipe.return_value = (receive, send)
        context.Process.return_value = process
        receive.poll.return_value = True
        receive.recv_bytes.side_effect = [json.dumps({
            'phase': 'capacity', 'volume': {'name': 'C:', 'total_bytes': 400, 'free_bytes': 4},
        }).encode(), json.dumps({'volume': {
            'name': 'C:', 'total_bytes': 400, 'free_bytes': 4, 'disk_number': 2},
        }).encode()]
        process.is_alive.return_value = False
        with mock.patch.object(storage_probe.multiprocessing, 'get_context', return_value=context):
            result = volume_probe.probe_volume('C:\\')
        self.assertEqual(result['volume']['disk_number'], 2)
        self.assertEqual(result['volume']['used_pct'], 99)
        self.assertNotIn('identity_error', result)

    def test_identity_timeout_retains_capacity_age_and_disposes_blocked_reader(self):
        context = mock.Mock()
        receive, send, process = mock.Mock(), mock.Mock(), mock.Mock()
        context.Pipe.return_value = (receive, send)
        context.Process.return_value = process
        receive.poll.return_value = True
        receive.recv_bytes.return_value = json.dumps({
            'phase': 'capacity', 'volume': {'name': 'C:', 'total_bytes': 400, 'free_bytes': 4},
        }).encode()
        process.is_alive.side_effect = [True, False]
        with (mock.patch.object(storage_probe.multiprocessing, 'get_context', return_value=context),
              mock.patch.object(storage_probe.time, 'monotonic', side_effect=[100, 100, 100, 100, 109])):
            result = volume_probe.probe_volume('C:\\', timeout=8)
        self.assertEqual(result['volume']['used_pct'], 99)
        self.assertEqual(result['sample_time'], 100)
        self.assertIn('timed out', result['identity_error'])
        process.terminate.assert_called_once()
        process.close.assert_called_once()
        receive.close.assert_called_once()

    def test_cancellation_after_capacity_never_publishes_a_late_reading(self):
        context = mock.Mock()
        receive, send, process = mock.Mock(), mock.Mock(), mock.Mock()
        context.Pipe.return_value = (receive, send)
        context.Process.return_value = process
        receive.poll.return_value = True
        receive.recv_bytes.return_value = json.dumps({
            'phase': 'capacity', 'volume': {'name': 'C:', 'total_bytes': 400, 'free_bytes': 4},
        }).encode()
        process.is_alive.side_effect = [True, False]
        with mock.patch.object(storage_probe.multiprocessing, 'get_context', return_value=context):
            result = volume_probe.probe_volume('C:\\', mock.Mock(side_effect=[False, False, True]))
        self.assertIsNone(result)
        process.terminate.assert_called_once()

    def test_invalid_capacity_progress_cannot_become_fallback_data(self):
        context = mock.Mock()
        receive, send, process = mock.Mock(), mock.Mock(), mock.Mock()
        context.Pipe.return_value = (receive, send)
        context.Process.return_value = process
        receive.poll.return_value = True
        receive.recv_bytes.return_value = json.dumps({
            'phase': 'capacity', 'volume': {'name': 'D:', 'total_bytes': 400, 'free_bytes': 4},
        }).encode()
        process.is_alive.return_value = False
        with mock.patch.object(storage_probe.multiprocessing, 'get_context', return_value=context):
            result = volume_probe.probe_volume('C:\\')
        self.assertIn('error', result)
        self.assertNotIn('volume', result)

    def test_one_timeout_does_not_hide_healthy_later_volume_and_backs_off(self):
        app = sensor_app(2)
        first_publications = []

        def probe(mount=None, cancelled=None):
            if mount is None:
                return {'mounts': ['C:\\', 'D:\\', 'E:\\']}
            if mount == 'D:\\':
                return {'error': 'timeout'}
            first_publications.append((mount, app._volume_snapshot))
            return {'volume': {'name': mount[:2], 'used_pct': 91 if mount == 'C:\\' else 10}}

        app._volume_snapshot = None
        with (mock.patch.object(overlay, 'probe_volume', side_effect=probe) as read,
              mock.patch.object(overlay.time, 'monotonic', side_effect=lambda: app._stop_event.now)):
            app.volume_loop()
        self.assertEqual([call.args[0] for call in read.call_args_list if call.args[0] is not None],
                         ['C:\\', 'D:\\', 'E:\\', 'C:\\', 'E:\\'])
        # C: has already been published before E: is even requested.
        self.assertEqual(first_publications[1][1][1]['volumes'][0]['name'], 'C:')
        data = overlay._fresh_volume_data(app._volume_snapshot, app._stop_event.now)
        self.assertEqual([volume['name'] for volume in data['volumes']], ['C:', 'E:'])
        self.assertEqual(data['volume_errors'], ['D: timeout'])

    def test_inventory_failure_removes_old_rows_and_reports_error(self):
        app = sensor_app(1)
        app._volume_snapshot = (100, {'volumes': [{'name': 'C:', 'used_pct': 99}]})
        with (mock.patch.object(overlay, 'probe_volume', return_value={'error': 'enumeration timed out'}),
              mock.patch.object(overlay.time, 'monotonic', return_value=100)):
            app.volume_loop()
        self.assertEqual(app._volume_snapshot[1]['volumes'], [])
        self.assertIn('enumeration timed out', app._volume_snapshot[1]['volume_errors'][0])

    def test_healthy_cached_volume_survives_wait_for_another_volume_without_refreshing_age(self):
        app = sensor_app(1)
        app._volume_snapshot = (95, {'volumes': [
            {'name': 'E:', 'used_pct': 80, '_sample_time': 90},
            {'name': 'F:', 'used_pct': 99, '_sample_time': 90}]})

        def probe(mount, cancelled):
            if mount is None:
                return {'mounts': ['C:\\', 'D:\\', 'E:\\']}
            if mount == 'D:\\':
                data = overlay._fresh_volume_data(app._volume_snapshot, 100)
                self.assertEqual([volume['name'] for volume in data['volumes']], ['C:', 'E:'])
                self.assertEqual(app._volume_snapshot[1]['volumes'][1]['_sample_time'], 90)
                return {'error': 'timeout'}
            return {'volume': {'name': mount[:2], 'used_pct': 20}}

        with (mock.patch.object(overlay, 'probe_volume', side_effect=probe),
              mock.patch.object(overlay.time, 'monotonic', return_value=100)):
            app.volume_loop()

    def test_process_launch_failure_is_visible_and_does_not_escape_volume_owner(self):
        app = sensor_app(1)
        with (mock.patch.object(overlay, 'probe_volume', side_effect=OSError('no process resources')),
              mock.patch.object(overlay.time, 'monotonic', return_value=100)):
            app.volume_loop()
        self.assertIn('no process resources', app._volume_snapshot[1]['volume_errors'][0])

    def test_cancelled_read_does_not_publish(self):
        app = sensor_app(1)
        app._volume_snapshot = None
        with mock.patch.object(overlay, 'probe_volume', return_value=None):
            app.volume_loop()
        self.assertIsNone(app._volume_snapshot)

    def test_sensor_owner_uses_volume_snapshot_without_any_disk_calls(self):
        app = sensor_app(1)
        app._volume_snapshot = (100, {'volumes': [{'name': 'C:', 'used_pct': 91}], 'volume_errors': []})
        with (mock.patch.object(overlay, '_read_volume_usage', side_effect=AssertionError('blocking I/O')),
              mock.patch.object(overlay, 'require_hardware_access'),
              mock.patch.object(overlay, 'init_hardware_monitor', return_value=mock.Mock()),
              mock.patch.object(overlay, 'read_sensors', return_value={'cpu_temp': 45}),
              mock.patch.object(app, '_cache_sensor_diagnostics'),
              mock.patch.object(overlay.psutil, 'cpu_percent'),
              mock.patch.object(overlay.time, 'monotonic', return_value=100)):
            app.sensor_loop()
        self.assertEqual(app.sensor_data['cpu_temp'], 45)
        self.assertEqual(app.sensor_data['volumes'][0]['used_pct'], 91)

    def test_new_publication_cannot_refresh_an_older_volume_sample(self):
        snapshot = (140, {'volumes': [
            {'name': 'C:', 'used_pct': 91, '_sample_time': 80},
            {'name': 'D:', 'used_pct': 12, '_sample_time': 140}], 'volume_errors': []})
        data = overlay._fresh_volume_data(snapshot, 140)
        self.assertEqual(data['volumes'], [{'name': 'D:', 'used_pct': 12}])
        self.assertTrue(any('C:' in error for error in data['volume_errors']))

    def test_validator_rejects_wrong_paths_identity_and_capacity(self):
        for result in (None, {}, {'volume': {'name': 'D:', 'total_bytes': 100, 'free_bytes': 20}},
                       {'volume': {'name': 'C:', 'total_bytes': True, 'free_bytes': 0}},
                       {'volume': {'name': 'C:', 'total_bytes': 100, 'free_bytes': 101}},
                       {'volume': {'name': 'C:', 'total_bytes': 100, 'free_bytes': 20, 'disk_number': True}}):
            with self.subTest(result=result), self.assertRaises(ValueError):
                volume_probe._validate(result, 'C:\\')
        for mounts in (None, ['Z:\\', 'Z:\\'], ['\\\\server\\share'], [True], ['C:']):
            with self.subTest(mounts=mounts), self.assertRaises(ValueError):
                volume_probe._validate({'mounts': mounts}, None)
        for mount in ('C:', 'c:\\', '\\\\server\\share', 1):
            with self.subTest(mount=mount), mock.patch.object(storage_probe.multiprocessing, 'get_context') as spawn:
                with self.assertRaises(ValueError):
                    volume_probe.probe_volume(mount)
                spawn.assert_not_called()

    def test_capacity_percentage_is_recomputed_from_validated_bytes(self):
        result = volume_probe._validate({'volume': {
            'name': 'C:', 'total_bytes': 400, 'free_bytes': 4, 'used_pct': 1, 'disk_number': 2}}, 'C:\\')
        self.assertEqual(result['volume']['used_pct'], 99)
        self.assertEqual(result['volume']['disk_number'], 2)

    def test_volume_timeout_reuses_bounded_disposal_without_disk_sensor_access(self):
        context = mock.Mock()
        receive, send, process = mock.Mock(), mock.Mock(), mock.Mock()
        context.Pipe.return_value = (receive, send)
        context.Process.return_value = process
        process.is_alive.side_effect = [True, False]
        with mock.patch.object(storage_probe.multiprocessing, 'get_context', return_value=context):
            result = volume_probe.probe_volume('C:\\', timeout=0)
        self.assertIn('timed out', result['error'])
        process.terminate.assert_called_once()
        process.close.assert_called_once()
        receive.close.assert_called_once()
        send.close.assert_called()

    def test_fixed_inventory_deduplicates_letters_and_excludes_remote_optical_paths(self):
        from types import SimpleNamespace as NS
        partitions = [NS(mountpoint=mount, opts=opts) for mount, opts in (
            ('C:\\', 'rw,fixed'), ('c:/', 'rw,fixed'), ('D:\\', 'rw,cdrom'),
            ('Z:\\', 'rw,remote'), ('\\\\server\\share', 'rw,fixed'))]
        with (mock.patch.object(volume_probe.psutil, 'disk_partitions', return_value=partitions),
              mock.patch.object(volume_probe.psutil, 'disk_usage') as usage):
            self.assertEqual(volume_probe._query(None), {'mounts': ['C:\\']})
        usage.assert_not_called()


if __name__ == '__main__':
    unittest.main()
