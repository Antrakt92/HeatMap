"""Shared fan-worker primitives: heartbeat watchdog, status validation,
terminal evidence policy, commissioning wait-loop, and journal file envelope.

Lower layer: imports only stdlib plus startup_readiness and thermal_policy,
so both the case and GPU workers (and their commissioning scripts) can depend
on it without import cycles. psutil is used only for the owner-tree walk and
never imports project code.
"""
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import psutil

from startup_readiness import StartupCancelled, STARTUP_TIMEOUT_SECONDS
from thermal_policy import finite


HEARTBEAT_TIMEOUT_SECONDS = 15
# Case/GPU status reports older than this are treated as stale.
STATUS_STALE_SECONDS = 10


HEARTBEAT_TIMEOUT_SECONDS = 15


# Terminal-report compact allowlists. When the full report does not fit
# (ENOSPC), only these keys survive into the smaller report, so the status
# client keeps rollback evidence. Per-worker: adding a key to a full report
# without listing it here hides it from the compact fallback by design.
CASE_TERMINAL_KEYS = ("profile", "restore_confirmed", "restore_errors", "control_attempted",
                      "controlled_channels", "firmware_channels",
                      "recovery_pending", "settings_conflict")
GPU_TERMINAL_KEYS = ("profile", "restore_confirmed", "restore_errors", "control_attempted",
                     "recovery_pending", "settings_conflict")


class OwnerHeartbeatExpired(StartupCancelled):
    """Watchdog cancellation, distinct from an intentional owner shutdown."""


class OwnerHeartbeat:
    """Owner liveness tracker fed by alive/stop lines on stdin."""

    def __init__(self, stop):
        self.stop = stop
        self.last_seen = time.monotonic()

    def expired(self):
        return time.monotonic() - self.last_seen > HEARTBEAT_TIMEOUT_SECONDS

    def listen(self):
        try:
            for line in sys.stdin:
                if line.strip() == "stop":
                    break
                if line.strip() == "alive":
                    self.last_seen = time.monotonic()
        finally:
            self.stop.set()


def check_owner(stop, owner, heartbeat, stopped_message, expired_message):
    # An intentional stop or lost owner must never qualify for watchdog recovery.
    if stop.is_set() or not owner.is_running():
        raise StartupCancelled(stopped_message)
    if heartbeat.expired():
        raise OwnerHeartbeatExpired(expired_message)


def check_guarded(stop, owner, heartbeat, guard, stopped_message, expired_message):
    check_owner(stop, owner, heartbeat, stopped_message, expired_message)
    guard()
    # Process inspection may block; recheck the owner after the guard too.
    check_owner(stop, owner, heartbeat, stopped_message, expired_message)


def restore_with_retry(restore):
    """Run a session restore, retrying once after a short settle delay.

    Keeps the first errors too: a clean retry must not erase transient
    evidence, nor hide a changed second failure.
    """
    errors = restore()
    if errors:
        time.sleep(0.2)
        errors = errors + [error for error in restore() if error not in errors]
    return errors


def stage_json_payload(path, payload):
    """Atomically stage a JSON payload at a sibling tmp path; returns it.

    fsyncs before returning. The caller performs the final replace with its
    own primitive (replace_status_file for concurrent readers, os.replace
    otherwise) and must remove the staging file afterwards, including on
    replace failure. Staging errors clean up after themselves and propagate.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(payload, stream)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def load_json_payload(path, opener):
    """Parse bounded JSON through opener(path). Errors propagate for callers to map."""
    with opener(path) as stream:
        return json.loads(stream.read(65536))


@dataclass(frozen=True)
class StatusPolicy:
    """Per-worker validation contract for one parsed status report."""
    profile: str
    states: tuple
    profile_error: str
    shape_error: Optional[str]      # combined state/stamp/pid check; None to skip
    reason_error: str
    stop_cause_error: str
    restoration_error: str
    restoration_keys: tuple       # case also type-checks control_attempted; GPU does not
    state_error: Optional[str]      # standalone state check; None to skip
    launch_error: str               # out-of-window stamp
    stale_error: str
    exited_error: str
    owner_first: bool               # True: launch window before owner walk (GPU)
    owner_foreign_error: Optional[str]  # live PID outside our tree; None keeps stale path
    owner_unconfirmed_error: Optional[str]  # None: fall back to stamp check (case)
    opening_timeout: Optional[float]  # None: unreadable report without history raises
    opening_text: str
    extra_early: Optional[Callable]  # (status) -> reason | None, before owner block
    extra_late: Optional[Callable]   # (status) -> reason | None, after exited check


def write_alive(process):
    """Ping a live worker; a dead pipe never fails the poll."""
    try:
        process.stdin.write("alive\n")
        process.stdin.flush()
    except (OSError, ValueError):
        pass


def read_status_report(opener, path, last_status, *, exited, started, now,
                       opening_timeout, opening_text):
    """Read and parse one status file.

    Returns (status, needs_validation). An unreadable file falls back to the
    last verified report; with no history, the opening grace (GPU) returns a
    checking report that skips validation, otherwise the original error raises.
    """
    try:
        with opener(path) as stream:
            return json.loads(stream.read(65536)), True
    except (OSError, ValueError):
        if last_status is None:
            if (opening_timeout is not None and not exited
                    and now - started < opening_timeout):
                return {"state": "checking", "reason": opening_text}, False
            raise
        # A busy file must not manufacture an error while the last verified
        # report is still fresh. All PID/expiry checks still run on it.
        return last_status, True


def resolve_worker_pid(pid, process_pid, known_pid):
    """Classify a reported worker PID. Returns (outcome, pid_to_keep).

    outcome is "direct" (owner or already-known PID), "child" (redirector
    child, adopt it), "foreign" (live process outside our tree) or "missing"
    (invalid, gone, or inaccessible). Error policy stays with callers.
    """
    if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
        if pid == process_pid or pid == known_pid:
            return "direct", known_pid
        try:
            # Windows venv python[w].exe is a redirector whose child writes
            # the report. The root process remains alive until that child exits.
            child = psutil.Process(pid)
            if any(parent.pid == process_pid for parent in child.parents()):
                return "child", pid
            return "foreign", known_pid
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return "missing", known_pid


def check_status(policy, status, *, exited, worker_pid, process_pid, started, now):
    """Validate one parsed report. Returns (error_reason | None, worker_pid).

    Branch order mirrors the historical per-worker validators exactly; only
    the message/contract differences in `policy` vary. Pure except the
    psutil owner-tree walk.
    """
    if not isinstance(status, dict) or status.get("profile") != policy.profile:
        return policy.profile_error, worker_pid
    stamp = finite(status.get("time"), 0, 1e12)
    state = status.get("state")
    pid = status.get("pid")
    terminal = state in ("error", "stopped")
    if policy.shape_error is not None and (
            state not in policy.states or stamp is None
            or type(pid) is not int or pid <= 0):
        return policy.shape_error, worker_pid
    if "reason" in status and not isinstance(status["reason"], str):
        return policy.reason_error, worker_pid
    if ("stop_cause" in status and (status["stop_cause"] != "heartbeat_expired"
                                    or state != "error")):
        return policy.stop_cause_error, worker_pid
    if (any(key in status and type(status[key]) is not bool for key in policy.restoration_keys)
            or ("restore_errors" in status
                and (not isinstance(status["restore_errors"], list)
                     or any(not isinstance(item, str) for item in status["restore_errors"])))):
        return policy.restoration_error, worker_pid
    if policy.state_error is not None and state not in policy.states:
        return policy.state_error, worker_pid
    if policy.extra_early is not None:
        reason = policy.extra_early(status)
        if reason is not None:
            return reason, worker_pid
    if policy.owner_first:
        if stamp is None or stamp < started - 2 or stamp > now + 2:
            return policy.launch_error, worker_pid
    outcome, worker_pid = resolve_worker_pid(pid, process_pid, worker_pid)
    if outcome == "foreign" and policy.owner_foreign_error is not None:
        return policy.owner_foreign_error, worker_pid
    if outcome == "missing":
        if policy.owner_unconfirmed_error is not None and not (exited and terminal):
            return policy.owner_unconfirmed_error, worker_pid
        # Case-style fallback: a fast terminal result can precede the first
        # poll. This path is unique to this launch and must have been written
        # after it; the launch window below re-checks the stamp either way.
    if (outcome not in ("direct", "child")
            and not (exited and terminal and stamp is not None and stamp >= started - 2)) \
            or stamp is None or stamp < started - 2 or stamp > now + 2:
        return policy.launch_error, worker_pid
    if not (exited and terminal) and now - stamp > STATUS_STALE_SECONDS:
        return policy.stale_error, worker_pid
    if exited and not terminal:
        return policy.exited_error, worker_pid
    if policy.extra_late is not None:
        reason = policy.extra_late(status)
        if reason is not None:
            return reason, worker_pid
    return None, worker_pid


def verify_loop(client, samples, duration, *, evidence_ok, never_acquired,
                stopped_message, evidence_message, timestamp_message,
                timeout_message, restore_message):
    """Shared commission wait-loop: sustained active samples with proof.

    Collects only advancing status stamps as samples, stops the worker, waits
    for native restore, then requires verified restoration. evidence_ok and
    never_acquired carry the per-hardware proof rules; messages stay per
    caller so reports keep their exact wording.
    """
    if finite(duration, 0, 3600) is None:
        raise ValueError("Verification duration must be finite and between 0 and 3600 seconds")
    client.start()
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS + 30 + duration
    active_since = None
    first_stamp = None
    last_stamp = None
    failure = None
    try:
        while time.monotonic() < deadline:
            status = client.poll()
            if status["state"] in ("error", "stopped", "off"):
                raise RuntimeError(status.get("reason", stopped_message))
            if status["state"] == "active":
                if not evidence_ok(status):
                    raise RuntimeError(evidence_message)
                stamp = finite(status.get("time"), 0, 1e12)
                if stamp is None or (last_stamp is not None and stamp < last_stamp):
                    raise RuntimeError(timestamp_message)
                if last_stamp is None or stamp > last_stamp:
                    # The client can return a recent cached snapshot on a busy
                    # status file. Re-reading it is not another hardware sample.
                    samples.append(status)
                    if active_since is None:
                        active_since, first_stamp = time.monotonic(), stamp
                    last_stamp = stamp
                    if (time.monotonic() - active_since >= duration
                            and stamp - first_stamp >= duration):
                        break
            else:
                active_since = first_stamp = None
            time.sleep(2)
        else:
            raise RuntimeError(timeout_message)
    except Exception as exc:
        failure = exc
    finally:
        client.stop()
        if client.process is not None:
            try:
                client.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                raise RuntimeError("Native fan restore did not finish. Restart Windows before retrying.")
    restored = client.poll()
    if failure and never_acquired(restored):
        raise failure
    if not restored.get("restore_confirmed") or restored.get("restore_errors"):
        raise RuntimeError(restore_message + str(restored))
    if failure:
        raise failure
    return restored
