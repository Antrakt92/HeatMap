"""HeatMap GPU fan policy, isolated owner, heartbeat and verified rollback."""
import argparse
import copy
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

import psutil

from case_fans import FanWorkerClient, WorkerMutex, open_status_file, replace_status_file, write_status
from hardware_access_guard import require_hardware_access
from thermal_policy import finite, interpolate
from startup_readiness import StartupCancelled, StartupNotReady, wait_for_readiness

PROFILE = 'gigabyte-rx7900xt-hotspot90'
CURVES = {
    'gpu_core_temp': ((40, 30), (50, 45), (60, 65), (70, 85), (75, 100)),
    'gpu_hotspot_temp': ((50, 30), (60, 45), (75, 70), (85, 90), (90, 100)),
    'gpu_memory_temp': ((55, 30), (65, 45), (75, 65), (85, 85), (90, 100)),
}
LABELS = {'gpu_core_temp': 'Core', 'gpu_hotspot_temp': 'Hotspot', 'gpu_memory_temp': 'Memory'}
# Application intervention thresholds, not hardware temperature limits.
ASSIST_ON = {'gpu_core_temp': 70, 'gpu_hotspot_temp': 85, 'gpu_memory_temp': 85}
ASSIST_OFF = {'gpu_core_temp': 60, 'gpu_hotspot_temp': 75, 'gpu_memory_temp': 75}
RELEASE_HOLD_SECONDS = 10


class GpuAssistPolicy:
    """Let the saved driver curve own cool GPUs, including normal Zero RPM."""
    def __init__(self):
        self.active = False
        self.cool_since = None

    def update(self, data, now):
        valid = all(finite(data.get(key), 1, 150) is not None for key in CURVES)
        if not self.active:
            self.active = valid and any(data[key] >= limit for key, limit in ASSIST_ON.items())
        elif valid and all(data[key] <= limit for key, limit in ASSIST_OFF.items()):
            if self.cool_since is None or now < self.cool_since:
                self.cool_since = now
            if now - self.cool_since >= RELEASE_HOLD_SECONDS:
                self.active = False
        else:
            self.cool_since = None
        if not self.active:
            self.cool_since = None
        return self.active


def demand(data):
    missing = [LABELS[key] for key in CURVES if finite(data.get(key), 1, 150) is None]
    if missing:
        return 100, 'Missing ' + '/'.join(missing)
    demands = {key: math.ceil(interpolate(data[key], points)) for key, points in CURVES.items()}
    key = max(demands, key=demands.__getitem__)
    return demands[key], LABELS[key]


class GpuRamp:
    def __init__(self, initial=None):
        self.command = initial
        self.last_time = None
        self.cool_since = None

    def update(self, target, now):
        if finite(target, 30, 100) is None or finite(now, 0, 1e15) is None:
            self.command, self.cool_since = 100, None
            return 100
        if self.command is None:
            self.command = target
        if self.last_time is not None and now < self.last_time:
            self.command, self.cool_since = 100, None
        elapsed = 0 if self.last_time is None else min(2, now - self.last_time)
        self.last_time = now
        if target >= self.command:
            self.command, self.cool_since = target, None
        else:
            if self.cool_since is None:
                self.cool_since = now
            if now - self.cool_since >= 10:
                eligible_elapsed = min(elapsed, now - (self.cool_since + 10))
                self.command = max(target, self.command - 2 * eligible_elapsed)
        return math.ceil(self.command)


def cooling_points(percent, baseline_points=None):
    if type(percent) is not int or not 30 <= percent <= 100:
        raise ValueError('GPU fan command must be an integer within 30..100')
    if baseline_points is not None:
        if not valid_snapshot({'points': baseline_points, 'zero_rpm': None}):
            raise ValueError('Invalid saved GPU fan curve')
        # Preserve the original temperature knots: taking the maximum speed at
        # each knot guarantees the interpolated curve never reduces driver duty.
        points = [[temperature, max(percent, speed)] for temperature, speed in baseline_points]
        # With only five points, a new 90C knot could discard an original peak.
        # Conservatively reach full speed at the preceding original knot instead.
        full_index = max((index for index, (temperature, _) in enumerate(points) if temperature <= 90), default=0)
        for point in points[full_index:]:
            point[1] = 100
        return points
    # Retain an independent hardware heat-response even if the worker is killed.
    # The software chooses a floor; the driver may demand more on its own sensor.
    return [[25, percent], [40, percent], [55, percent], [75, max(percent, 85)], [90, 100]]


def valid_snapshot(value):
    if not isinstance(value, dict) or set(value) != {'points', 'zero_rpm'}:
        return False
    points = value['points']
    return (value['zero_rpm'] is None or type(value['zero_rpm']) is bool) and (
        isinstance(points, list) and len(points) == 5 and all(
            isinstance(point, list) and len(point) == 2 and
            type(point[0]) is int and 0 <= point[0] <= 150 and
            type(point[1]) is int and 0 <= point[1] <= 100 for point in points)
        and all(left[0] <= right[0] for left, right in zip(points, points[1:])))


class RecoveryJournal:
    """Recover only exact states that this owner could have written to this GPU."""
    def __init__(self, path, identity):
        self.path = Path(path)
        self.identity = copy.deepcopy(identity)

    def before_write(self, baseline, before, after):
        if not all(valid_snapshot(state) for state in (baseline, before, after)):
            raise RuntimeError('Invalid GPU recovery snapshot; no new command sent')
        # Curve and Zero RPM are separate native calls. Include their partial
        # write/restore combinations, retaining the original curve across updates.
        states = []
        for curve in (baseline, before, after):
            for zero in (baseline, before, after):
                state = {'points': curve['points'], 'zero_rpm': zero['zero_rpm']}
                if state not in states:
                    states.append(state)
        payload = dict(profile=PROFILE, version=1, gpu=self.identity,
                       baseline=baseline, known_states=states)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            with temporary.open('w', encoding='utf-8') as stream:
                json.dump(payload, stream)
                stream.flush()
                os.fsync(stream.fileno())
            replace_status_file(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def clear(self):
        self.path.unlink(missing_ok=True)

    def recover(self, adapter, *, accept_external=False, before_write=None, check_cancelled=None):
        try:
            with open_status_file(self.path) as stream:
                saved = json.loads(stream.read(65536))
        except FileNotFoundError:
            return False
        except (OSError, ValueError) as exc:
            raise RuntimeError(f'GPU recovery journal unreadable; settings preserved: {exc}') from exc
        if (not isinstance(saved, dict) or saved.get('profile') != PROFILE or
                type(saved.get('version')) is not int or saved['version'] != 1 or
                saved.get('gpu') != self.identity or not valid_snapshot(saved.get('baseline')) or
                not isinstance(saved.get('known_states'), list) or
                not 1 <= len(saved['known_states']) <= 9 or
                not all(valid_snapshot(state) for state in saved['known_states'])):
            raise RuntimeError('GPU recovery journal identity or settings invalid; settings preserved')
        current = adapter.snapshot()
        if current == saved['baseline']:
            self.clear()
            return False
        if current not in saved['known_states']:
            if accept_external is not True:
                raise RuntimeError('GPU settings changed since interrupted HeatMap session; settings preserved. '
                                   'Close competing fan tools, then toggle GPU fans OFF/ON to accept current settings')
            # Explicit re-enable only, under the worker mutex. Keep the old journal
            # as evidence; never restore our obsolete baseline over an external edit.
            if not valid_snapshot(current) or adapter.snapshot() != current:
                raise RuntimeError('Current GPU fan settings invalid or changing; settings preserved')
            if check_cancelled is not None:
                check_cancelled()
            archive = self.path.with_name('recovery.conflict-' + uuid.uuid4().hex + '.json')
            self.path.rename(archive)
            return False
        session = GpuSession(adapter, self)
        session.baseline = saved['baseline']
        session.known_states = copy.deepcopy(saved['known_states'])
        session.touched = True
        errors = session.restore(before_write=before_write, check_cancelled=check_cancelled)
        if errors:
            raise RuntimeError('Interrupted GPU session restoration unconfirmed: ' + '; '.join(errors))
        return True


class GpuSession:
    def __init__(self, adapter, journal=None):
        self.adapter = adapter
        self.journal = journal
        self.baseline = copy.deepcopy(adapter.snapshot())
        if not valid_snapshot(self.baseline):
            raise RuntimeError('Invalid original GPU fan settings')
        self.expected = copy.deepcopy(self.baseline)
        self.touched = False
        self.command = None
        self.ownership_lost = False
        self.conflict = None
        self.known_states = [copy.deepcopy(self.baseline)]

    def check(self, external=True):
        current = self.adapter.snapshot()
        if current != self.expected:
            self.conflict = copy.deepcopy({'expected': self.expected, 'observed': current})
            if external:
                self.ownership_lost = True
                raise RuntimeError('GPU fan settings changed outside HeatMap; close competing fan tools, '
                                   'then toggle GPU fans OFF/ON to accept current settings')
            raise RuntimeError('GPU fan command readback differs')

    def apply(self, percent, before_write=None, check_cancelled=None):
        points = cooling_points(percent, self.baseline['points'])
        self.check()
        expected = {'points': points, 'zero_rpm': False if self.baseline['zero_rpm'] is not None else None}
        if self.journal is not None:
            self.journal.before_write(self.baseline, self.expected, expected)
        if before_write is not None:
            before_write()
        if self.journal is not None or before_write is not None:
            # Filesystem flush and process inspection can take seconds. Recheck
            # ownership even when the caller needs no extra process guard.
            self.check()
        if check_cancelled is not None:
            check_cancelled()
        # Both setters may fail after writing; permit only our known partial states
        # during rollback, so an external change immediately before stop survives.
        self.known_states = [
            {'points': copy.deepcopy(curve['points']), 'zero_rpm': zero['zero_rpm']}
            for curve in (self.baseline, self.expected, expected)
            for zero in (self.baseline, self.expected, expected)
        ]
        self.touched = True  # A failed native call may already have changed hardware.
        self.adapter.set_points(points)
        if self.baseline['zero_rpm'] is not None:
            self.adapter.set_zero_rpm(False)
        self.expected = expected
        self.check(external=False)
        self.command = percent

    def restore(self, *, before_write=None, check_cancelled=None):
        if not self.touched:
            return []
        if self.ownership_lost:
            return ['External GPU fan settings preserved; original settings not restored']
        if before_write is not None:
            before_write()
        try:
            current = self.adapter.snapshot()
            if current not in self.known_states:
                self.ownership_lost = True
                self.conflict = copy.deepcopy({'expected': self.expected, 'observed': current})
                return ['External GPU fan settings preserved; original settings not restored']
        except Exception as exc:
            return [f'Cannot confirm GPU fan ownership before restore: {exc}']
        if check_cancelled is not None:
            check_cancelled()
        errors = []
        for label, action in (
            ('curve', lambda: self.adapter.set_points(self.baseline['points'])),
            ('Zero RPM', lambda: self.adapter.set_zero_rpm(self.baseline['zero_rpm'])
             if self.baseline['zero_rpm'] is not None else None),
        ):
            try:
                action()
            except Exception as exc:
                errors.append(f'{label}: {exc}')
        try:
            if self.adapter.snapshot() != self.baseline:
                errors.append('Original GPU fan settings readback differs')
        except Exception as exc:
            errors.append(f'Restore readback: {exc}')
        if not errors:
            try:
                if self.journal is not None:
                    self.journal.clear()
                self.touched = False
                self.expected = copy.deepcopy(self.baseline)
                self.command = None
            except OSError as exc:
                errors.append(f'GPU recovery journal cleanup: {exc}')
        return errors


def status_error(reason):
    return {'state': 'error', 'reason': reason, 'restore_confirmed': False}


def mode_text(status):
    state = status.get('state', 'off')
    if state == 'checking' and status.get('phase') == 'waiting':
        remaining = finite(status.get('remaining_seconds'), 0, 60)
        return f'Waiting GPU {math.ceil(remaining)}s' if remaining is not None else 'Waiting GPU...'
    if state == 'active':
        reason = status.get('reason', '')
        reason = reason if reason in LABELS.values() else 'Failsafe'
        return f"AUTO {status['command_pct']}%+ · {reason}"
    if state == 'standby':
        return ('AUTO ready · Driver curve' if status.get('thermal_ready', True) else
                'AUTO waiting · Driver curve')
    return {'checking': 'Checking...', 'error': 'ERROR'}.get(state, 'Driver curve')


class GpuWorkerClient(FanWorkerClient):
    """Reuse the existing non-killing stop protocol, with an independent GPU owner."""
    def __init__(self, app_dir, *, commission=False):
        super().__init__(app_dir)
        self.commission = commission is True

    def start(self, *, accept_external=False):
        if self.process is not None and self.process.poll() is None:
            return
        self.started = time.time()
        self.error = None
        self.last_status = None
        self.worker_pid = None
        directory = Path(os.environ.get('LOCALAPPDATA', self.app_dir)) / 'HeatMap' / 'gpu-fan-status'
        try:
            directory.mkdir(parents=True, exist_ok=True)
            self.status_path = str(directory / (uuid.uuid4().hex + '.json'))
            self.process = subprocess.Popen(
                [sys.executable, str(Path(self.app_dir) / 'gpu_fans.py'), '--status', self.status_path,
                 '--owner-pid', str(os.getpid()), '--owner-created', str(psutil.Process().create_time())]
                + (['--commission'] if self.commission else [])
                + (['--accept-external'] if accept_external is True else []),
                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                text=True, bufsize=1, creationflags=subprocess.CREATE_NO_WINDOW, cwd=self.app_dir)
        except (OSError, psutil.Error) as exc:
            self.error = str(exc) or type(exc).__name__

    def poll(self):
        if self.error:
            return status_error(self.error)
        if self.process is None:
            return {'state': 'off'}
        exited = self.process.poll() is not None
        if not exited:
            try:
                self.process.stdin.write('alive\n')
                self.process.stdin.flush()
            except (OSError, ValueError):
                pass
        try:
            try:
                with open_status_file(self.status_path) as stream:
                    status = json.loads(stream.read(65536))
            except (OSError, ValueError):
                if self.last_status is None:
                    if not exited and time.time() - self.started < 10:
                        return {'state': 'checking', 'reason': 'Opening AMD fan interface'}
                    raise
                status = self.last_status
            if not isinstance(status, dict) or status.get('profile') != PROFILE:
                raise ValueError('Invalid GPU fan status')
            state, stamp, pid = status.get('state'), finite(status.get('time'), 0, 1e12), status.get('pid')
            terminal = state in ('error', 'stopped')
            if (state not in ('checking', 'standby', 'active', 'error', 'stopped') or stamp is None or
                    type(pid) is not int or pid <= 0):
                raise ValueError('Invalid GPU fan status fields')
            if 'reason' in status and not isinstance(status['reason'], str):
                raise ValueError('Invalid GPU fan status reason')
            if ('restore_confirmed' in status and type(status['restore_confirmed']) is not bool or
                    'restore_errors' in status and (not isinstance(status['restore_errors'], list) or
                    any(not isinstance(item, str) for item in status['restore_errors']))):
                raise ValueError('Invalid GPU restoration report')
            if stamp < self.started - 2 or stamp > time.time() + 2:
                raise ValueError('GPU fan status belongs to another launch')
            if pid not in (self.process.pid, self.worker_pid):
                try:
                    if not any(parent.pid == self.process.pid for parent in psutil.Process(pid).parents()):
                        raise ValueError('Unexpected GPU fan owner')
                    self.worker_pid = pid
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    if not (exited and terminal):
                        raise ValueError('Cannot confirm GPU fan owner')
            if not (exited and terminal) and time.time() - stamp > 10:
                raise ValueError('GPU fan status is stale; restoration unconfirmed')
            if exited and not terminal:
                raise ValueError('GPU fan worker exited; restoration unconfirmed')
            if state == 'active' and finite(status.get('command_pct'), 30, 100) is None:
                raise ValueError('Invalid GPU fan command report')
            if state == 'standby' and type(status.get('thermal_ready')) is not bool:
                raise ValueError('Invalid GPU fan standby report')
            self.last_status = status
            return status
        except (OSError, ValueError) as exc:
            return status_error(str(exc))


class OwnerHeartbeat:
    def __init__(self, stop):
        self.stop = stop
        self.last_seen = time.monotonic()

    def expired(self):
        return time.monotonic() - self.last_seen > 15

    def listen(self):
        try:
            for line in sys.stdin:
                if line.strip() == 'stop':
                    break
                if line.strip() == 'alive':
                    self.last_seen = time.monotonic()
        finally:
            self.stop.set()


def recovery_journal_exists(path):
    # Path.exists() suppresses access errors on newer Python versions. Unknown
    # journal state must not become evidence that no prior owner needs recovery.
    try:
        path.stat()
    except FileNotFoundError:
        return False
    return True


def worker(path, owner_pid, owner_created, *, commission=False, accept_external=False):
    from amd_gpu_fan import AmdGpuFan
    stop = threading.Event()
    heartbeat = OwnerHeartbeat(stop)
    threading.Thread(target=heartbeat.listen, daemon=True).start()
    adapter = session = None
    error = None
    restore_errors = []
    baseline_readings = None
    recovery_pending = None
    recovered = False
    control_attempted = False
    startup_cancelled = False
    journal_path = Path(path).parent / 'recovery.json'

    def publish(state, **details):
        write_status(path, state, profile=PROFILE, **details)

    try:
        recovery_pending = recovery_journal_exists(journal_path)
        owner = psutil.Process(owner_pid)
        if abs(owner.create_time() - owner_created) > 0.01:
            raise RuntimeError('GPU controller owner process changed')

        def check_owner():
            if stop.is_set() or not owner.is_running():
                raise StartupCancelled('GPU owner stopped before takeover')
            if heartbeat.expired():
                raise RuntimeError('GPU owner heartbeat expired before takeover')

        def check_startup():
            check_owner()
            require_hardware_access()
            check_owner()

        last_ready_stamp = None
        last_observed_stamp = None

        def probe():
            nonlocal adapter, baseline_readings, last_ready_stamp, last_observed_stamp
            if adapter is None:
                adapter = AmdGpuFan()
            data = adapter.readings(strict=True)
            if data is None:
                last_ready_stamp = None
                raise StartupNotReady('Waiting for AMD GPU metrics')
            if not isinstance(data, dict):
                raise RuntimeError('Invalid AMD GPU metrics response')
            missing = [LABELS[key] for key in CURVES if data.get(key) is None]
            invalid = [LABELS[key] for key in CURVES if data.get(key) is not None and
                       finite(data[key], 1, 150) is None]
            if invalid:
                raise RuntimeError('Invalid GPU temperature: ' + '/'.join(invalid))
            raw_stamp = data.get('timestamp_ms')
            stamp = finite(raw_stamp, 0, 1e15)
            if raw_stamp is not None and stamp is None:
                raise RuntimeError('Invalid GPU metrics timestamp')
            if stamp is not None:
                if last_observed_stamp is not None and stamp < last_observed_stamp:
                    raise RuntimeError('GPU metrics timestamp moved backwards')
                last_observed_stamp = stamp
            if missing or stamp is None:
                last_ready_stamp = None
                raise StartupNotReady('Waiting for GPU ' + ('/'.join(missing) if missing else 'timestamp'))
            previous, last_ready_stamp = last_ready_stamp, stamp
            if previous is None or stamp == previous:
                raise StartupNotReady('Waiting for fresh GPU metrics')
            baseline_readings = data
            return True

        def waiting(reason, elapsed, remaining, attempt):
            publish('checking', phase='waiting', reason=reason, elapsed_seconds=elapsed,
                    remaining_seconds=remaining, attempt=attempt, baseline=None,
                    control_attempted=None if recovery_pending is not False else False,
                    recovery_pending=recovery_pending)

        wait_for_readiness(probe, stop, check_startup, waiting, clock=time.monotonic)
        check_startup()
        recovery_pending = None
        recovery_pending = recovery_journal_exists(journal_path)
        journal = RecoveryJournal(journal_path, adapter.identity)
        if recovery_pending:
            publish('checking', phase='recovering', baseline=None, control_attempted=None,
                    recovery_pending=True, reason='Restoring interrupted GPU fan session')
        # The named owner mutex covers both recovery and this new session.
        # Never adopt a leftover HeatMap curve as the user's original settings.
        recovered = journal.recover(adapter, accept_external=accept_external,
                                    before_write=check_startup, check_cancelled=check_owner)
        recovery_pending = False
        started = time.monotonic()
        ramp = GpuRamp()
        policy = GpuAssistPolicy()
        last_stamp = baseline_readings['timestamp_ms']
        last_fresh = started
        stall_since = None
        takeover_time = None
        verified_rpm = None
        if commission:
            session = GpuSession(adapter, journal)
            publish('checking', baseline=session.baseline, gpu=adapter.identity, control_attempted=False,
                    reason='Checking GPU full airflow')
            check_startup()
            session.apply(100, before_write=check_startup, check_cancelled=check_owner)
            control_attempted = True
            takeover_time = started
            ramp = GpuRamp(initial=100)
        while not stop.is_set() and owner.is_running():
            if heartbeat.expired():
                raise RuntimeError('GPU owner heartbeat expired')
            require_hardware_access()
            data = adapter.readings()
            now = time.monotonic()
            if stop.is_set() or not owner.is_running() or heartbeat.expired():
                break
            if not isinstance(data, dict):
                raise RuntimeError('Invalid AMD GPU metrics response')
            if session is not None:
                session.check()
            stamp = finite(data.get('timestamp_ms'), 0, 1e15)
            if stamp is not None and last_stamp is not None and stamp < last_stamp:
                raise RuntimeError('GPU metrics timestamp moved backwards')
            fresh = stamp is not None and (last_stamp is None or stamp > last_stamp)
            if fresh:
                last_stamp, last_fresh = stamp, now
            elif now - last_fresh > 6:
                data = dict(data, gpu_core_temp=None, gpu_hotspot_temp=None, gpu_memory_temp=None)
            requested, reason = demand(data)
            if now - last_fresh > 15:
                raise RuntimeError('AMD GPU metrics stopped updating')
            policy_data = data if fresh else dict(data, **dict.fromkeys(CURVES))
            assist = commission or policy.update(policy_data, now)
            if not assist:
                if session is not None:
                    errors = session.restore()
                    if errors:
                        raise RuntimeError('GPU cooling handback unconfirmed: ' + '; '.join(errors))
                    session = None
                    ramp = GpuRamp()
                    stall_since = takeover_time = None
                thermal_ready = all(finite(policy_data.get(key), 1, 150) is not None for key in CURVES)
                publish('standby', reason=('GPU cool; saved driver curve controls fans' if thermal_ready else
                                          'Waiting for GPU temperatures; saved driver curve controls fans'),
                        readings=data, thermal_ready=thermal_ready, control_attempted=control_attempted,
                        recovered_previous_session=recovered, gpu=adapter.identity)
                stop.wait(2)
                continue
            if session is None:
                # Re-snapshot on each takeover: Adrenalin edits while idle are owned
                # by the user and become the new settings to restore.
                session = GpuSession(adapter, journal)
                takeover_time = now
            rpm = finite(data.get('gpu_fan'), 200, 10000)
            if rpm is None:
                if stall_since is None:
                    stall_since = now
                # Zero RPM at takeover needs time to spin up, not an immediate blast.
                if now - takeover_time >= 6:
                    requested, reason = 100, 'GPU fan tachometer unavailable/stopped'
                if now - stall_since >= 10:
                    raise RuntimeError('GPU fan tachometer unavailable/stopped')
            else:
                stall_since = None
            if commission and now - started < 15:
                requested, reason = 100, 'Checking GPU full airflow'
            elif commission and verified_rpm is None:
                if rpm is None or rpm < 2500:
                    raise RuntimeError('GPU full airflow not confirmed by RPM')
                before_rpm = finite(baseline_readings.get('gpu_fan'), 0, 10000)
                if before_rpm and rpm < before_rpm * 0.90:
                    raise RuntimeError('GPU RPM fell during full-airflow verification')
                if before_rpm and before_rpm < 2500 and rpm < before_rpm * 1.10:
                    raise RuntimeError('GPU fans did not respond to full airflow')
                verified_rpm = rpm
            command = ramp.update(requested, now)
            if not fresh and session.command is not None:
                # No cooling credit from repeated metrics, including the short
                # grace period before frozen readings become a full-airflow fault.
                command = max(command, session.command)
                ramp.command = command
                ramp.cool_since = None
            if command != session.command:
                if stop.is_set() or not owner.is_running() or heartbeat.expired():
                    break
                require_hardware_access()
                session.apply(command, before_write=check_startup, check_cancelled=check_owner)
                control_attempted = True
            publish('checking' if commission and verified_rpm is None else 'active',
                    command_pct=command, demand_pct=requested, reason=reason,
                    readings=data, baseline=session.baseline, control_attempted=True,
                    verified_full_rpm=verified_rpm, recovered_previous_session=recovered, gpu=adapter.identity)
            stop.wait(2)
    except StartupCancelled:
        startup_cancelled = not (control_attempted or bool(session and session.touched))
    except Exception as exc:
        error = str(exc)
    finally:
        touched = bool(session and session.touched)
        control_attempted = control_attempted or touched
        if session is not None:
            restore_errors = session.restore()
            if restore_errors:
                time.sleep(0.2)
                restore_errors = session.restore()
        if adapter is not None:
            try:
                adapter.close()
            except Exception as exc:
                restore_errors.append(str(exc))
        publish('error' if error or restore_errors else 'stopped',
                reason=error or ('GPU fan restore unconfirmed' if restore_errors else
                                'GPU startup cancelled before takeover' if startup_cancelled else
                                'Saved GPU fan curve restored' if control_attempted or recovered else
                                'GPU driver settings left unchanged'),
                control_attempted=None if recovery_pending is not False else bool(control_attempted or recovered),
                recovery_pending=recovery_pending,
                restore_confirmed=bool((control_attempted or recovered) and not restore_errors),
                restore_errors=restore_errors, baseline=session.baseline if session else None,
                settings_conflict=session.conflict if session else None)
    return 1 if error or restore_errors else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--status', required=True)
    parser.add_argument('--owner-pid', required=True, type=int)
    parser.add_argument('--owner-created', required=True, type=float)
    parser.add_argument('--commission', action='store_true', help='Explicit 15-second full-airflow hardware check')
    parser.add_argument('--accept-external', action='store_true',
                        help='Explicit re-enable: archive a conflicting journal and retain current driver settings')
    args = parser.parse_args()
    try:
        with WorkerMutex('Global\\HeatMapGpuFanControlV1'):
            return worker(args.status, args.owner_pid, args.owner_created, commission=args.commission,
                          accept_external=args.accept_external)
    except Exception as exc:
        write_status(args.status, 'error', profile=PROFILE, reason=str(exc), control_attempted=None,
                     recovery_pending=None, restore_confirmed=False, restore_errors=[])
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
