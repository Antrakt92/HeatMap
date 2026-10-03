"""An unavailable native disk must not hide healthy disks or CPU/GPU samples."""
import json
import sys
from types import SimpleNamespace as NS
import unittest
from unittest import mock

import overlay
import storage_probe
from test_sensor_lifecycle import sensor_app


class StorageProbeTests(unittest.TestCase):
    def test_conflict_after_windows_query_prevents_native_smart_constructor(self):
        from hardware_access_guard import HardwareAccessConflict
        constructor = mock.Mock()
        constructor.GetParameters.return_value = [None, None]
        descriptor_type, storage_type = mock.Mock(), mock.Mock()
        storage_type.GetConstructors.return_value = [constructor]
        assembly = mock.Mock()
        assembly.GetType.side_effect = [descriptor_type, storage_type]
        system = NS(Activator=mock.Mock(), Int32=int, Object=object,
                    Array={object: lambda values: values})
        reflection = NS(Assembly=mock.Mock(), BindingFlags=NS(Instance=1, NonPublic=2))
        reflection.Assembly.LoadFrom.return_value = assembly
        with (mock.patch.dict(sys.modules, {'clr': mock.Mock(), 'System': system, 'System.Reflection': reflection}),
              mock.patch('setup.verify_lib_manifest', return_value=(True, [])),
              mock.patch.object(storage_probe, 'disk_properties', return_value=None),
              mock.patch.object(storage_probe, 'require_hardware_access', side_effect=[
                  'full', HardwareAccessConflict('GPU driver installation started')])):
            with self.assertRaises(HardwareAccessConflict):
                storage_probe._native_sample(1)
        constructor.Invoke.assert_not_called()

    def test_current_identification_is_not_overwritten_by_an_older_device_model(self):
        app = sensor_app(1)
        app.config = {}
        app._storage_snapshot = {1: (80, {'disk_number': 1, 'name': 'Old SSD', 'temp': 40})}
        app._volume_snapshot = None
        with (mock.patch.object(overlay.time, 'monotonic', return_value=100),
              mock.patch.object(overlay, 'physical_disk_numbers', return_value={1}),
              mock.patch.object(overlay, 'require_hardware_access'),
              mock.patch.object(overlay, 'probe_disk', return_value={
                  'disk_number': 1, 'name': 'Replacement SSD', 'error': 'SMART timeout'})):
            app.storage_loop()
        self.assertEqual(app._storage_snapshot[1][1]['name'], 'Replacement SSD')
        self.assertNotIn('temp', app._storage_snapshot[1][1])

    def test_malformed_identification_error_is_reported_without_escaping_the_probe(self):
        context = mock.Mock()
        receive, send, process = mock.Mock(), mock.Mock(), mock.Mock()
        context.Pipe.return_value = (receive, send)
        context.Process.return_value = process
        process.is_alive.return_value = False
        receive.poll.return_value = True
        receive.recv_bytes.return_value = json.dumps({
            'disk_number': 1, 'phase': 'identified', 'error': 'failure'}).encode()
        with mock.patch.object(storage_probe.multiprocessing, 'get_context', return_value=context):
            result = storage_probe.probe_disk(1)
        self.assertIn('Invalid storage identification', result['error'])
        process.close.assert_called_once()

    def test_pending_kernel_io_cannot_force_an_unbounded_overlay_exit_join(self):
        context = mock.Mock()
        receive, send, process = mock.Mock(), mock.Mock(), mock.Mock()
        context.Pipe.return_value = (receive, send)
        context.Process.return_value = process
        process.is_alive.return_value = True
        children = {process}
        with (mock.patch.object(storage_probe.multiprocessing, 'get_context', return_value=context),
              mock.patch('multiprocessing.process._children', children)):
            sample = storage_probe.probe_disk(0, timeout=0)
        self.assertIn('error', sample)
        process.terminate.assert_called_once()
        self.assertNotIn(process, children)
        process.close.assert_not_called()

    def test_timeout_terminates_and_reaps_only_the_disposable_probe(self):
        context = mock.Mock()
        receive, send, process = mock.Mock(), mock.Mock(), mock.Mock()
        context.Pipe.return_value = (receive, send)
        context.Process.return_value = process
        process.is_alive.side_effect = [True, False]
        with mock.patch.object(storage_probe.multiprocessing, 'get_context', return_value=context):
            sample = storage_probe.probe_disk(3, timeout=0)
        self.assertEqual(sample['disk_number'], 3)
        self.assertIn('timed out', sample['error'])
        process.terminate.assert_called_once()
        self.assertEqual(process.join.call_count, 2)
        process.close.assert_called_once()
        receive.close.assert_called_once()
        self.assertTrue(context.Process.call_args.kwargs['daemon'])

    def test_shutdown_cancels_active_probe_without_publishing_a_false_reading(self):
        context = mock.Mock()
        receive, send, process = mock.Mock(), mock.Mock(), mock.Mock()
        context.Pipe.return_value = (receive, send)
        context.Process.return_value = process
        process.is_alive.side_effect = [True, False]
        with mock.patch.object(storage_probe.multiprocessing, 'get_context', return_value=context):
            result = storage_probe.probe_disk(2, mock.Mock(side_effect=[False, True]))
        self.assertIsNone(result)
        process.terminate.assert_called_once()

    def test_wrong_identity_and_malformed_reply_cannot_assign_temperature(self):
        for reply in (b'{', json.dumps({'disk_number': 1, 'temp': 40}).encode(),
                      json.dumps({'disk_number': True, 'temp': 40}).encode()):
            with self.subTest(reply=reply):
                context = mock.Mock()
                receive, send, process = mock.Mock(), mock.Mock(), mock.Mock()
                context.Pipe.return_value = (receive, send)
                context.Process.return_value = process
                receive.poll.return_value = True
                receive.recv_bytes.return_value = reply
                process.is_alive.return_value = False
                with mock.patch.object(storage_probe.multiprocessing, 'get_context', return_value=context):
                    sample = storage_probe.probe_disk(2)
                self.assertIn('error', sample)
                self.assertNotIn('temp', sample)

    def test_invalid_identity_never_starts_hardware_reader(self):
        for number in (-1, True, '2', 2**32):
            with self.subTest(number=number), mock.patch.object(storage_probe.multiprocessing, 'get_context') as spawn:
                with self.assertRaises(ValueError):
                    storage_probe.probe_disk(number)
                spawn.assert_not_called()

    def test_identified_model_survives_later_native_failure_without_a_temperature(self):
        context = mock.Mock()
        receive, send, process = mock.Mock(), mock.Mock(), mock.Mock()
        context.Pipe.return_value = (receive, send)
        context.Process.return_value = process
        receive.poll.return_value = True
        receive.recv_bytes.side_effect = [
            json.dumps({'disk_number': 0, 'name': 'WD40EZAZ', 'temp': None, 'phase': 'identified'}).encode(),
            EOFError('native reader exited'),
        ]
        process.is_alive.return_value = False
        with mock.patch.object(storage_probe.multiprocessing, 'get_context', return_value=context):
            sample = storage_probe.probe_disk(0)
        self.assertEqual(sample['name'], 'WD40EZAZ')
        self.assertIn('error', sample)
        self.assertNotIn('temp', sample)

    def test_non_finite_or_malformed_temperatures_cannot_reach_display(self):
        for value in (float('nan'), float('inf'), True, '45', -1, 151):
            with self.subTest(value=value), self.assertRaises(ValueError):
                storage_probe._validated_sample({'disk_number': 1, 'name': 'SSD', 'temp': value}, 1)
        sample = storage_probe._validated_sample(
            {'disk_number': 1, 'name': 'SSD', 'temp': 45, 'aux_temp': 150,
             'temperatures': [{'name': 'Temperature 2', 'temp': 51.85}]}, 1)
        self.assertEqual(sample['temp'], 45)
        self.assertEqual(sample['aux_temp'], 52)

    def test_snapshot_keeps_healthy_disk_but_hides_expired_and_removed_readings(self):
        snapshot = {
            1: (100, {'disk_number': 1, 'name': '860 EVO', 'temp': 27}),
            2: (40, {'disk_number': 2, 'name': 'Lexar', 'temp': 41,
                     'temperatures': [{'temp': 42}], 'life_pct': 99}),
            3: (100, {'disk_number': 3, 'temp': 45}),
        }
        data = storage_probe.snapshot_data(snapshot, {1, 2}, 110)
        self.assertEqual(data['disks'][0]['temp'], 27)
        self.assertIsNone(data['disks'][1]['temp'])
        self.assertEqual(data['disks'][1]['name'], 'Lexar')
        self.assertNotIn('temperatures', data['disks'][1])
        self.assertNotIn('life_pct', data['disks'][1])
        self.assertEqual(len(data['disks']), 2)
        self.assertIn('expired', data['storage_errors'][0])

    def test_failed_disk_does_not_prevent_later_disks_being_probed(self):
        app = sensor_app(1)
        app.config = {}
        app._storage_snapshot = {}
        app._volume_snapshot = (100, {'volumes': [{'disk_number': 2}, {'disk_number': 1}, {'disk_number': 2}]})
        def probe(number, cancelled):
            return {'disk_number': number, **({'error': 'timeout'} if number == 1 else {'name': 'Lexar', 'temp': 41})}
        with (mock.patch.object(overlay.time, 'monotonic', return_value=100),
              mock.patch.object(overlay, 'physical_disk_numbers', return_value={1, 2}),
              mock.patch.object(overlay, 'require_hardware_access'),
              mock.patch.object(overlay, 'probe_disk', side_effect=probe) as read):
            app.storage_loop()
        self.assertEqual([call.args[0] for call in read.call_args_list], [1, 2])
        self.assertEqual(app._storage_snapshot[2][1]['temp'], 41)

    def test_sensor_owner_excludes_global_storage_and_merges_confirmed_identity(self):
        app = sensor_app(1)
        app.config = {}
        app._isolated_storage = True
        app._storage_snapshot = {3: (100, {'disk_number': 3, 'name': '980 PRO', 'temp': 45})}
        volumes = {'volumes': [{'name': 'C:', 'disk_number': 3, 'used_pct': 46}]}
        app._volume_snapshot = (100, volumes)
        with (mock.patch.object(overlay.time, 'monotonic', return_value=100),
              mock.patch.object(overlay, 'require_hardware_access', return_value='full'),
              mock.patch.object(overlay, 'init_hardware_monitor', return_value=mock.Mock()) as initialize,
              mock.patch.object(overlay, 'read_sensors', return_value={'cpu_temp': 45, 'gpu_temp': 46}),
              mock.patch.object(overlay, '_read_volume_usage', return_value=volumes),
              mock.patch.object(app, '_cache_sensor_diagnostics'),
              mock.patch.object(overlay.psutil, 'cpu_percent')):
            app.sensor_loop()
        initialize.assert_called_once_with(storage_enabled=False)
        self.assertEqual(app.sensor_data['cpu_temp'], 45)
        self.assertEqual(app.sensor_data['disks'][0]['disk_number'], 3)
        self.assertEqual(app.sensor_data['disks'][0]['temp'], 45)
        self.assertEqual(app.sensor_data['volumes'][0]['used_pct'], 46)

    def test_failed_disk_backs_off_while_other_disks_keep_refreshing(self):
        app = sensor_app(2)
        app.config = {}
        app._storage_snapshot = {}
        app._volume_snapshot = (100, {'volumes': [{'disk_number': 1}, {'disk_number': 2}]})
        def probe(number, cancelled):
            return {'disk_number': number, **({'error': 'timeout'} if number == 1 else {'name': 'SSD', 'temp': 41})}
        with (mock.patch.object(overlay.time, 'monotonic', side_effect=lambda: app._stop_event.now),
              mock.patch.object(overlay, 'physical_disk_numbers', return_value={1, 2}),
              mock.patch.object(overlay, 'require_hardware_access'),
              mock.patch.object(overlay, 'probe_disk', side_effect=probe) as read):
            app.storage_loop()
        self.assertEqual([call.args[0] for call in read.call_args_list], [1, 2, 2])


if __name__ == '__main__':
    unittest.main()
