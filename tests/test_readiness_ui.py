from unittest.mock import Mock

import overlay
from test_thermal_advisor import sample
from test_ui_layout import TkTestCase, layout_app


class ReadinessUiTests(TkTestCase):
    def waiting(self, **changes):
        return dict(state='checking', phase='waiting', reason='Waiting for GPU Memory',
                    remaining_seconds=59, **changes)

    def test_waiting_is_visible_before_first_sample_without_alerts(self):
        with layout_app() as app:
            app.sensor_data = {}
            app.fan_worker.poll = Mock(return_value=self.waiting(
                control_attempted=False, baseline=[], controlled_channels=[]))
            app.gpu_fan_worker = Mock()
            app.gpu_fan_worker.poll.return_value = self.waiting()
            app._check_alerts.reset_mock()
            app.update_ui()
            self.assertEqual(app.rows['case_fan_control'].cget('text'), 'Waiting sensors...')
            self.assertIn('Waiting GPU', app.rows['gpu_fan_control'].cget('text'))
            self.assertIn('Case fans: waiting for sensors.', app.health_label.cget('text'))
            self.assertIn('GPU fans: waiting for sensors.', app.health_label.cget('text'))
            self.assertEqual(overlay._case_fan_owner(app._case_fan_status, 'System Fan #4'), 'FW')
            app._check_alerts.assert_not_called()

    def test_waiting_does_not_hide_error_or_claim_pending_gpu_restore(self):
        with layout_app() as app:
            app._gpu_fan_status = self.waiting(recovery_pending=True)
            app._set_health_panel(['Case fans: restore unconfirmed'], 2)
            self.assertEqual(app.health_messages[0], 'Case fans: restore unconfirmed')
            self.assertIn('Recovery pending', app.health_messages[1])
            self.assertEqual(app.health_label.cget('fg'), '#f87171')

    def test_waiting_clears_after_ready_and_rows_fit_supported_scales(self):
        for scale in (1.333, 2.0, 2.666):
            with self.subTest(scale=scale), layout_app(scale) as app:
                app.sensor_data = sample()
                app.fan_worker.poll = Mock(return_value=self.waiting())
                app.gpu_fan_worker = Mock()
                app.gpu_fan_worker.poll.return_value = self.waiting()
                app.update_ui()
                app.root.update_idletasks()
                for key in ('case_fan_control', 'gpu_fan_control'):
                    self.assertLessEqual(app.rows[key].master.master.winfo_reqwidth(),
                                         int(app.canvas.itemcget(app._content_window, 'width')))
                app.fan_worker.poll.return_value = dict(state='off')
                app.gpu_fan_worker.poll.return_value = dict(state='off')
                app.update_ui()
                self.assertFalse(any('waiting for sensors' in text for text in app.health_messages))
