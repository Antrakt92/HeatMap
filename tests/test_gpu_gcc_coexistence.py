"""Background GCC permits GPU assistance, with EC and ownership guards intact."""
import copy
import threading
import unittest
from unittest import mock

import gpu_fans
import hardware_access_guard as guard
import overlay
import test_controller_recovery as recovery_tests
import test_gpu_fan_startup_audit as startup_tests
from test_sensor_lifecycle import sensor_app


class GpuGccCoexistenceTests(unittest.TestCase):
    def test_worker_uses_gpu_scope_at_every_guard(self):
        with mock.patch.object(gpu_fans, '_require_hardware_access') as check:
            gpu_fans.require_hardware_access()
        check.assert_called_once_with('gpu_control')

    def test_hot_gpu_worker_can_take_over_beside_background_gcc(self):
        # Exercise the worker's real GPU guard, not a mocked permission result.
        harness = startup_tests.GpuStartupAuditTests()
        with mock.patch.object(guard, 'hardware_conflicts', return_value=['gcc.exe']):
            result, adapter, baseline, reports, journal = harness.run_worker(
                lambda *_: startup_tests.sample(gpu_hotspot_temp=85, gpu_fan=2000), stop_at=9,
                access_guard=lambda: guard.require_hardware_access('gpu_control'))
        self.assertEqual(result, 0)
        self.assertTrue(any(report.get('command_pct') == 90 for report in reports))
        self.assertEqual(adapter.snapshot(), baseline)
        self.assertFalse(journal)

    def test_shared_monitor_handback_keeps_allowed_gpu_and_stops_case_owner(self):
        app = sensor_app(1)
        app._monitor_coexistence = True
        app._gpu_monitor_coexistence = False
        app.gpu_fan_worker = mock.Mock()
        app.fan_worker = mock.Mock(process=None)
        app._stop_fan_workers_for_monitor()
        app.gpu_fan_worker.stop.assert_not_called()
        app.gpu_fan_worker.process.wait.assert_not_called()
        app.fan_worker.stop.assert_called_once()
        app._stop_fan_workers()
        app.gpu_fan_worker.stop.assert_called_once()

    def test_external_gpu_curve_change_beside_gcc_stops_without_overwriting_it(self):
        external = []
        def change_curve(now, adapter):
            if now == 5:
                adapter.state['points'][0][1] = 31
                external.append(copy.deepcopy(adapter.state))
        with mock.patch.object(guard, 'hardware_conflicts', return_value=['gcc.exe']):
            result, adapter, _, reports, journal = startup_tests.GpuStartupAuditTests().run_worker(
                lambda *_: startup_tests.sample(gpu_hotspot_temp=85, gpu_fan=2000),
                stop_at=9, on_wait=change_curve,
                access_guard=lambda: guard.require_hardware_access('gpu_control'))
        self.assertEqual(result, 1)
        self.assertTrue(reports[-1]['settings_conflict'])
        self.assertEqual(adapter.snapshot(), external[0])
        self.assertTrue(journal)
        self.assertTrue(all(when < 5 for when, *_ in adapter.writes))

    def test_gpu_toggle_is_allowed_while_case_control_stays_paused(self):
        app = overlay.OverlayApp.__new__(overlay.OverlayApp)
        app.config = {'gpu_fans_enabled': False}
        app.lock = threading.Lock()
        app._monitor_coexistence, app._gpu_monitor_coexistence = True, False
        app.gpu_fan_worker = mock.Mock(process=None)
        app._save_config, app._set_menu_label = mock.Mock(), mock.Mock()
        app.toggle_gpu_fans()
        app.gpu_fan_worker.start.assert_called_once_with(accept_external=True)
        self.assertTrue(app.config['gpu_fans_enabled'])
        self.assertTrue(app._fan_control_paused('fan_worker'))
        self.assertFalse(app._fan_control_paused('gpu_fan_worker'))

    def test_verified_gpu_watchdog_retry_remains_available_beside_gcc(self):
        harness = recovery_tests.ControllerRecoveryTests()
        app, worker, _ = harness.app()
        app._monitor_coexistence, app._gpu_monitor_coexistence = True, False
        harness.poll(app, 0, 'gpu_fan_worker', 'gpu_fans_enabled')
        harness.poll(app, 3, 'gpu_fan_worker', 'gpu_fans_enabled')
        worker.start.assert_called_once_with()

    def test_live_gcc_arrival_restricts_sensors_without_stopping_gpu(self):
        app = sensor_app(2, mock.Mock())
        app._gpu_monitor_coexistence = False
        app.gpu_fan_worker = mock.Mock()
        app.fan_worker = mock.Mock(process=None)
        with (mock.patch.object(guard, 'hardware_conflicts', return_value=['gcc.exe']),
              mock.patch.object(overlay, 'init_hardware_monitor', return_value=mock.Mock()),
              mock.patch.object(overlay, 'read_sensors', return_value={'cpu_temp': 50}),
              mock.patch.object(app, '_cache_sensor_diagnostics'),
              mock.patch.object(overlay.psutil, 'cpu_percent')):
            app.sensor_loop()
        self.assertTrue(app._monitor_coexistence)
        self.assertFalse(app._gpu_monitor_coexistence)
        app.gpu_fan_worker.stop.assert_not_called()
        app.fan_worker.stop.assert_called_once()

    def test_gpu_startup_availability_keeps_installers_and_other_tools_blocked(self):
        for names, allowed in (([], True), (['gcc.exe'], True),
                               (['gcc.exe', 'atisetup.exe'], False),
                               (['gcc.exe', 'hwinfo64.exe'], False)):
            with self.subTest(names=names), mock.patch.object(guard, 'hardware_conflicts', return_value=names):
                self.assertEqual(overlay._gpu_control_available(), allowed)
