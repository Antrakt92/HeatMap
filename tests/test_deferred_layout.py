"""Regular refreshes must yield to the Windows event loop while laying out."""
from unittest import mock

import overlay
from test_ui_layout import TkTestCase, layout_app


class DeferredLayoutTests(TkTestCase):
    def test_runtime_layout_never_drains_idle_queue_and_coalesces_requests(self):
        with layout_app() as app:
            app._ui_refresh_in_progress = True
            with mock.patch.object(app.root, 'update_idletasks', side_effect=AssertionError('idle drain')):
                app._fit_content()
                first = app._layout_after_id
                app._fit_content()
                self.assertEqual(app._layout_after_id, first)
                app._ui_refresh_in_progress = False
                app.root.after(200, app.root.quit)
                app.root.mainloop()
            self.assertIsNone(app._layout_after_id)
            self.assertIsNone(app._layout_iterator)

    def test_hwnd_lookup_is_cached_without_repeated_layout_flushes(self):
        with layout_app() as app:
            hwnd = app._get_hwnd()
            with mock.patch.object(app, '_flush_layout', side_effect=AssertionError('idle drain')):
                self.assertEqual(app._get_hwnd(), hwnd)
                self.assertEqual(app._get_hwnd(), hwnd)

    def test_deferred_growth_clamps_window_inside_work_area(self):
        with layout_app(height=720) as app:
            app.config.update(x=1200, y=680)
            app.root.geometry('+1200+680')
            app._set_health_panel(['A long cooling warning. ' * 24], 2)
            app._ui_refresh_in_progress = True
            app._fit_content()
            app._ui_refresh_in_progress = False
            app.root.after(250, app.root.quit)
            app.root.mainloop()
            self.assertLessEqual(app.config['x'] + app.root.winfo_width(), 1280)
            self.assertLessEqual(app.config['y'] + app.root.winfo_height(), 720)

    def test_shutdown_cancels_deferred_steps_without_renewing_owners(self):
        with layout_app() as app:
            app._ui_refresh_in_progress = True
            app._fit_content()
            app.running = False
            with mock.patch.object(app, '_service_fan_heartbeats') as heartbeat:
                app._advance_layout()
            heartbeat.assert_not_called()
            self.assertIsNone(app._layout_iterator)
