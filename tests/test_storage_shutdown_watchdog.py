"""Owner scheduling delays must not discard timely replies or refresh their age."""
import json
import sys
from types import SimpleNamespace as NS
import unittest
from unittest import mock

import storage_probe
import volume_probe


class DelayedProbeOwnerTests(unittest.TestCase):
    def test_close_stage_pipe_failure_cannot_skip_native_dispose(self):
        storage = mock.Mock(Model='SSD', IsNVMe=True, Smart=NS(Temperature=40, Life=None))
        constructor = mock.Mock()
        constructor.GetParameters.return_value = [None, None]
        constructor.Invoke.return_value = storage
        descriptor_type, storage_type = mock.Mock(), mock.Mock()
        storage_type.GetConstructors.return_value = [constructor]
        storage_type.GetProperty.return_value.GetValue.return_value = True
        assembly = mock.Mock()
        assembly.GetType.side_effect = [descriptor_type, storage_type]
        system = NS(Activator=mock.Mock(), Int32=int, Object=object,
                    Array={object: lambda values: values})
        reflection = NS(Assembly=mock.Mock(), BindingFlags=NS(Instance=1, NonPublic=2))
        reflection.Assembly.LoadFrom.return_value = assembly
        hardware = NS(Update=mock.Mock(), Sensors=[])

        def progress(stage):
            if stage == 'close':
                raise OSError('owner pipe already closed')

        with (mock.patch.dict(sys.modules, {
                  'clr': mock.Mock(), 'System': system, 'System.Reflection': reflection,
                  'LibreHardwareMonitor.Hardware': NS(Computer=mock.Mock(),
                                                      SensorType=NS(Temperature='temperature')),
                  'LibreHardwareMonitor.Hardware.Storage': NS(StorageDevice=mock.Mock(return_value=hardware))}),
              mock.patch('setup.verify_lib_manifest', return_value=(True, [])),
              mock.patch.object(storage_probe, 'disk_properties', return_value=None),
              mock.patch.object(storage_probe, 'require_hardware_access', return_value='full')):
            with self.assertRaisesRegex(OSError, 'owner pipe already closed'):
                storage_probe._native_sample(2, progress=progress)
        storage.Dispose.assert_called_once()

    def test_poll_crossing_deadline_cannot_accept_late_worker_completion(self):
        context = mock.Mock()
        receive, send, process = mock.Mock(), mock.Mock(), mock.Mock()
        context.Pipe.return_value = receive, send
        context.Process.return_value = process
        process.is_alive.return_value = False
        clock = [100]

        def ready(_timeout):
            clock[0] = 110
            return True

        receive.poll.side_effect = ready
        receive.recv_bytes.return_value = json.dumps({
            'disk_number': 2, 'name': 'SSD', 'temp': 40, 'sample_time': 109}).encode()
        with (mock.patch.object(storage_probe.multiprocessing, 'get_context', return_value=context),
              mock.patch.object(storage_probe.time, 'monotonic', side_effect=lambda: clock[0])):
            sample = storage_probe.probe_disk(2, timeout=8)
        self.assertIn('timed out', sample['error'])
        self.assertNotIn('temp', sample)

    def delayed_reply(self, reply, *, cancelled=lambda: False):
        context = mock.Mock()
        receive, send, process = mock.Mock(), mock.Mock(), mock.Mock()
        context.Pipe.return_value = receive, send
        context.Process.return_value = process
        process.is_alive.return_value = False
        receive.poll.return_value = True
        receive.recv_bytes.return_value = json.dumps(reply).encode()
        calls = [0]

        def clock():
            calls[0] += 1
            return 100 if calls[0] == 1 else 110

        with (mock.patch.object(storage_probe.multiprocessing, 'get_context', return_value=context),
              mock.patch.object(storage_probe.time, 'monotonic', side_effect=clock)):
            sample = storage_probe.probe_disk(2, cancelled, timeout=8)
        return sample, process, receive

    def test_timely_completed_reply_survives_owner_delay_past_deadline(self):
        sample, process, receive = self.delayed_reply({
            'disk_number': 2, 'name': 'SSD', 'temp': 40, 'sample_time': 102})
        self.assertNotIn('error', sample)
        self.assertEqual(sample['temp'], 40)
        self.assertEqual(sample['sample_time'], 102)
        receive.recv_bytes.assert_called_once()
        process.terminate.assert_not_called()
        data = storage_probe.snapshot_data({2: (sample['sample_time'], sample)}, {2}, 148)
        self.assertIsNone(data['disks'][0]['temp'], 'Delivery cannot reset the temperature age')

    def test_worker_completion_after_deadline_cannot_extend_native_budget(self):
        sample, process, _receive = self.delayed_reply({
            'disk_number': 2, 'name': 'SSD', 'temp': 40, 'sample_time': 109})
        self.assertIn('timed out', sample['error'])
        self.assertNotIn('temp', sample)
        process.close.assert_called_once()

    def test_unstamped_reply_after_deadline_cannot_claim_timely_completion(self):
        sample, _process, _receive = self.delayed_reply({
            'disk_number': 2, 'name': 'SSD', 'temp': 40})
        self.assertIn('timed out', sample['error'])
        self.assertNotIn('temp', sample)

    def test_shutdown_still_discards_even_a_timely_queued_reply(self):
        sample, _process, receive = self.delayed_reply({
            'disk_number': 2, 'name': 'SSD', 'temp': 40, 'sample_time': 102},
            cancelled=mock.Mock(side_effect=[False, True]))
        self.assertIsNone(sample)
        receive.recv_bytes.assert_not_called()

    def test_delayed_volume_capacity_retains_worker_sample_time(self):
        context = mock.Mock()
        receive, send, process = mock.Mock(), mock.Mock(), mock.Mock()
        context.Pipe.return_value = receive, send
        context.Process.return_value = process
        process.is_alive.return_value = False
        receive.poll.return_value = True
        receive.recv_bytes.side_effect = [json.dumps(reply).encode() for reply in (
            {'phase': 'capacity', 'sample_time': 102,
             'volume': {'name': 'C:', 'total_bytes': 100, 'free_bytes': 20}},
            {'sample_time': 104, 'volume': {
                'name': 'C:', 'total_bytes': 100, 'free_bytes': 20, 'disk_number': 2}})]
        calls = [0]

        def clock():
            calls[0] += 1
            return 100 if calls[0] == 1 else 110

        with (mock.patch.object(storage_probe.multiprocessing, 'get_context', return_value=context),
              mock.patch.object(storage_probe.time, 'monotonic', side_effect=clock)):
            sample = volume_probe.probe_volume('C:\\', timeout=8)
        self.assertNotIn('error', sample)
        self.assertEqual(sample['sample_time'], 102)
        self.assertEqual(sample['volume']['disk_number'], 2)
        self.assertEqual(receive.recv_bytes.call_count, 2)

    def test_worker_reports_start_before_entering_native_reader(self):
        pipe = mock.Mock()

        def native(_number, _identified, _progress):
            first = json.loads(pipe.send_bytes.call_args.args[0])
            self.assertEqual(first['stage'], 'start')
            self.assertEqual(first['sample_time'], 100)
            return {'disk_number': 2, 'name': 'SSD', 'temp': 40}

        with (mock.patch.object(storage_probe, '_native_sample', side_effect=native),
              mock.patch.object(storage_probe.time, 'monotonic', return_value=100)):
            storage_probe._probe_worker(2, pipe)
        final = json.loads(pipe.send_bytes.call_args.args[0])
        self.assertEqual(final['sample_time'], 100)
        self.assertEqual(final['temp'], 40)
        pipe.close.assert_called_once()

    def test_timeout_retains_validated_last_native_stage(self):
        context = mock.Mock()
        receive, send, process = mock.Mock(), mock.Mock(), mock.Mock()
        context.Pipe.return_value = receive, send
        context.Process.return_value = process
        process.is_alive.return_value = False
        receive.poll.side_effect = [True, False]
        receive.recv_bytes.return_value = json.dumps({
            'disk_number': 2, 'phase': 'stage', 'stage': 'properties', 'sample_time': 102}).encode()
        calls = [0]

        def clock():
            calls[0] += 1
            return 100 if calls[0] == 1 else 110

        with (mock.patch.object(storage_probe.multiprocessing, 'get_context', return_value=context),
              mock.patch.object(storage_probe.time, 'monotonic', side_effect=clock)):
            sample = storage_probe.probe_disk(2, timeout=8)
        self.assertIn('last stage: properties', sample['error'])
        self.assertNotIn('temp', sample)

    def test_capacity_producer_stamps_before_extent_query_finishes(self):
        pipe = mock.Mock()

        def query(_mount, capacity, *, include_identity):
            capacity({'phase': 'capacity', 'volume': {
                'name': 'C:', 'total_bytes': 100, 'free_bytes': 20}})
            return {'volume': {'name': 'C:', 'total_bytes': 100,
                               'free_bytes': 20, 'disk_number': 2}}

        with (mock.patch.object(volume_probe, '_query', side_effect=query),
              mock.patch.object(storage_probe.time, 'monotonic', side_effect=[102, 104])):
            volume_probe._worker('C:\\', pipe)
        replies = [json.loads(call.args[0]) for call in pipe.send_bytes.call_args_list]
        self.assertEqual([reply['sample_time'] for reply in replies], [102, 104])
        self.assertEqual(replies[0]['phase'], 'capacity')
        self.assertEqual(replies[1]['volume']['disk_number'], 2)
