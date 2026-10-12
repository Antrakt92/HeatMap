"""Check notice timer servicing using only withdrawn, disposable Tk windows.

No sensors, fan workers, native MessageBox, clipboard or user windows are used.
"""
import json
from pathlib import Path
import sys
import threading
import time
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import overlay
from fan_common import OwnerHeartbeat


def main():
    root = overlay.tk.Tk()
    root.withdraw()
    app = overlay.OverlayApp.__new__(overlay.OverlayApp)
    app.root, app.running = root, True
    owned = []
    errors = []
    results = []
    native_toplevel = overlay.tk.Toplevel

    def hidden_notice(*args, **kwargs):
        dialog = native_toplevel(*args, **kwargs)
        dialog.withdraw()
        owned.append(dialog)
        return dialog

    def callback_failed(_kind, error, _traceback):
        errors.append(str(error))
        root.quit()

    root.report_callback_exception = callback_failed
    try:
        with (mock.patch.object(overlay.tk, 'Toplevel', side_effect=hidden_notice),
              mock.patch.object(overlay, '_show_error_message', side_effect=AssertionError('Native modal used')),
              mock.patch.object(overlay, '_show_info_message', side_effect=AssertionError('Native modal used'))):
            for error in (False, True):
                message = '\n'.join(f'Notice detail {index}' for index in range(80))
                begin = time.monotonic()
                dialog = app._show_notice('HeatMap disposable notice check', message, error=error)
                assert dialog is owned[-1] and dialog.master is root
                assert root.grab_current() is None, 'Notice acquired a modal grab'
                ticks = []
                heartbeat = OwnerHeartbeat(threading.Event())

                def pulse():
                    assert dialog.winfo_exists(), 'Notice closed before the timer check'
                    heartbeat.last_seen = time.monotonic()
                    ticks.append(heartbeat.last_seen)
                    root.after(100, pulse)

                root.after(50, pulse)
                root.after(1250, root.quit)
                root.mainloop()
                assert not errors, f'Tk callback failed: {errors}'
                assert time.monotonic() - begin >= 1.2, 'Check ended before the notice wait'
                assert len(ticks) >= 8, f'Notice starved Tk callbacks: {len(ticks)} ticks'
                assert not heartbeat.expired(), 'Notice starved the owner heartbeat'
                assert dialog.winfo_exists(), 'Open notice was not retained'
                text_widgets = [widget for frame in dialog.winfo_children()
                                for widget in frame.winfo_children()
                                if isinstance(widget, overlay.tk.Text)]
                assert len(text_widgets) == 1 and text_widgets[0].get('1.0', 'end-1c') == message
                assert text_widgets[0].cget('state') == 'disabled'
                results.append({'error': error, 'ticks_while_open': len(ticks),
                                'elapsed_seconds': round(time.monotonic() - begin, 3)})
                for callback in list(root.tk.call('after', 'info') or ()):
                    root.after_cancel(callback)
                dialog.destroy()
        print(json.dumps({'notice_timer_checks': results}))
    finally:
        app.running = False
        for dialog in reversed(owned):
            if dialog.winfo_exists():
                dialog.destroy()
        for callback in list(root.tk.call('after', 'info') or ()):
            root.after_cancel(callback)
        root.destroy()


if __name__ == '__main__':
    main()
