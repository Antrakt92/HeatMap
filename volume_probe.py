"""Bounded Windows filesystem queries, independent of native sensor ownership."""
import ctypes
from ctypes import wintypes
import re

import psutil

from storage_identity import volume_disk_number
from storage_probe import (PROBE_TIMEOUT_SECONDS, run_bounded_probe,
                           _reply_sample_time, _send_probe_message)
from thermal_policy import finite


_kernel = ctypes.WinDLL('kernel32', use_last_error=True)
_kernel.GetLogicalDrives.argtypes = []
_kernel.GetLogicalDrives.restype = wintypes.DWORD
_kernel.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
_kernel.GetDriveTypeW.restype = wintypes.UINT


def _fixed_mounts():
    # WHY: disk_partitions reads filesystem metadata for every drive, including
    # remote/unresponsive drives, before returning even the healthy local ones.
    # Enumerate only letters/types here; isolate all filesystem I/O per volume.
    mask = _kernel.GetLogicalDrives()
    if not mask:
        raise ctypes.WinError(ctypes.get_last_error())
    mounts = [chr(ord('A') + index) + ':\\' for index in range(26) if mask & (1 << index)]
    return [mount for mount in mounts if _kernel.GetDriveTypeW(mount) == 3]  # DRIVE_FIXED


def _query(mount, capacity=lambda result: None, *, include_identity=True):
    if mount is None:
        return {'mounts': _fixed_mounts()}
    usage = psutil.disk_usage(mount)
    volume = {'name': mount[:2], 'total_bytes': usage.total, 'free_bytes': usage.free}
    # Capacity is independent of physical-disk identity. Retain this reading
    # even when the following extent query blocks or is unsupported.
    capacity({'phase': 'capacity', 'volume': dict(volume)})
    number = volume_disk_number(mount[:2]) if include_identity else None
    if number is not None:
        volume['disk_number'] = number
    return {'volume': volume}


def _worker(mount, pipe):
    mount, include_identity = mount if isinstance(mount, tuple) else (mount, True)

    def capacity(result):
        _send_probe_message(pipe, result)

    try:
        try:
            result = _query(mount, capacity, include_identity=include_identity)
        except (OSError, psutil.Error, ValueError) as exc:
            result = {'error': str(exc)[:500]}
        try:
            _send_probe_message(pipe, result)
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


def probe_volume(mount=None, cancelled=lambda: False, *, timeout=PROBE_TIMEOUT_SECONDS,
                 include_identity=True):
    """Bound volume queries while retaining capacity if only mapping fails.

    Capacity includes its own sample_time; identity_error never removes it.
    A capacity-only retry omits mapping and leaves warning/backoff to its owner.
    """
    if mount is not None and (not isinstance(mount, str) or not re.fullmatch(r'[A-Z]:\\', mount)):
        raise ValueError('Invalid fixed-volume path')
    if type(include_identity) is not bool:
        raise ValueError('Invalid volume identity option')
    progress = {}

    def consume(result):
        sample = _validate(result, mount)
        if result.get('phase') == 'capacity':
            if mount is None or 'error' in sample or 'disk_number' in sample['volume']:
                raise ValueError('Invalid volume capacity progress')
            if progress:
                raise ValueError('Duplicate volume capacity progress')
            progress.update(sample, sample_time=_reply_sample_time(result))
            return None
        if 'error' in sample:
            return failure(sample['error'])
        if progress:
            if any(sample['volume'][key] != progress['volume'][key]
                   for key in ('name', 'total_bytes', 'free_bytes')):
                raise ValueError('Volume capacity changed within the reply')
            sample['sample_time'] = progress['sample_time']
        elif "sample_time" in result:
            sample["sample_time"] = _reply_sample_time(result)
        return sample

    def failure(error):
        if progress:
            return {**progress, 'identity_error': 'Physical disk mapping unavailable: ' + error}
        return {'error': 'Volume ' + error}

    return run_bounded_probe(
        _worker, mount if include_identity else (mount, False), consume, failure, cancelled, timeout,
    )
