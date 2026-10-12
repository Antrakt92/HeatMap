import copy
import json
import os
import tempfile
import unittest
from unittest.mock import patch

import overlay
from test_sensor_lifecycle import sensor_app


class DeepAuditIntegrationTests(unittest.TestCase):
    def test_overlapping_config_saves_keep_separate_staged_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'config.json')
            replace = os.replace
            nested = []
            def overlap(source, destination):
                if not nested:
                    nested.append(True)
                    self.assertTrue(overlay.save_config({'second': 2})[0])
                replace(source, destination)
            with patch.object(overlay, 'CONFIG_PATH', path), patch.object(overlay.os, 'replace', side_effect=overlap):
                self.assertTrue(overlay.save_config({'first': 1})[0])
            with open(path, encoding='utf-8') as stream:
                self.assertEqual(json.load(stream), {'first': 1})
            self.assertEqual(os.listdir(directory), ['config.json'])

    def test_mapping_backoff_retains_fresh_capacity_and_its_actual_sample_time(self):
        app = sensor_app(2)
        calls = []
        def probe(mount, cancelled, **options):
            if mount is None:
                return {'mounts': ['C:\\']}
            calls.append(options)
            result = {'volume': {'name': 'C:', 'used_pct': 55}, 'sample_time': 97}
            if not options:
                result['identity_error'] = 'Physical disk mapping unavailable: timeout'
            return result
        with patch.object(overlay, 'probe_volume', side_effect=probe), patch.object(overlay.time, 'monotonic', return_value=100):
            app.volume_loop()
        self.assertEqual(calls, [{}, {'include_identity': False}])
        self.assertEqual(app._volume_snapshot[1]['volumes'][0]['_sample_time'], 97)
        self.assertIn('mapping unavailable', app._volume_snapshot[1]['volume_errors'][0])

    def test_exact_fresh_adlx_activity_overrides_stale_but_in_range_d3d_value(self):
        data = dict(gpu_name='AMD Radeon RX 7900 XT', gpu_name_match_count=1, gpu_load=77, gpu_load_source='Windows D3D')
        status = dict(state='standby', time=100, readings_fresh=True,
                      gpu=dict(name=data['gpu_name'], vendor='1002', device='744c', subsystem='240c', subvendor='1458'),
                      readings=dict(timestamp_ms=50, gpu_load=0))
        updated = overlay._gpu_activity_for_display(data, status, now=100)
        self.assertEqual((updated['gpu_load'], updated['gpu_load_source']), (0, 'AMD ADLX'))
        for changes in ({'time': 90}, {'readings_fresh': False}, {'state': 'error'},
                        {'gpu': dict(status['gpu'], subsystem='other')},
                        {'readings': dict(timestamp_ms=50, gpu_load=101)}):
            self.assertEqual(overlay._gpu_activity_for_display(data, dict(status, **changes), now=100), data)
        self.assertEqual(data['gpu_load'], 77)
        self.assertEqual(overlay._gpu_activity_for_display(dict(data, gpu_name_match_count=2), status, now=100)['gpu_load'], 77)

    def test_completed_unknown_mapping_clears_expired_timeout_warning(self):
        app = sensor_app(2)
        now = [100]
        probes = [0]
        def probe(mount, cancelled, **options):
            if mount is None:
                return {'mounts': ['C:\\']}
            probes[0] += 1
            result = {'volume': {'name': 'C:', 'used_pct': 55}, 'sample_time': now[0]}
            if probes[0] == 1:
                result['identity_error'] = 'mapping timeout'
                now[0] = 500
            return result
        # Move beyond backoff between iterations, after its first deadline is saved.
        original_wait = app._stop_event.wait
        def wait(seconds):
            now[0] = 1000
            return original_wait(seconds)
        app._stop_event.wait = wait
        with patch.object(overlay, 'probe_volume', side_effect=probe), patch.object(overlay.time, 'monotonic', side_effect=lambda: now[0]):
            app.volume_loop()
        self.assertEqual(probes[0], 2)
        self.assertEqual(app._volume_snapshot[1]['volume_errors'], [])


if __name__ == '__main__':
    unittest.main()
