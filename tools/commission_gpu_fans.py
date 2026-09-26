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
from fan_common import verify_loop
from gpu_fans import GpuWorkerClient
from thermal_policy import finite


def _never_acquired(restored):
    return (restored.get('control_attempted') is False and restored.get('baseline') is None
            and restored.get('recovery_pending') is False and not restored.get('restore_errors'))


def verify(client, samples, duration=8):
    return verify_loop(
        client, samples, duration,
        evidence_ok=lambda status: finite(status.get('verified_full_rpm'), 2500, 10000) is not None,
        never_acquired=_never_acquired,
        stopped_message='GPU fan controller stopped',
        evidence_message='GPU full-airflow verification evidence is missing or invalid',
        timestamp_message='GPU verification timestamp is invalid or moved backward',
        timeout_message='GPU fan verification timed out',
        restore_message='GPU fan restoration not confirmed: ',
    )


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
