"""Bounded, cancellable retries for read-only hardware discovery before takeover."""
import math
import time


STARTUP_TIMEOUT_SECONDS = 60.0
STARTUP_RETRY_SECONDS = 1.0


class StartupNotReady(RuntimeError):
    """Only positively identified temporary absence of startup data is retryable."""


class StartupCancelled(RuntimeError):
    pass


def wait_for_readiness(probe, stop, check, waiting, timeout=STARTUP_TIMEOUT_SECONDS,
                       retry_interval=STARTUP_RETRY_SECONDS, clock=None):
    """Retry missing data, never native writes, conflicts or arbitrary exceptions.

    A probe must own no hardware mutations. Its caller retains resource ownership
    even when cancellation/deadline checks reject a successful probe result.
    """
    if any(type(value) not in (int, float) or not math.isfinite(value) or value <= 0
           for value in (timeout, retry_interval)):
        raise ValueError("Startup timeout and retry interval must be positive and finite")
    clock = clock or time.monotonic
    started = clock()
    deadline = started + timeout
    previous = started
    reason = "hardware discovery did not complete"
    attempt = 0

    def guard():
        nonlocal previous
        if stop.is_set():
            raise StartupCancelled("Startup cancelled before fan takeover")
        check()
        now = clock()
        if not math.isfinite(now) or now < previous:
            raise RuntimeError("Startup monotonic clock became invalid")
        previous = now
        if now >= deadline:
            raise StartupNotReady(f"Startup readiness timed out after {timeout:g}s: {reason}")
        return now

    while True:
        guard()
        attempt += 1
        try:
            result = probe()
        except StartupNotReady as exc:
            reason = str(exc)
            now = guard()
            waiting(reason, now - started, deadline - now, attempt)
            now = guard()
            if stop.wait(min(retry_interval, deadline - now)):
                raise StartupCancelled("Startup cancelled before fan takeover")
        else:
            guard()
            return result
