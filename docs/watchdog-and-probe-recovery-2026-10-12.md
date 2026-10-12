# Watchdog and delayed-probe recovery

## Observed failure

The running overlay reported version 1.3.0. Both fan owners expired their
15-second UI heartbeat. Case control restored its owned channels and completed
the existing one-time retry. GPU control was in untouched driver-curve standby:
`control_attempted=false`, `recovery_pending=false`, empty restore errors and no
baseline. Its truthful `restore_confirmed=false` incorrectly blocked recovery.
All four SMART rows also reported a timeout, while Windows volume capacity and
physical mappings remained available. The user reported ordinary operation,
without sleep or a menu/dialog. The cause of that original UI pause is unknown;
there is no evidence that the release or test run caused it.

## Confirmed repairs

- A shared terminal-evidence predicate permits verified handback or explicitly
  untouched control. Untouched-control recovery requires explicit
  `recovery_pending=false`; verified legacy handback reports remain compatible.
  Malformed flags, restoration errors, conflicts and failed terminal publication
  block recovery. The existing
  exited-process requirement, two seconds of responsive polling and one retry
  remain. Recovery never accepts external GPU settings.
- A transient UI/status exception no longer permanently removes the next polling
  callback. Both owners are polled before rendering, so a broken GPU label cannot
  starve case control. Repeated failures back off and limit exception logging.
  Long refreshes and polling gaps are logged. Scheduler toggles run in a
  non-daemon worker, verify the requested result and publish through the existing
  UI queue. Failed operations remain visible; read-only retries never repeat writes.
- Native Windows message boxes invoked on Tk's thread stopped its timers in an
  independent disposable-window experiment. In-app messages now use nonmodal Tk
  windows with scrollable text. Launcher errors before the application starts
  retain their existing native dialog. This adjacent defect does not establish
  the cause of the ordinary-use incident.
- A delayed storage owner formerly discarded a final reply already waiting in
  its pipe. Replies now carry monotonic completion timestamps. After the deadline,
  the runner polls only already queued bytes and accepts only completion at or
  before the original deadline. Late completion and cancellation remain failures;
  no native query receives extra time. Storage and volume publication preserve
  actual sample age, so a delayed old result cannot become a current temperature.
- Disk probes report fixed stage names through startup, runtime loading,
  properties, SMART, reading and cleanup. A timeout preserves the last stage.
  A stage-publication failure cannot skip native disposal.

## Evidence and remaining limits

Regression tests reproduced the standby recovery rejection, lost queued replies,
late-reply acceptance, missed cancellation, synchronous Scheduler calls and a
broken UI polling chain before their repairs. Tests preserve unknown ownership,
the one-retry limit, saved settings and stale-data rejection.

A short elevated read-only check completed all four physical disk probes in
0.118-0.682 seconds each, returning temperatures and their real sample timestamps.
Two used Windows storage properties and two used the existing SMART fallback.
It sent zero fan commands and changed no driver or dependency. This proves those
queries were working at that time, not the cause of the earlier simultaneous
timeouts or continuous hardware reliability.

1089 local tests passed, with compilation, runtime integrity/preflight, runtime
manifest synchronization and local Win32 checks. Hidden Tk notice integration
kept twelve timer callbacks running during each open notice. A normal elevated
restart completed; fresh GPU status showed driver-curve standby with 0 RPM and
case status showed active CPU assistance. Preferences, including Alerts OFF,
were preserved. This is a short startup/read-back check, not a new load/noise test.

The protective timeout stays 15 seconds. A genuinely unresponsive UI, native
driver failure, external ownership conflict or unconfirmed handback must still
stop software control. No software repair guarantees uninterrupted operation
through every Windows/driver/hardware failure. Reboot/login, extended gaming,
noise and other physical acceptance checks remain informational follow-ups.
