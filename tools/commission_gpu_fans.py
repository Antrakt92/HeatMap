"""Verify GPU airflow/restore; optionally enable it and restart HeatMap normally."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import overlay
from enable_case_fans import close_previous_overlay
from gpu_fans import GpuWorkerClient


def verify(client, samples, duration=8):
    client.start()
    active_since = None
    last_report = None
    failure = None
    try:
        deadline = time.monotonic() + 65
        while time.monotonic() < deadline:
            status = client.poll()
            if status['state'] in ('error', 'stopped', 'off'):
                raise RuntimeError(status.get('reason', 'GPU fan controller stopped'))
            if status['state'] == 'active':
                stamp = status.get('time')
                if type(stamp) not in (int, float) or not 0 < stamp < float('inf'):
                    raise RuntimeError('GPU verification report has no valid timestamp')
                if last_report is None or stamp > last_report:
                    samples.append(status)
                    last_report = stamp
                    if active_since is None:
                        active_since = time.monotonic()
                    elif time.monotonic() - active_since >= duration:
                        break
            else:
                active_since = None
            time.sleep(2)
        else:
            raise RuntimeError('GPU fan verification timed out')
    except Exception as exc:
        failure = exc
    finally:
        client.stop()
        if client.process is not None:
            client.process.wait(timeout=20)
    restored = client.poll()
    if not restored.get('restore_confirmed') or restored.get('restore_errors'):
        raise RuntimeError('GPU fan restoration not confirmed: ' + str(restored))
    if failure:
        raise failure
    return restored


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enable', action='store_true')
    args = parser.parse_args()
    directory = Path(os.environ['LOCALAPPDATA']) / 'HeatMap' / 'gpu-control-2026-09-06'
    directory.mkdir(parents=True, exist_ok=True)
    report_path = directory / f'commission-{time.time_ns()}.json'
    report = dict(state='checking', started=time.time(), samples=[])
    client = None
    closed = False
    try:
        if args.enable:
            if not overlay._is_admin():
                raise RuntimeError('Restarting the elevated overlay requires administrator access; no automatic elevation')
            close_previous_overlay()
            closed = True
        config, error = overlay.load_config_result()
        if error:
            raise RuntimeError(error)
        if args.enable:
            backup = directory / f'config-before-{time.time_ns()}.json'
            backup.write_text(json.dumps(config, indent=2), encoding='utf-8')
            report['config_backup'] = str(backup)
        client = GpuWorkerClient(str(ROOT))
        report['restore'] = verify(client, report['samples'])
        report['state'] = 'commissioned_and_restored'
        if args.enable:
            config['gpu_fans_enabled'] = True
            saved, message = overlay.save_config(config)
            if not saved:
                raise RuntimeError(message)
            report['state'] = 'enabled_after_verified_restore'
    except Exception as exc:
        report.update(state='error', reason=str(exc))
    finally:
        if client is not None:
            report['status_path'] = client.status_path
            report['terminal'] = client.poll()
            if not report['terminal'].get('restore_confirmed'):
                closed = False
        if closed:
            process = subprocess.Popen([str(ROOT / '.venv/Scripts/pythonw.exe'), str(ROOT / 'overlay.py')],
                                       cwd=str(ROOT), creationflags=subprocess.CREATE_NO_WINDOW)
            report['overlay_restarted_pid'] = process.pid
        report['finished'] = time.time()
        report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(str(report_path))
    return 1 if report['state'] == 'error' else 0


if __name__ == '__main__':
    raise SystemExit(main())
