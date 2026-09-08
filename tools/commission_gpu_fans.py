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
from startup_readiness import STARTUP_TIMEOUT_SECONDS
from thermal_policy import finite


def verify(client, samples, duration=8):
    if finite(duration, 0, 3600) is None:
        raise ValueError('Verification duration must be finite and between 0 and 3600 seconds')
    client.start()
    active_since = None
    first_report = None
    last_report = None
    failure = None
    try:
        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS + 30 + duration
        while time.monotonic() < deadline:
            status = client.poll()
            if status['state'] in ('error', 'stopped', 'off'):
                raise RuntimeError(status.get('reason', 'GPU fan controller stopped'))
            if status['state'] == 'active':
                if finite(status.get('verified_full_rpm'), 2500, 10000) is None:
                    raise RuntimeError('GPU full-airflow verification evidence is missing or invalid')
                stamp = finite(status.get('time'), 0, 1e12)
                if stamp is None or (last_report is not None and stamp < last_report):
                    raise RuntimeError('GPU verification timestamp is invalid or moved backward')
                if last_report is None or stamp > last_report:
                    samples.append(status)
                    last_report = stamp
                    if active_since is None:
                        active_since, first_report = time.monotonic(), stamp
                    if (time.monotonic() - active_since >= duration
                            and stamp - first_report >= duration):
                        break
            else:
                active_since = first_report = None
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
    if (failure and restored.get('control_attempted') is False
            and restored.get('baseline') is None and restored.get('recovery_pending') is False
            and not restored.get('restore_errors')):
        raise failure
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
        client = GpuWorkerClient(str(ROOT), commission=True)
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
            terminal = report['terminal']
            if terminal.get('restore_confirmed') is not True or terminal.get('restore_errors') != []:
                closed = False
                report.setdefault('reason', 'GPU fan restoration not confirmed by final status')
                report['state'] = 'error'
        if closed:
            try:
                process = subprocess.Popen([str(Path(sys.executable).with_name('pythonw.exe')), str(ROOT / 'overlay.py')],
                                           cwd=str(ROOT), creationflags=subprocess.CREATE_NO_WINDOW)
                report['overlay_restarted_pid'] = process.pid
            except OSError as exc:
                report['restart_error'] = str(exc)
                report.setdefault('reason', 'HeatMap restart failed: ' + str(exc))
                report['state'] = 'error'
        report['finished'] = time.time()
        report_path.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(str(report_path))
    return 1 if report['state'] == 'error' else 0


if __name__ == '__main__':
    raise SystemExit(main())
