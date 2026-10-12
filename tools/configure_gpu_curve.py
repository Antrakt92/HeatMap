"""Explicitly save a balanced driver fan curve for the exact commissioned GPU."""
import argparse
import copy
import json
import os
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from amd_gpu_fan import AmdGpuFan, supported_gpu
from case_fans import WorkerMutex, replace_status_file
from fan_common import stage_json_payload, restore_with_retry
from gpu_fans import (BALANCED_DRIVER_POINTS, GpuSession, RecoveryJournal,
                      emergency_cooling, require_hardware_access)
from startup_readiness import StartupNotReady, wait_for_readiness
from thermal_policy import finite


def save_receipt(path, payload):
    temporary = stage_json_payload(path, payload)
    try:
        replace_status_file(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def validate_ranges(adapter, points):
    for values, bounds, label in (([point[0] for point in points], adapter.temperature_range, 'temperature'),
                                 ([point[1] for point in points], adapter.speed_range, 'speed')):
        if (len(bounds) != 3 or any(type(value) is not int for value in bounds)
                or bounds[2] <= 0 or bounds[0] > bounds[1]
                or any(not bounds[0] <= value <= bounds[1] or (value - bounds[0]) % bounds[2]
                       for value in values)):
            raise RuntimeError(f'Balanced GPU curve outside driver {label} range; settings preserved')


def fresh_temperatures(adapter, guard):
    previous = None
    def probe():
        nonlocal previous
        data = adapter.readings(strict=True)
        if not isinstance(data, dict) or emergency_cooling(data):
            raise RuntimeError('GPU telemetry missing or GPU too hot for curve replacement; settings preserved')
        stamp = finite(data.get('timestamp_ms'), 0, 1e15)
        if stamp is None or (previous is not None and stamp < previous):
            raise RuntimeError('Invalid GPU profile metrics timestamp; settings preserved')
        ready = previous is not None and stamp > previous
        previous = stamp
        if not ready:
            raise StartupNotReady('Waiting for fresh GPU profile temperatures')
        return data
    return wait_for_readiness(probe, threading.Event(), guard, lambda *_: None,
                              timeout=6, retry_interval=0.1, clock=time.monotonic)


def configure(adapter, journal_path, receipt_path, *, apply=False, guard=require_hardware_access):
    if not supported_gpu(adapter.identity):
        raise RuntimeError('Balanced curve requires the exact commissioned Gigabyte RX 7900 XT')
    points = [list(point) for point in BALANCED_DRIVER_POINTS]
    validate_ranges(adapter, points)
    guard()
    report = dict(state='preview', gpu=copy.deepcopy(adapter.identity), started=time.time(),
                  requested_points=points, original=adapter.snapshot(), fan_writes=0)
    if not apply:
        return report
    fresh_temperatures(adapter, guard)
    # The same mutex and recovery journal as the normal worker prevent adopting
    # an interrupted override as the user's saved curve.
    journal = RecoveryJournal(journal_path, adapter.identity)
    report['recovered_previous_session'] = journal.recover(adapter, before_write=guard)
    session = GpuSession(adapter, journal)
    report['original'] = copy.deepcopy(session.baseline)
    report.update(state='prepared', zero_rpm=session.baseline['zero_rpm'])
    save_receipt(receipt_path, report)
    committed = False
    try:
        session.apply_points(points, zero_rpm=session.baseline['zero_rpm'],
                             before_write=lambda: fresh_temperatures(adapter, guard))
        report['readback'] = adapter.snapshot()
        if report['readback'] != session.expected:
            raise RuntimeError('Balanced GPU curve readback differs')
        session.commit()
        committed = True
        report.update(state='applied', finished=time.time(), fan_writes=1)
        save_receipt(receipt_path, report)
        try:
            journal.clear()
        except OSError as exc:
            report['cleanup_warning'] = str(exc)
            save_receipt(receipt_path, report)
        return report
    except BaseException as exc:
        committed = committed or session.committed
        report.update(state='applied' if committed else 'error', reason=str(exc) or type(exc).__name__,
                      restore_errors=[] if committed else restore_with_retry(session.restore),
                      fan_writes=1 if committed else report['fan_writes'],
                      finished=time.time())
        save_receipt(receipt_path, report)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Write and read back the curve; preserves Zero RPM')
    args = parser.parse_args(argv)
    directory = Path(os.environ['LOCALAPPDATA']) / 'HeatMap'
    receipt = directory / 'gpu-fan-profiles' / f'balanced-{time.time_ns()}.json'
    with WorkerMutex('Global\\HeatMapGpuFanControlV1'):
        adapter = None
        try:
            require_hardware_access()
            adapter = AmdGpuFan()
            report = configure(adapter, directory / 'gpu-fan-status' / 'recovery.json', receipt, apply=args.apply)
            print(json.dumps(dict(report, receipt=str(receipt) if args.apply else None), indent=2))
            return 0
        finally:
            if adapter is not None:
                adapter.close()


if __name__ == '__main__':
    raise SystemExit(main())
