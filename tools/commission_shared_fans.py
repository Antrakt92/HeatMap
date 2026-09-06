"""Explicit elevated commissioning; enable four-fan control only after restore proof."""
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
from case_fans import FanWorkerClient, open_status_file
from enable_case_fans import close_previous_overlay, verify_worker
from hardware_access_guard import require_hardware_access
from pawnio_shared import verified_module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--enable', action='store_true', help='Save the profile and restart HeatMap after commissioning')
    args = parser.parse_args()
    directory = Path(os.environ['LOCALAPPDATA']) / 'HeatMap' / 'shared-controller-2026-09-06'
    directory.mkdir(parents=True, exist_ok=True)
    report_path = directory / f'commission-{time.time_ns()}.json'
    result = dict(started=time.time(), state='checking', samples=[])
    closed = False
    client = None
    def save():
        report_path.write_text(json.dumps(result, indent=2), encoding='utf-8')
    save()
    try:
        if not overlay._is_admin():
            raise RuntimeError('Administrator access is required; this tool never elevates itself')
        verified_module()
        require_hardware_access()
        config, error = overlay.load_config_result()
        if error:
            raise RuntimeError(error)
        close_previous_overlay()
        closed = True
        backup = directory / f'config-before-{time.time_ns()}.json'
        backup.write_text(json.dumps(config, indent=2), encoding='utf-8')
        result['config_backup'] = str(backup)
        client = FanWorkerClient(str(ROOT), config.get('case_fan_full_rpm'), shared=True, commission=True)
        result['restore'] = verify_worker(client, result['samples'], duration=6)
        result['state'] = 'commissioned_and_restored'
        if args.enable:
            config['case_fans_shared_enabled'] = True
            config['case_fans_enabled'] = True
            config['case_fan_full_rpm'] = result['samples'][-1]['verified_full_rpm']
            # Use the same atomic writer as the application; never touch BIOS.
            saved, message = overlay.save_config(config)
            if not saved:
                raise RuntimeError(message)
            result['state'] = 'enabled_after_verified_restore'
    except Exception as exc:
        result.update(state='error', reason=str(exc))
    finally:
        if client is not None:
            result['worker_status_path'] = client.status_path
            terminal = client.poll()
            result['terminal'] = terminal
            if not terminal.get('restore_confirmed') and terminal.get('control_attempted') is not False:
                # Do not start another hardware owner on top of uncertain restoration.
                closed = False
        if closed:
            try:
                require_hardware_access()
                process = subprocess.Popen([str(ROOT / '.venv/Scripts/pythonw.exe'), str(ROOT / 'overlay.py')],
                    cwd=str(ROOT), creationflags=subprocess.CREATE_NO_WINDOW)
                result['overlay_restarted_pid'] = process.pid
            except Exception as exc:
                result['restart_error'] = str(exc)
        result['finished'] = time.time()
        save()
    return 1 if result['state'] == 'error' else 0


if __name__ == '__main__':
    raise SystemExit(main())
