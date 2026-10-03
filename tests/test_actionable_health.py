"""User-facing problems stay brief while diagnostic evidence remains intact."""
from unittest import mock

import overlay
from test_thermal_advisor import sample
from test_ui_layout import TkTestCase, layout_app


class ActionableHealthTests(TkTestCase):
    def test_normal_waiting_does_not_warn_but_pending_restore_does(self):
        with layout_app() as app:
            app._gpu_fan_status = dict(state='checking', phase='waiting', reason='Waiting GPU')
            app._set_health_panel([], 0)
            self.assertEqual(app.health_label.winfo_manager(), '')
            app._gpu_fan_status['recovery_pending'] = True
            app._set_health_panel([], 0)
            self.assertIn('Recovery pending', app.health_label.cget('text'))

    def test_disabled_case_control_does_not_warn_about_other_monitor(self):
        with layout_app() as app:
            app.config['case_fans_enabled'] = False
            app._monitor_coexistence = True
            app._update_thermal_advice(sample())
            self.assertEqual(app.health_label.cget('text'), '')
            self.assertEqual(app.health_label.winfo_manager(), '')
            app.config['case_fans_enabled'] = True
            app._update_thermal_advice(sample())
            self.assertIn('Close other monitor', app.health_label.cget('text'))

    def test_volume_error_is_brief_and_clears_on_fresh_success(self):
        with layout_app() as app:
            raw = 'C: Volume probe timed out or exited without a reading'
            app._update_thermal_advice(sample(volume_errors=[raw]))
            self.assertEqual(app.health_label.cget('text'), 'C: space unavailable; click for details')
            self.assertIn(raw, '\n'.join(app.health_messages))
            app._update_thermal_advice(sample(volumes=[]))
            self.assertEqual(app.health_label.winfo_manager(), '')

    def test_autostart_stack_is_retained_only_in_diagnostics(self):
        with layout_app() as app:
            raw = 'Autostart: exit code 1: Get-ScheduledTask\nHRESULT 0x80070002'
            app._autostart_warning = raw
            app._set_health_panel([], 0)
            self.assertEqual(app.health_label.cget('text'), 'Autostart: check failed; click for details')
            self.assertEqual(app.health_messages, [raw])
            app._autostart_warning = ''
            app._set_health_panel([], 0)
            self.assertEqual(app.health_label.winfo_manager(), '')

    def test_critical_temperature_and_restore_errors_remain_visible(self):
        with layout_app() as app:
            app._gpu_fan_status = {'state': 'error', 'reason': 'restore unconfirmed'}
            app._update_thermal_advice(sample(gpu_hotspot_temp=110))
            self.assertIn('restore unconfirmed', app.health_label.cget('text'))
            self.assertIn('GPU Hotspot', app.health_label.cget('text'))
            self.assertEqual(app.health_label.cget('fg'), '#f87171')

    def test_readonly_autostart_retry_never_repairs_stale_task(self):
        definition = mock.Mock(enabled='true', trigger_enabled='true')
        with (mock.patch.object(overlay, '_resolve_autostart_identity', return_value=('user', [], None)),
              mock.patch.object(overlay, '_query_autostart_task_definition', return_value=(definition, None)),
              mock.patch.object(overlay, '_classify_autostart_task', return_value=overlay.AUTOSTART_STALE_HEATMAP),
              mock.patch.object(overlay, '_autostart_owner_error', return_value=None),
              mock.patch.object(overlay, 'enable_autostart') as enable):
            result = overlay.reconcile_autostart_security(read_only=True)
        self.assertFalse(result.ok)
        self.assertFalse(result.changed)
        enable.assert_not_called()
