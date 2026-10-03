"""Bounded Windows filesystem queries, independent of native sensor ownership."""
import json
import re

import psutil

from storage_identity import volume_disk_number
from storage_probe import PROBE_TIMEOUT_SECONDS, run_bounded_probe
from thermal_policy import finite


def _query(mount):
    if mount is None:
        mounts = sorted({partition.mountpoint[:2].upper() + '\\'
                         for partition in psutil.disk_partitions(all=False)
                         if 'fixed' in partition.opts.split(',')
                         and re.fullmatch(r'[A-Za-z]:[\\/]', partition.mountpoint)})
        return {'mounts': mounts}
    usage = psutil.disk_usage(mount)
    volume = {'name': mount[:2], 'total_bytes': usage.total, 'free_bytes': usage.free}
    number = volume_disk_number(mount[:2])
    if number is not None:
        volume['disk_number'] = number
    return {'volume': volume}


def _worker(mount, pipe):
    try:
        try:
            result = _query(mount)
        except (OSError, psutil.Error, ValueError) as exc:
            result = {'error': str(exc)[:500]}
        try:
            pipe.send_bytes(json.dumps(result, allow_nan=False).encode('utf-8'))
        except OSError:
            pass  # Owner cancelled or timed out; this process owns no controls.
    finally:
        pipe.close()


def _validate(result, mount):
    if not isinstance(result, dict):
        raise ValueError('Invalid volume reply')
    if 'error' in result:
        if not isinstance(result['error'], str):
            raise ValueError('Invalid volume error')
        return {'error': result['error'][:500]}
    if mount is None:
        mounts = result.get('mounts')
        if (not isinstance(mounts, list) or len(mounts) > 26
                or any(not isinstance(item, str) or not re.fullmatch(r'[A-Z]:\\', item)
                       for item in mounts) or len(set(mounts)) != len(mounts)):
            raise ValueError('Invalid fixed-volume inventory')
        return {'mounts': mounts}
    volume = result.get('volume')
    if not isinstance(volume, dict) or volume.get('name') != mount[:2]:
        raise ValueError('Volume probe returned a different identity')
    total, free = volume.get('total_bytes'), volume.get('free_bytes')
    if (type(total) is not int or type(free) is not int
            or finite(total, 1, 2**64 - 1) is None or not 0 <= free <= total):
        raise ValueError('Invalid volume capacity')
    sample = {'name': mount[:2], 'total_bytes': total, 'free_bytes': free,
              'used_pct': 100 * (total - free) / total}
    if 'disk_number' in volume:
        number = volume['disk_number']
        if type(number) is not int or not 0 <= number <= 0x7fffffff:
            raise ValueError('Invalid volume disk identity')
        sample['disk_number'] = number
    return {'volume': sample}


def probe_volume(mount=None, cancelled=lambda: False, *, timeout=PROBE_TIMEOUT_SECONDS):
    """Enumerate fixed drive letters or read one volume in a disposable process."""
    if mount is not None and (not isinstance(mount, str) or not re.fullmatch(r'[A-Z]:\\', mount)):
        raise ValueError('Invalid fixed-volume path')
    return run_bounded_probe(
        _worker, mount, lambda result: _validate(result, mount),
        lambda error: {'error': 'Volume ' + error}, cancelled, timeout,
    )
