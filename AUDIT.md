# HeatMap Audit Backlog

Storage inventory repair (2026-10-29): elevated fixed-volume enumeration through
`psutil.disk_partitions` reproduced an eight-second timeout while individual
C:/D:/E:/F: capacity/extent queries succeeded in 0.10�0.68 seconds. Enumeration
now uses Windows drive letters and drive types without querying filesystem
metadata for all drives. Each fixed volume retains its isolated bounded read;
remote, optical and removable drive types are excluded. A failed drive-letter
inventory remains an error rather than a verified empty inventory.

The blocked-enumeration regression fails against the prior implementation and
passes with the repair. Local checks passed 1,160 unit tests, compileall, DLL
integrity, startup preflight, manifest synchronization and volume integration
(including synthetic blocked-child disposal). Elevated probes returned all four
volumes and temperatures for all four disks in two consecutive passes. The
individual temperature timeouts in the supplied report were not reproduced;
their original runtime/SMART cause remains unconfirmed. Deadlines, freshness,
backoff, fan policy and dependencies are unchanged.

Normal close recorded process exit and verified case-fan restoration. The
existing close helper initially timed out waiting for the launcher; after exit
was confirmed, the normal Scheduler task restarted HeatMap. A live window
capture showed all four volume rows with capacity and temperature, and WM_NULL
confirmed responsiveness. Fresh case/GPU status reports showed active/standby.
Evidence: `%LOCALAPPDATA%/HeatMap/storage-{baseline,stages,live}-20261029.*`.
Extended load, sleep/resume and physical reboot/login remain unperformed.


Unexpected-exit investigation (2026-10-24): no overlay process remained when
reported. The previous case/GPU status files ended at approximately 17:55 in
active/standby states, without terminal reports. No matching Application Error,
Windows Error Reporting, .NET error, application hang or system reboot event was
found for that interval. The warning-only HeatMap log contained no current exit
evidence, so the historical termination cause remains unconfirmed; this is not
proof of a native crash, a normal close or external process termination.

Lifecycle diagnostics now retain startup version/PIDs, normal shutdown requests,
process exits and unhandled main-thread exception tracebacks in the existing
rotating log. INFO logging also retains the existing recovery messages. No sensor,
fan-control, driver, timeout or automatic-restart policy changed. Regression tests
exercise actual file logging, propagated exceptions with cleanup, and idempotent
shutdown logging. Check a normal elevated close/restart and fresh controller
statuses; future forced termination can still prevent a final log entry.
Local verification passed 1,159 tests, compilation, DLL integrity and startup
preflight. The prior termination cause remains open despite the logging repair.

PiP/topmost ordering repair (2026-10-24): the visibility poll previously checked
only WS_EX_TOPMOST, allowing a later topmost video window to cover HeatMap
indefinitely. The existing 250 ms poll now detects visible, overlapping foreign
topmost windows above the widget and reasserts its z-order without activation,
movement or resizing. Both Always on top and temporary edge raise use this path;
ordinary desktop mode still stays below applications. Hidden/nonoverlapping
windows and HeatMap's own windows do not trigger repeated reordering. Traversal
is bounded against disappearing/cyclic window chains.

Native popup inspection also found that raising an owner can cover its Windows
menu. Recovery now restores only this process's visible native menus, preserving
submenu order. Owned dialogs remain above their owner. The initial regression
failed in both persistent and hover modes before the fix. Focused unit checks,
real off-screen Win32 PiP-order tests (foreign PID modeled), and a real native Tk
popup check passed. The PiP fixture starts in the existing topmost band, matching
the reported defect; it verifies recovery, retained dialog order, unchanged
foreground and the other window's unchanged topmost style.

Final local checks passed: 1,156 unit tests, compileall, DLL integrity, startup
preflight, runtime manifest synchronization and native desktop/PiP integration.

Manual elevated follow-up: overlap real browser PiP with HeatMap, raise PiP again
after showing HeatMap, and check recovery in both edge-hover and Always on top
modes. Open submenus and Cooling/CPU reference dialogs, check their order and
departure cancellation, then disable Always on top and confirm desktop behavior.
This does not claim acceptance of a particular browser/video player.

Settings/edge-hover repair (2026-10-24): native Windows Tk menus report an
unmapped 1x1 Tk placeholder while their actual popup remains visible. The old
hover check could therefore lower the widget beneath other applications and
leave its settings stranded above them. Native menu hover now checks the popup
under the cursor and its process identity. HeatMap dialogs also participate in
hover bounds rather than pinning the preview open indefinitely. Departure closes
the native menu and invokes each dialog's normal Cancel/Close handler before
lowering the widget; the next hover starts with no old settings. Nested dialogs
close before their parents; cancelling the CPU reference does not save input.

Three regressions failed against the previous transition methods and pass with
the repair. Local verification: 1,150 unit tests, compileall, DLL verification,
runtime manifest synchronization, and provenance checks for 23 licensed packages
passed. A real Windows popup check confirmed menu hover, native EndMenu during
the popup event loop, owner lowering, clean popup return and reentry. Disposable
native dialog/modal tests and the desktop integration check passed. No driver,
sensor, fan-control policy or runtime dependency changed.

Manual elevated smoke checklist: hover the edge, open settings and each submenu,
move between menu and widget, then leave both; settings should close together
with the temporary raise. Repeat with CPU reference and Cooling status; unsaved
CPU reference must cancel. Reenter repeatedly and confirm no stale dialog or
overlay above old settings. Check Always on top and dragging separately. Physical
Explorer/multi-monitor acceptance remains outside the disposable-window checks.

Driver-pause recovery audit (2026-10-22): the supplied diagnostics showed a
historical 72-second sensor snapshot after a PnPUtil pause. HeatMap.log records
the pause at 12:38:49; Windows setupapi.dev.log confirms live driver-package
uninstall operations at 12:38:50. The guard was justified, rather than a proven
false positive. The sensor thread and storage-temperature thread previously
terminated on that transient condition, leaving readings unavailable indefinitely.
They now retain one owner, close the old native monitor, and reopen only after
15 seconds of continuously clear access and verified controller handback.
Unknown conflicts, failed Close, pending recovery, and a still-running fan owner
remain hard stops. Fan assistance requires a deliberate OFF/ON after the pause;
fresh Windows RAM and volume capacity remain independent of native sensors.

An independent GPU audit reproduced interrupted ownership being left pending
when a temperature was unavailable: recovery waited behind thermal readiness.
Valid interrupted settings are now restored first under the existing identity,
ownership and external-change guards. A new takeover still requires complete
advancing temperatures. Legacy PnPUtil mutation syntax and unreadable arguments
also now remain guarded. No driver, DLL, thermal threshold or watchdog was changed.
Regression and live verification details: docs/driver-recovery-audit-2026-10-22.md.
Local integration passed 1,143 tests, compilation, runtime/preflight/manifest
and license-provenance checks, plus disposable native window/notice checks.
Normal elevated restart produced a responsive visible overlay and repeated
fresh active B850/standby GPU reports. Real driver replacement, sustained load,
sleep/resume and native-call hangs remain separate verification boundaries.

Storage/UI scheduling follow-up (2026-10-22): the live log again showed UI
polling gaps and the supplied diagnostics had failed volume discovery and disk
probe timeouts. The owned Scheduler task used its implicit priority 7; the live
overlay had BELOW_NORMAL CPU priority. Windows assigns background CPU, I/O and
memory priority to that task setting. The task now explicitly requests ordinary
interactive priority 6, parses/validates it, and migrates enabled owned old tasks
through the existing guarded replacement. Disabled tasks, foreign owners,
read-only checks and the LeastPrivilege/UAC boundary remain preserved.
Reference: [Microsoft task priority mapping](https://learn.microsoft.com/en-us/windows/win32/taskschd/taskschedulerschema-priority-settingstype-element).

Five new regressions failed before the repair and pass afterward. Local checks:
1,123 unit tests, compileall, DLL integrity, preflight, manifest synchronization,
disposable Scheduler round-trip (exported priority 6), desktop/notice checks and
bounded volume integration passed. The elevated migration and normal close/task
restart succeeded; the new overlay has NORMAL CPU priority, is responsive and
fits its work area. A window capture confirms C:/D:/E:/F: rows with fullness and
temperatures, with only the expected CPU-warm warning. Independent elevated
reads returned all four capacities in 0.16–0.17 seconds each and all four disk
temperatures in 0.22–0.98 seconds each. Fresh controller reports show active B850
SYS1/2/3 and GPU driver-curve standby. Evidence is retained under
`%LOCALAPPDATA%/HeatMap/repair-priority-20261022.json`,
`render-live-20261022.json` and `storage-repair-window-20261022.png`.

A same-session normal-priority volume check succeeded at 100% CPU; lowering its
priority slowed queries. However, CPU load had fallen to about 34% by the live
restart, so this is a verified scheduling defect and current recovery, not proof
that every earlier timeout or CPU load source is explained or that identical-load
stability is established. Probe deadlines, stale-data rules and fan watchdogs
were not relaxed. Extended load, reboot/login and physical display checks remain
informational follow-ups.

Sensor-rendering heartbeat follow-up (2026-10-21): both workers again stopped
with `heartbeat_expired`; the log includes a 33.8-second refresh and a later
24.9-second polling gap. Deferred layout alone did not cover sensor rendering.
The UI now renews both owners between completed CPU/GPU/RAM rendering stages,
fan rows, storage rows and detail/error labels. No background heartbeat, timeout
extension or additional automatic recovery was added. Regressions simulate
36-second normal and error refreshes; both failed before the change and pass
afterward. This proves aggregate rendering starvation is repaired, not the
cause of every individual slow native call or scheduling gap.

Local verification passed 1,118 unit tests, compileall, runtime integrity,
preflight, manifest synchronization and disposable desktop/notice integration.
Normal shutdown and independent scheduled-task restart produced a visible,
responsive window inside the work area, fresh active B850 SYS1/2/3 reports and
GPU standby on its saved driver curve. Extended load stability and the cause of
the earlier volume/disk probe timeouts remain unconfirmed.

UI redraw/edge-hover follow-up (2026-10-18): the loaded runtime again recorded
single layout flushes of 15–16 seconds and a 60–73 second refresh. A bounded
stack trace sampled the UI in Tk `update_idletasks`, including native message
dispatch through the mouse-activation subclass. This establishes the blocking
UI path, not a GPU-driver defect or a complete explanation of CPU scheduling.
Regular refreshes now run the same layout algorithm as scheduled short stages,
without explicitly draining the idle queue. Repeated HWND checks use the stable
live HWND instead of flushing layout every 250 ms. Completed deferred geometry
always clamps the widget to the monitor work area. Watchdog and ownership
requirements remain unchanged; no unconditional background heartbeat was added.

The fresh case-worker report separately confirmed missing IT8696E discovery on
the exact B850 board before any commands. A missing startup controller now
reopens the read-only monitor at most five times within the existing 60-second
readiness wait. Ambiguous chips and wrong boards remain fatal; pending ownership
is never bypassed by this retry. GPU executable and checkout paths existed in
the inspected process. The earlier raw WinError 2 screenshot alone does not
identify which missing file generated it.

Regressions cover no idle draining during regular layout, coalesced requests,
cached HWND lookup, final work-area bounds, shutdown, transient/permanent chip
absence and original hardware identity guards. Existing DPI/clipping assertions
now wait for deferred completion rather than weakening their bounds. Two WoW
clients were closed by the user during diagnosis; a new identical-load gaming
comparison has not been performed.
Local verification passed 1,117 unit tests, compilation, DLL/preflight/manifest
checks and disposable Windows placement/notice integration. After normal
elevated restart, WM_NULL completed within the one-second inspection timeout;
the live overlay was visible at (1749,193)-(2046,710), inside the 2048x1104 work
area. Fresh GPU standby and active B850 reports were read independently of the
UI. These are restart/responsiveness checks, not identical-load gaming proof.

UI layout heartbeat starvation (2026-10-18): both owners stopped twice with
`heartbeat_expired` and explicit restoration/no-command evidence. Logs record
UI refreshes of 16.5–27 seconds; the single automatic retry was exhausted.
Heartbeats previously occurred only at controller polling before rendering.
Repeated synchronous layout passes could cumulatively exceed the watchdog
despite ongoing UI progress. Both clients now receive keepalives between those
passes and before status polling, on the UI thread only. The 15-second timeout,
single retry and restoration/conflict requirements are unchanged. Slow completed
layout flushes now have a specific log message.

A regression reproduces six four-second layout passes starving both owners
before the fix, and verifies renewal with the fix. A single blocked call still
expires ownership; shutdown and broken-pipe paths are covered. The original
slow native call was not identified: a bounded 90-second startup trace did not
reproduce it. Extended ordinary-use/load acceptance remains open; do not claim
that this fixes every possible Windows, driver or event-loop stall.
Verification: 1,111 unit tests passed, as did compileall, runtime verification,
preflight, manifest synchronization and disposable Win32/notice checks. A local
Tk/pipe smoke test kept two sensor-free child owners alive through six real
three-second layout passes (18 seconds total), then confirmed expiry during
one 16-second blocked call. Normal elevated restart produced fresh GPU standby
and active B850 case-fan reports; no new full-speed commissioning was run.

GPU startup access recovery (2026-10-17): a transient `pnputil.exe` access
conflict before ADLX initialization previously left Cooling in ERROR until
manual intervention. The GPU worker now identifies that untouched startup path;
the UI permits one retry only after the worker exits, explicit no-command and
no-recovery evidence, and two seconds of clear GPU access checks. Original
diagnostics are retained. Native faults, conflicts after opening ADLX, unknown
restoration, and installer-latched sensor pauses remain excluded from this retry.
The original PnPUtil command line was not retained, so its exact operation is
unconfirmed; this correction does not weaken driver-installation guards.
Local verification: 1,104 unit tests, compileall, runtime-manifest check, DLL
verification and preflight passed. After normal elevated shutdown/relaunch,
fresh repeated GPU reports showed thermally ready standby (driver curve) and
the B850 case controller remained active. This is startup/idle evidence;
gaming, sleep/resume and a real driver replacement were not exercised.

Settings interaction (2026-10-15): right-click no longer opens settings. The
header's left-click gear button remains the settings entry point; control help
and README instructions match it.

B850 no-reboot firmware follow-up (2026-10-14): three independent elevated
read paths agree that the HWM index/data ports at 0xA45/0xA46 return 0xFF:
strict successful PawnIO IOCTL replies, the installed signed Gigabyte driver,
and the board's GSA1 firmware WMI interface. Configuration ID 0x8696, LDN4
activation and base 0xA40 remain readable. This is a confirmed native-access
failure; the upstream cause remains unresolved, rather than a proven GCC
conflict or a guaranteed reboot fix. GCC remains running and Windows was not
restarted, as requested.

Firmware PCI method metadata reverses the device/function parameter names;
compensating for the actual AML packing validates AMD LPC identity 0x790E1022.
Both firmware ECAM and the installed driver still return all ones for legacy
decode/SPI offsets beyond 0x40; those values are not usable register baselines.
The documented AMD eSPI MMIO aperture at 0xFEC20000 is readable through GSA1.
Decode 0x001F1F06 includes enabled extended I/O window 4, base 0xA00 and size
128, which covers the HWM ports. Peripheral/VW/OOB channels are enabled
(slave configuration 0xE000000E); status 0x10000000 has command-complete only,
with no recorded no-response/timeout/bus-error bits. Thus an absent eSPI window
or disabled peripheral channel is excluded by fresh reads; no routing writes
are justified. Neither the installed SDK nor the inspected firmware interface
provided an alternate RPM reader or a validated non-reboot recovery operation.
The pending fan recovery journal is retained and restoration remains unconfirmed.

B850 native-access/coexistence audit (2026-10-14): the exact board's sensorless
IT8696E now marks its sample partial and requests the existing guarded LHM
reinitialization, at the existing 30-second interval. LHM's constructor can
return before creating sensors after an invalid vendor/configuration read;
ordinary Update calls cannot repair that object. Regressions exercise empty
and healthy controllers, other-board isolation, repeated failed opens followed
by recovery, resource cleanup and retained CPU/GPU readings. GCC is left running;
its background presence is not treated as a case-controller ownership conflict.

Elevated standalone reads on the same boot confirmed configuration ID 0x8696,
active LDN4 and base 0xA40, but HWM address/data ports returned 0xFF, including
vendor register 0x58 and control register 0x13. No fan commands were sent by
these diagnostics. The existing signed bridge found no MMIO mapping and refused
its addressing-state query as unsupported. An exploratory PCI-port query was
discarded: those ports are blacklisted by the signed LpcIO module, so its output
is not chipset-configuration evidence. Chipset decode/firmware/native-driver
causes remain candidates; neither GCC causation nor recovery by a Windows reboot
has been demonstrated. The original restart recommendation is a recovery step,
not a proven root cause or guaranteed fix. No chipset registers, driver, DLL,
BIOS setting or other monitoring application were changed.

B850 interrupted-start recovery follow-up (2026-10-14): register restoration no
longer requires published RPM sensors before the first LHM update. Exact board,
chip, native backend and journal/register checks remain mandatory; new takeover
still needs two running samples. Missing startup tachs wait within the existing
deadline; duplicate or wrong identities fail closed. Pending recovery with an
empty baseline now explicitly advises restarting Windows rather than a generic
controller error. Regression coverage includes recovery before tach discovery.
On this boot, elevated read-only LHM checks still found an IT8696E with no
sensors and invalid native register readback; software handback is unconfirmed
and the recovery journal is retained. Fresh successful native readback is
required before claiming hardware recovery; reboot recovery is unverified.
All four independent disk probes
succeeded, including disk 0 in 0.12 seconds; the earlier disk timeout's cause
was not established. No DLLs, drivers, firmware settings or protection checks
were changed.

Live watchdog/storage follow-up (2026-10-12):
[recovery repair and evidence](docs/watchdog-and-probe-recovery-2026-10-12.md).
Untouched GPU standby now qualifies for one watchdog retry with explicit
no-write/no-pending proof; it is not mislabeled as restored. Delayed queued probe
replies retain their completion age and deadline checks. UI refresh failures
reschedule polling, manual Scheduler operations run off-thread, and in-app notices
keep Tk timers live. The original ordinary-use pause was not independently traced;
duration/stage diagnostics now make another interruption more informative.

Independent second control audit (2026-10-12):
[review and verification](docs/control-review-and-audit-2026-10-12.md).
Confirmed accumulated-error 100% latching and reversed-clock acceleration were
removed. Targets now add bounded proportional correction; hardware fallback,
independent emergency limits and gentle ordinary acceleration remain. B850 now
uses a measured startup estimate, handles all available GPU tachs, pins runtime
sources, restores before normal sensor readiness and returns channels to automatic
mode before retained-duty writes. Identical complete LHM samples are no longer
claimed stale. Interrupted profile commits reconcile durable evidence. Partial UI
startup and storage-process setup/teardown unwind owned resources; malformed
signature JSON fails verification. Physical thermal/acoustic transfer functions,
silent native cache freshness and unexposed individual GPU fans remain limits.

Deep fan/runtime/storage audit (2026-10-12): confirmed failures are closed in the
[current audit](docs/audit-and-gpu-targets-2026-10-12.md), including scoped B850
ownership, PWM masking stopped tachs, shared-journal ownership/partial recovery,
activation profile/preferences, storage queued replies and independent capacity,
config-save concurrency, setup aliases/bounded downloads/PnP guards and ADLX
validation. GPU control now combines a separately installed balanced driver
profile with bounded temperature feedback and gradual ordinary acceleration;
independent full-temperature/failure protection remains immediate. Fresh exact-board
ADLX activity is preferred over generation-less D3D counters when available.

B850 reboot follow-up (2026-10-12): an early SYS1 zero stopped activation before
commands although all three physical fans were reported spinning. Twenty later
read-only samples showed positive SYS1/2/3 RPM. Startup now waits within its
existing 60-second deadline for two consecutive running samples; persistent zero,
invalid values and wrong identities still fail closed. Regressions reproduce the
old premature failure and retained no-write timeout. Normal elevated restart
restored AUTO SYS1/2/3 at full speed while assisting the user-operated GPU load.
The next physical reboot with this fix remains an informational follow-up.

GPU utilization follow-up: this boot's copied Windows D3D percentage disagreed
with driver activity/power at idle and with ADLX during gaming. A separate Windows
GPU Engine query also returned values above 100%. These counters are not evidence
of thermal load. Temperature evaluation uses the exact GPU's fresh ADLX readings,
board power and clocks. The display now uses validated fresh ADLX activity from
the exact selected GPU when its worker is available. The D3D provider anomaly and
upstream early-return handle cleanup remain separately documented candidates;
their runtime cause has not been established and DLLs were not changed.

B850 SYS1/2/3 assistance now uses the greater CPU/GPU fan fraction and independent
thermal protection, with exact register ownership, interrupted-write recovery and
verified motherboard handback. CPU/CPU OPT/SYS4 and PWM/DC selection stay outside
its write allowlist. Background GCC alone is allowed with per-poll register checks;
other monitoring tools and installers still pause control. Matched-load cooling,
physical airflow direction, sleep/resume, actual reboot/login and long-duration
GCC coexistence remain hardware follow-ups. Equal duty/RPM fractions do not prove
equal airflow. The explicit local response check and results are recorded in
`docs/b850-case-cooling-2026-10-12.md`.

GPU policy review: `docs/control-review-and-audit-2026-10-12.md`. Cooling-first
full-speed limits are retained; soft correction no longer integrates error. Complete-worker tests
now explicitly cover Hotspot 40 idle, the full-speed boundary and cooling handback.
Matched-load noise/temperature optimization and physical response remain open.

Warning-panel follow-up: technical autostart and volume errors are summarized
on the desktop and retained in Copy diagnostics. Disabled case control and normal
startup waiting no longer produce warnings; pending restoration remains visible.
Failed autostart inspection is retried read-only, clearing on success. Local
regressions cover these transitions. Elevated acceptance: after normal restart,
check the compact panel, click-to-copy details, and disappearance after recovery.

Background GCC and independent GPU ownership policy:
`docs/gpu-gcc-coexistence-2026-10-03.md`. GCC alone no longer suppresses GPU
assistance; B550 case/EC control remains exclusive, with known installer blocking and
GPU curve/Zero RPM conflict checks retained. Extended simultaneous operation
remains a hardware follow-up.
ADLX GPU selection now permits an integrated AMD adapter beside exactly one
matching Gigabyte RX 7900 XT; order, missing/duplicate matches and identity
changes are covered by regressions. The read-only native probe matched the
expected board and obtained all three temperatures without changing settings.

Controller freshness and native-storage access ordering audit:
`docs/controller-freshness-audit-2026-10-03.md`. Reordered tachometer records and
gaps between GPU samples no longer earn cooling credit. SMART construction now
rechecks hardware access after Windows queries. Five additional regressions bring
local coverage to 914 passing tests; physical restoration and coexistence checks
below remain open.

Storage discovery is isolated per Windows-confirmed physical disk with bounded
probes. Windows temperature properties and direct SMART identification restored
all four mounted devices; global LHM storage discovery and Windows disk inventory
queries can block on the affected machine. The fallback uses internal APIs of the
integrity-verified pinned DiskInfoToolkit bundle, so runtime upgrades must revalidate
constructor and SMART conversion compatibility. Expired readings and failures stay
visible. Fixed-volume enumeration, filesystem capacity and extent queries now
have the same bounded disposable-process isolation, with a separate owner thread
and per-volume freshness. CPU/GPU samples do not perform filesystem calls.
Repair evidence and remaining acceptance boundaries:
`docs/volume-isolation-audit-2026-10-03.md`.

Worker status/recovery JSON rejects oversized documents and excessive parser
nesting. Independent volume rows and pressure warnings remain visible during
native initialization and hardware-access pauses. Regression and native Tk
evidence: `docs/worker-json-and-startup-audit-2026-10-03.md`.

## P2 - Disk 0 SATA I/O failures on the affected machine

System events 129 (storahci controller resets) and 153 (Disk 0 I/O retries) were
already present before this repair. The unmounted WD40EZAZ can expose model and
temperature through Windows properties while direct SMART identification times out.
The underlying device/controller/cable cause is unconfirmed. The app now bounds
probes and backs off failed disks for five minutes; it does not repair Windows
storage I/O, reconfigure the controller or alter disk contents.

Sensor source/display audit and verified corrections:
`docs/sensor-source-audit-2026-09-24.md`. The exact AMD PMLOG-versus-Windows activity
accounting difference remains unresolved; the main GPU percentage now uses Windows
engines, and a fallback driver percentage is explicitly marked. Driver-reported
clocks have not been validated against synchronized independent measurements.

Monitoring coexistence policy and regression coverage:
`docs/shared-monitoring-2026-09-24.md`. Extended simultaneous operation and
third-party tuning changes remain hardware follow-ups; process detection does
not guarantee coordination with every driver or unknown controller.

Codex-owned launcher lifetime: `run_as_admin.bat` now relays starts from a Codex
process through the verified Windows autostart task. The task setting is not
changed. Launches without an independent task stop with an explanation; check
the actual overlay after closing Codex to verify host-specific process lifetime.

This file contains only confirmed open tasks. Closed findings are removed rather
than retained as session history. Priority reflects impact and likelihood, not
change size.

The manual and hardware checks below are informational follow-up tasks, not
publication conditions. A release request produces a normal `X.Y.Z` release,
without candidates or prereleases; open reboot/gaming/Explorer/multi-monitor checks
do not delay it. The publication rule is defined in `AGENTS.md` and supersedes
previous conditions in historical reports. Check statuses are retained as factual
records.

Full audit results, fixes, and the scope of release 1.2.0-rc.4:
`docs/audit-1.2.0-rc.4.md`.
Autostart and sensor-readiness waiting checks in 1.2.0-rc.5:
`docs/immediate-startup-2026-09-06.md`.
Verified fixes for sensors, controller reports, and commissioning:
`docs/audit-cleanup-2026-09-08.md`. This software audit does not close the hardware
checks listed below.

## P2 - Extended operation after a low-space failure

Low-space recovery follow-up: a controller reported `ENOSPC` while C: was nearly
full. Automated fault injection now covers full and compact terminal-report
failures, preserving verified restoration or explicitly leaving it unknown.
These tests do not simulate a physically full system volume. Extended operation
after the user frees disk space remains to be checked; do not fill C: deliberately
or treat successful report publication as evidence of hardware restoration.

Storage display follow-up: compare the C:/D: capacity rows with Windows after a
large file copy or deletion, allowing the 30-second refresh interval. Compact rows
join temperature and fullness only by confirmed physical disk number. Unknown or
spanned mappings retain separate readings. Physical hot-plug and unusual storage
topologies remain follow-ups; fake regressions cover unknown/ambiguous identities,
reordered devices, duplicate models, and multiple partitions.

## P2 - Physical reboot/login acceptance

The new audit removed the forced prolonged GPU test at 100% during normal startup
and repeated case-fan calibration when saved channels match. A cold GPU now stays
under driver control. Checks and manual scenarios:
`docs/startup-fan-audit-2026-09-06.md`. The next actual boot/login, noise, and GPU
transitions from standby to assistance and back remain untested.

After an actual reboot, the user confirmed failure of case-fan automation in rc.5:
SYS1 was absent from the controller's LHM instance for all 60 seconds, while the
main monitor displayed its RPM. rc.6 added bounded rediscovery of a completely
empty motherboard; testing the next physical reboot remains open. Cause, fix
scope, and checks: `docs/startup-rediscovery-1.2.0-rc.6.md`.

Check startup after a real Windows restart and user sign-in: the `HWMonitorOverlay`
task starts the launcher without a configured delay and requests UAC. After
approval, the window should appear without waiting for Task Scheduler; controllers
wait up to 60 seconds for temporarily unavailable readings without sending new fan
commands. Once sensors are ready, the case-fan controller checks the current
command; full-speed operation is tested only if suitable calibration is missing
or an explicit check is requested. On a cold card, the GPU shows readiness and
leaves control with the driver. Task Scheduler registration and classification,
the launcher, and an ordinary restart are checked separately; they do not replace
a physical reboot. The current installation from a mutable checkout does not
support silent boot before user sign-in.

## P1 - Cause of the complete Windows freeze remains unknown

After the forced reboot on 2026-09-06, the event log contains Kernel-Power 41 with
BugcheckCode 0. There is no newly confirmed crash dump. Ryzen Master installation
errors before the event and bthmodem.sys corruption require a separate Windows
investigation; coincident timing does not establish causation. Details and evidence
limitations: `docs/freeze-and-fan-ownership-2026-09-06.md`.

Independent HeatMap risks were fixed: SYS4 no longer takes over the shared EC based
on a one-off Pump 100% reading; known competing programs pause sensors; Copy
diagnostics does not open an additional monitor. Ryzen Master detection recognizes
`AMD Ryzen Master.exe` from the official MSI, including spaces; a separate
regression checks that control access is blocked. Ambiguous `gcc.exe` detection
uses compiler support files to distinguish a toolchain from Gigabyte Control Center.
GCC and Ryzen Master, separately or together, permit read-only monitoring
with automatic fan control paused
and DDR5 SPD inventory disabled; RAM usage comes from Windows. Other known
conflicts still pause sensors. A 2026-09-23 clr.dll crash coincided with AMD display
driver installation and device restarts at 20:35:44 in setupapi.dev.log. That event
does not establish GCC concurrency as its cause. Replacing a live GPU driver
remains a native-monitor lifecycle risk; close HeatMap before driver installation.
A changed motherboard retires the B550 case-fan profile before worker startup.
A temporarily sensorless secondary display adapter no longer forces repeated
LHM reopening when the RX 7900 XT has valid readings.
The new board's generic IT8696E fan channels 0/4 are mapped to CPU/CPU OPT only
for the exact B850 AORUS ELITE WIFI7 ICE identity, matching its GCC profile.
Repair evidence, the concurrent-GCC smoke check, and the verified correction
of GPU load to Windows busiest-engine utilization:
`docs/b850-sensor-repair-2026-09-23.md`.
Conflict pauses preserve the cause and label cached diagnostics as historical.
Automated tests do not prove that
the kernel freeze is resolved; extended stability testing remains open.

## P2 - Extended RX 7900 XT fan-control testing

Check extended operation of both controllers after the watchdog fix: keep the menu
and CPU reference dialog open for more than 15 seconds, close them, and check for
fresh case/GPU statuses. After normal sleep/resume, check restoration of control
and the single automatic recovery attempt following a confirmed timeout. Copy
diagnostics must retain the original stop reason; OFF → ON must remain available
after a repeat failure. Do not suspend or kill hardware processes for this test.
Controller warnings must remain visible in the health panel without a separate
Mode row; detailed state remains in Cooling status and policy and Copy diagnostics.
Automated fake regressions check timeouts, restart boundaries, and diagnostic
retention, but do not replace sleep, sustained-load, and driver-hang checks.

Conflict diagnostics now retain expected and read-back settings. Manual OFF → ON
accepts an external curve/Zero RPM after checks and archives the conflict journal;
autostart keeps the lockout. Automated regressions cover this path. In the updated
elevated overlay, check re-enabling after a conflict, cold standby, and a normal
return after gaming load. A short background run of the new worker confirmed five
fresh standby reports without new commands. ADLX reinitialization was fixed: the
DLL is unloaded after Terminate; three consecutive read/close cycles passed on
this PC. New cancellation, external-change, and stale-measurement regressions:
`docs/reliability-1.2.0-rc.7.md`.

A separate ADLX GPU controller was added with independent Core/Hotspot/Memory
curves and full-speed cooling from Hotspot 90°C. A short hardware test confirmed
100% and 55% commands, RPM changes, and exact restoration of the original curve/Zero
RPM. Extended gaming, noise, and temperature comparison under identical loads are
still needed. Responses to synthetic temperatures are covered by tests; there is
no need to deliberately heat the card to these thresholds. Details:
`docs/gpu-fans-2026-09-06.md`. GPU fan percentage now uses the shared 80%/95% color
thresholds separately from RPM; speed color alone does not trigger a temperature
alert.

## P2 - Extended testing of the shared four-case-fan profile

The SYS1/SYS2/SYS4/SYS5 profile with unused SYS6 has now been tested on this PC:
SYS4 rose from 666 to 1188 RPM, restoration of the actual EC mode and original
registers passed, and all four channels follow the shared curve after restart.
The new signed module and hardware-test boundaries are described in
`docs/shared-fans-2026-09-06.md`. Historical limitations of the previous LHM path:
`docs/airflow-audit-2026-09-06.md`.

Extended stability, noise, physical airflow direction, and GPU Core/Hotspot
comparison at identical power and GPU fan speeds remain open. A short RPM check
does not prove that overheating is resolved, establish the original cause of the
Windows freeze, or demonstrate recovery from a kernel hang/forced controller
termination.

## P2 - Check GPU temperatures and new indicators after restart

Files: `overlay.py`, `tests/test_temperature_policy.py`; thresholds: `README.md`.

Background GPU reads through the current LHM without elevation produced three
consecutive RX 7900 XT samples: Core 54°C, Memory 74°C, Hotspot 108/109/110°C. This
confirms the reading's source, but is not an independent check of driver accuracy
or a cooling-system diagnosis. The 54–56°C Hotspot–Core difference requires checking.

The parser, separate rows and alert thresholds, unavailable/stale values, peaks,
and Samsung exceptions are covered by automated regressions. The updated elevated
overlay and automatic cooling were started and checked outside a game; hardware
results are recorded in `docs/thermal-control-validation-2026-09-05.md`. Comparison
with Adrenalin, sound checks under gaming load, and Explorer/DPI acceptance remain
open. A controlled comparison of gaming temperatures has not yet been performed.
A separate check of the controller's file-exchange fix is described in
`docs/fan-status-io-fix-2026-09-05.md`; it does not replace the temperature comparison.
`docs/audit-1.1.0.md` records additional sensor/warning regressions and hidden native
Tk scrolling/scaling checks; physical acceptance remains open. The main VRAM row
now includes GB and percentage; the empty footer collapses. Native Tk checks cover
the panel returning when a warning appears and retention of runtime errors; check
the updated compact layout in the gaming smoke test. Color, CPU percentage, and
grouped-layout audit: `docs/display-audit-2026-09-05.md`. Activity highlighting is
now neutral; RPM/reference estimates must be distinguished from measured duty.

Manual Windows smoke check after gaming:

- Close the old HeatMap and start the updated version normally with
  `run_as_admin.bat` and UAC.
- Compare G.CORE and HOTSPOT with Current/Junction in Adrenalin at the same moment;
  check V.TEMP, unavailable sensors shown as `--`, absence of clipped labels and
  overlaps at the current DPI, Details OFF/ON, and placement of an expanded window
  near the edge.
- Under ordinary gaming load, check the separate red HOTSPOT indicator and sound
  with Alerts enabled; do not deliberately cause overheating. Check Alerts OFF
  and Reset peaks. Freshness/errors are safely covered by fake tests; do not
  disable the driver.
- If Hotspot again remains at 108–110°C with a substantially cooler Core, reduce
  load, compare Adrenalin readings, and check cooling. No temperature reduction
  from the display fix is claimed.

## P2 - Confirm fixed widget placement in the real Windows shell

Files: `overlay.py`, `tests/test_layout_audit.py`; behavior: `README.md`.

Current model: an independent window remains on the desktop beneath applications.
`Raise on edge` temporarily changes only the window order. The widget's position,
size, and monitor are preserved; animation, movement to the edge, hiding because
an application covers it, and WorkerW attachment are no longer used. `Always on top`
enables persistent placement above applications.

Check the following after starting the updated HeatMap:

- Hover at the right edge, repeatedly leave and return, and click readings, the
  title, and the menu: the widget stays in place, returns beneath applications
  after departure, and the active application retains focus.
- Maximize and minimize applications, Show Desktop/Win+D, hover and repeatedly
  click the button beside the clock, Raise on edge OFF, and an auto-hide taskbar.
  The widget remains accessible on an unobstructed desktop.
- The menu, CPU reference entry, dragging, and a lost ButtonRelease during shell
  changes; Details and sensor errors near the bottom edge. Position adjustment
  is allowed only if an expanded window extends beyond the work area.
- Close during sensor opening and a modal dialog, then restart normally. Placement
  beneath icons is not guaranteed: check that the widget does not cover needed
  icons at the chosen position.

Automated regressions and transparent off-screen Tk/Win32 windows check geometry
and lifecycle, but do not replace these Explorer, real-rendering, and gaming
checks. Historical reports of sliding Peek refer to the previous behavior model
and do not establish acceptance of the new one. Verification of independent
cooling, formulas, and restart for the current model is described in
`docs/cooling-and-desktop-1.2.0-rc.2.md`.

## P3 - Complete the physical multi-monitor and mixed-DPI acceptance matrix

File: `overlay.py`.

Geometry tests use predefined monitor areas. The available physical test setup
has one monitor; negative coordinates, staggered layouts, mixed DPI, and
disconnect/reconnect require a second display or a separate Windows VM.

Checks:

- Two monitors: horizontal, staggered, negative origin, mixed 100%/150% scaling;
  drag, save, and restart on each.
- Hovering over the outer right edge of any monitor while in an application raises
  the widget at its existing position. Internal seams and the taskbar do not
  trigger it. The cursor's monitor does not change the widget's size or position
  on another monitor.
- Disconnect and reconnect a monitor in normal mode, while temporarily raised,
  and with Always on top. The window remains accessible within a connected
  monitor's work area; the saved position reflects the new placement.
- Details and warnings at different DPI settings: labels remain accessible through
  wrapping and scrolling, the title and close button are visible, and the window
  does not cover the taskbar.
- Check compact width, separate CPU/SYS percentages, and FW beside SYS4: actual
  0/100%, unknown --%, estimated ~%, a long warning, and sensor loss. Native Tk
  regressions cover the absence of clipping at 100/150/200%.
- Menu state matches actual window order after toggles. Check top/right taskbars
  only on Windows/VM setups with supported layouts; do not change the shell
  registry for testing.

Native Tk regressions cover drive rows disappearing and returning, removal of
their stale empty height, and the complete long warning panel at 100/150/200%
scaling. They do not replace the physical display matrix above.

## P3 - Worker-path duplication between case and GPU fan control

`case_fans.py` and `gpu_fans.py` evolved in parallel and now duplicate the
worker-status validator (~80 vs ~58 lines), the 15-second heartbeat protocol,
the terminal-report payload shape, the atomic JSON journal file layer, the
ramp-down logic (`FanRamp` vs `GpuRamp` with already-diverged constants), and
the commissioning wait loops (`enable_case_fans.py` vs
`tools/commission_gpu_fans.py`). Fixes to spoofing/stale bounds, timeouts, or
restore sequencing must currently be applied in two places; the ramp holds
(15s vs 10s) and deadbands have already diverged.

Do not refactor these paths cosmetically: each dedup must preserve exact
behavior and ride on the existing paired suites (`test_controller_heartbeat`,
`test_fan_commission_audit`, `test_fan_activation`, `test_thermal_advisor`,
`test_airflow_assist`, `test_gpu_conflict_recovery`, `test_shared_fans`).
Single-sourced already: `fan_common` (heartbeat, status validation, terminal
compact keys, commission wait-loop, journal envelope), status-file helpers,
`thermal_policy.GPU_GAP_*` gap constants, shared-EC recovery journal format.
Deliberately NOT unified: `FanRamp` vs `GpuRamp` (floors 60/30, holds 15/10,
deadband 3/0, int/fractional vs float math are per-hardware policy; the
classes cross-reference each other and frozen test tables pin the numbers).

## Parking - Update LibreHardwareMonitor and PawnIO atomically to the next bundle

Promote when: a specific hardware fix/security reason warrants an upgrade, or the
scheduled latest-compatible/provenance lane identifies an incompatibility.

Why not now:

- LibreHardwareMonitor and PawnIO form a compatible pair; updating only one
  component independently reintroduces the original failure mode after a package
  update.
- The next bundle must update the LHM DLL graph, PawnIO installer metadata,
  runtime lock, manifest, sensor fixtures, and licenses in one reviewable change.

Required checks when promoted:

- Clean-room restore and staged CLR smoke checks pass before the runtime swap.
- Failed downloads/hash checks/type imports preserve the previous working bundle.
- After driver installation/reboot, an elevated hardware smoke check confirms
  CPU, GPU, RAM, storage, and available fan sensors.

## Recommended next work bundles

1. **Fixed placement:** hovering, Show Desktop, and returning to an application
   after restart.
2. **Desktop acceptance:** the physical multi-monitor/mixed-DPI matrix and only
   reproducible follow-up fixes.
