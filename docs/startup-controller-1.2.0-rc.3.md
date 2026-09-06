# Controller startup validation — 1.2.0-rc.3

The reported controller stopped before creating a fan-control session, with
`Missing or ambiguous case fan channel: System Fan #1`. The overlay's later
inventory contained SYS1/2 tachometers. The old message did not distinguish which
sensor was missing, so the original incident alone cannot prove a unique cause.

The pinned LibreHardwareMonitor v0.9.5 source at commit
`30395c48a7f912894a7c392db8c11e1b97859658` confirms a startup failure path:
`IT87XX.Update` skips a read when its ISA mutex cannot be acquired within 10 ms,
and `SuperIOHardware.Update` activates tachometer sensors only after a non-null
reading. HeatMap previously selected its required channels after just one read.

The controller now makes up to four reads of the same Computer, waiting 0.5 seconds
between missing-channel/initial-null-tachometer failures. No fan commands occur
during discovery. Cancellation, owner death, expired heartbeat and a newly detected
hardware-tool conflict stop retries. Duplicate sensors, wrong identities, a stopped
tachometer and a missing control object remain immediate failures. Exhausted
discovery reports the channel and control/tachometer counts.

An explicit no-command terminal snapshot now keeps firmware ownership labels and
explains that automatic control could not start. Unknown failures and unconfirmed
restoration retain their existing warnings. The activation wizard preserves the
original startup cause only with explicit no-command evidence.

## Automated verification

- 491 unittest cases passed, including delayed discovery, cancellation, permanent
  refusal, client snapshot validation, activation and UI ownership regressions.
- Both delayed-discovery regressions fail when the original single-read behavior
  is substituted, and pass with the revised discovery loop.
- Compilation, DLL verification, setup preflight, runtime-manifest drift check,
  pip dependency consistency, locked-version ages and license provenance passed.
- Disposable off-screen Win32 checks passed for DWM exclusion, under-application
  ordering, minimize recovery and unchanged foreground focus. Hidden Tk tests cover
  compact fan rows and the scrollable cooling dialog at increased DPI.

## Elevated local acceptance — 2026-09-06

The previous overlay was closed normally. A separate bounded commissioning run
verified SYS1/SYS2 full airflow and then returned them to their original state:
`restore_confirmed=true`, with no restore errors. Primary output-mode checks passed.
The updated overlay was then launched with the existing configuration and observed
through commissioning and at least eight seconds of fresh active worker reports.
Only SYS1/SYS2 were selected; SYS4/5/6 remained firmware-owned. Observed full-speed
tachometers were approximately 1216 and 1162 RPM.

This validates startup and restoration on the available machine, not long-term
Windows stability, physical fan placement or cooling effectiveness in games.
Explorer/Show Desktop, normal-workload sound and physical multi-monitor/mixed-DPI
acceptance remain open in AUDIT.md. This version remains a prerelease.
