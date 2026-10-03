"""Read-only Windows capacity smoke and synthetic hung-process disposal check."""
import json
import multiprocessing
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psutil
from storage_probe import run_bounded_probe
from volume_probe import probe_volume


def blocked_worker(argument, pipe):
    try:
        pipe.send_bytes(json.dumps({'pid': multiprocessing.current_process().pid}).encode())
        time.sleep(60)
    finally:
        pipe.close()


def main():
    observed = []

    def consume(result):
        observed.append(result['pid'])

    started = time.monotonic()
    result = run_bounded_probe(blocked_worker, None, consume,
                               lambda error: {'error': error}, lambda: False, 2)
    elapsed = time.monotonic() - started
    assert 'timed out' in result['error'], result
    assert observed, 'Synthetic worker never reached the blocking call'
    assert not psutil.pid_exists(observed[0]), 'Probe process was not reaped'
    assert elapsed < 5, f'Unbounded disposal: {elapsed:.2f}s'
    print(f'Synthetic blocked probe disposed in {elapsed:.2f}s; no surviving child')
    inventory = probe_volume()
    assert 'mounts' in inventory, inventory
    for mount in inventory['mounts']:
        sample = probe_volume(mount)
        assert 'volume' in sample, sample
        volume = sample['volume']
        assert 0 <= volume['free_bytes'] <= volume['total_bytes']
        print(f"{volume['name']} capacity OK; physical mapping={'disk_number' in volume}")
    print(f"Read-only fixed-volume smoke OK: {len(inventory['mounts'])} volumes")


if __name__ == '__main__':
    multiprocessing.freeze_support()
    main()
