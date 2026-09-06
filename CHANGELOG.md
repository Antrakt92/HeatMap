# Changelog

## 1.2.0-rc.5 — 2026-09-06

- Start at Windows login without the fixed 30-second task delay. Keep ordinary
  UAC, preserve disabled/absent startup preferences and refuse foreign tasks.
- Create the overlay before Task Scheduler inspection. Check and migrate startup
  in the background; show failures without blocking the UI or fan heartbeats.
- Wait up to 60 seconds for missing case/GPU sensor readings before takeover.
  Require exact channels, valid temperatures and fresh GPU metrics; reject
  malformed values, conflicts, unsupported devices and native cleanup failures.
- Show waiting before the first sensor sample, preserve pending GPU recovery
  ownership, and treat normal cancellation before takeover as a clean stop.
- Allow readiness and full-airflow checks within commissioning time budgets.
  Preserve the original startup failure only with explicit no-command evidence.
- Keep preview status pending physical reboot/login and extended hardware/UI
  acceptance. See `docs/immediate-startup-2026-09-06.md` for verified live results.

## 1.2.0-rc.4 — 2026-09-06

- Add an opt-in, verified four-case-fan profile for the commissioned B550 AORUS
  PRO AC: SYS1/SYS2/SYS4/SYS5, with the unused SYS6 held at full duty. Use a pinned,
  signed PawnIO EC module and verify actual shared-controller restoration.
- Add isolated RX 7900 XT fan control using the installed AMD ADLX runtime.
  Core, Hotspot and Memory demand cooling independently; Hotspot reaches 100%
  at 90°C. Retain a driver heat-response curve, freshness checks and slow ramp-down.
- Journal original GPU fan settings before writes; recover recognized interrupted
  sessions on next launch. Preserve external fan changes and report ownership
  conflicts. Attempt all native cleanup operations even after a release failure.
- Preload full duty before both case profiles enter software mode. Check shared
  output/manual modes and independently attempt EC and primary-channel restoration.
  Reject nonfinite control bounds and stale commissioning evidence.
- Color the GPU fan percentage with the existing 80%/95% intensity thresholds,
  independently from RPM. Show GPU startup failures before the first sensor sample.
  Warn when a previously running fan disappears from enumeration under heat;
  preserve its temperature scope and retire obsolete channels after GPU replacement.
  Remove obsolete CPU combined-format and config-loader wrappers.
- Preserve exclamation marks in launcher paths, fail closed on inaccessible
  runtime ownership checks, and report filesystem races during DLL verification.
  Repair native launcher/Task Scheduler integration scripts for Windows PowerShell 5.
- Keep this release a preview pending real reboot/login, extended cooling/noise,
  Explorer and physical multi-monitor acceptance. See `docs/audit-1.2.0-rc.4.md`.

## 1.2.0-rc.3 — 2026-09-06

- Retry missing startup fan sensors up to four reads on the same hardware instance,
  with cancellable half-second waits before any control command. Reject duplicates,
  foreign identities, stopped fans and unavailable control objects immediately.
- Distinguish startup refusal before fan commands from unconfirmed restoration;
  retain firmware ownership labels when startup explicitly never acquired control.
  Diagnostics now identify missing control/tachometer counts and exhausted retries.
- Preserve the original startup failure in the activation wizard when explicit
  status confirms that no control commands were sent; retain restoration gates.

- Add bounded airflow assistance from sustained temperature rises and confirmed
  stops of previously running firmware-owned case fans under thermal load.
  Keep the existing temperature curves, full-airflow safeguards and slow decline.
- Show all headers as SYS numbers, distinguish firmware/unknown ownership in every
  controller state, and warn when a previously running fan tachometer disappears.
- Include observed case-fan readings and base/policy demand in controller diagnostics.
- Verify primary fan-output mode bits before takeover and after restoration;
  refuse unsupported or unreadable modes instead of trusting duty readback alone.
- Keep SYS4 and pump channels under firmware ownership regardless of instantaneous
  pump duty: LHM's secondary-controller switch affects their shared EC mode.
- Pause sensor access and restore owned fans when a known competing hardware tool
  appears; require restart after resolving the conflict. Copy diagnostics uses the
  sensor owner's cached inventory instead of opening another hardware monitor.
- Remove the CPU fan `ref` suffix and show an independently colored percentage
  beside each CPU/case fan: measured channel duty first, verified RPM reference
  otherwise. Retain `~` for estimates and show `--%` when unavailable.
- Match motherboard duty by exact chip/channel identifiers; reject ambiguous
  controls and clear percentages with stale sensor data. Orange from 80% and red
  from 95% describe fan speed, without changing thermal alerts or fan commands.
- Reduce unused width and wrap warnings within the content width; retain scaling,
  scrolling and readable layouts at higher DPI. Shorten the firmware marker to FW.

## 1.2.0-rc.2 — 2026-09-06

- Keep SYS1/SYS2 automatic cooling available when the separate pump controller
  prevents SYS4 ownership. Verify exact chip/channel identities and retain pump
  guards, full-speed verification, heartbeat and firmware restoration.
- Show selected automatic channels and SYS4 firmware mode. Add a live, nonmodal
  Cooling status and policy window with requested/commanded airflow and its cause.
- Support verified RPM references for both approved channel sets and preserve
  existing channel calibration when moving between two and three channels.
- Make fan slowdown depend on elapsed time after the cooling delay, including
  fractional intervals and duplicate timestamps. Report the limiting temperature,
  fail to full airflow on malformed demands and reset gap warnings on GPU changes.

- Keep one persistent desktop window. Hovering an exposed right edge temporarily
  raises that same window above applications; leaving lowers it without moving,
  hiding, cloaking or reparenting it. Remove slide animations and coverage hiding.
- Preserve the widget's monitor and coordinates across edge hover and topmost
  toggles. Fit content at its existing position, clamping only when necessary.
- Rename the existing edge setting to Raise on edge while retaining its saved
  preference. Menus, settings dialogs and dragging keep the temporary raise active.

## 1.2.0-rc.1 — 2026-09-05

- Fix stale Peek timers, quick re-entry during slide-out, monitor-specific hover,
  and transient cursor-query failures. Leave six pixels of the application edge
  reachable beside the preview.
- Stop drag/animation conflicts, recover missing mouse-release events, ignore
  duplicate releases, and keep released positions inside the monitor work area.
- Refit Details immediately in Peek, retain error panels on-screen, and adapt
  long detail lines to narrow and subsequently wider work areas.
- Complete cleanup after destroyed windows or event-loop failures; hide the
  overlay before waiting for workers. Ignore modal results after shutdown and
  allow PawnIO preparation retries after a worker-launch failure.
- Add 30 lifecycle/layout/shutdown regressions. Real Explorer, gaming and
  physical multi-monitor/mixed-DPI acceptance remain open for this candidate.

- Keep the independent desktop fallback hidden and DWM-cloaked when an application
  fully covers its location. Finishing a Peek slide no longer remaps that window
  over a game; the next edge preview and Show Desktop can reveal it again.

- Prevent clicks on the overlay from activating Tk's native wrapper and taking
  foreground ownership from the game. Handle WM_MOUSEACTIVATE explicitly while
  preserving mouse delivery and normal activation of separate settings dialogs.

- Restore component identity colors on group headings and sensor labels: blue
  CPU, purple GPU, cyan RAM, orange disks and mint case cooling. Reading colors
  continue to express their independent health/activity policy.

- Group the dashboard into CPU, GPU, case cooling and memory/storage; expose
  grouped settings through a header button. Keep Peek open while using menus.
- Show CPU fan duty when available, otherwise an explicitly configured approximate
  RPM/reference percentage. Match CPU Optional control to its own header.
- Make activity and unrated auxiliary readings neutral; share capacity alarm
  thresholds with row colors. Cancel queued/second beeps after sound is muted.
- Stop periodic re-embedding of an already positioned desktop fallback. Disable
  DWM shell transitions and cloak the composed surface while changing position
  and window layer; add native DWM and fallback timer regressions.

- Show used/total GB beside VRAM percentage in the main view and label RAM
  capacity explicitly as GB. Collapse the warning panel when empty; keep sound
  state in the menu and reserve RAM/VRAM panel warnings for critical usage.

- Hide the native Peek window during its return to the saved desktop position;
  finish Tk geometry and opacity changes before desktop placement, then show it
  without raising it. Prevent visibility recovery from showing a pending return.
- Prepare Peek entry off-screen before restoring opacity, including a new Peek
  that interrupts the previous return. Add real off-screen Tk/Win32 regressions
  for animations, repeated transitions, saved position, z-order and focus.

## 1.1.0 — 2026-09-05

- Keep expanded metrics reachable with scrolling while the header and warning
  panel stay visible; wrap long disk names and fit the current/Peek work area.
- Monitor every reported GPU fan, pair primary RPM with the correct fan control,
  and ignore unrelated power controls. Detect persistent hot GPU fan stalls;
  CPU-only heat does not turn normal GPU Zero RPM into an alarm.
- Apply CPU fan stall warnings to CPU heat and color confirmed stalled fan rows
  red. Report missing previously available GPU temperatures and required airflow inputs.
- Show concise controller errors with restoration status, count omitted warnings,
  and preserve complete details in click-to-copy diagnostics. Muted sound remains
  labelled when temperatures are normal. Add a sensor guide to the menu.
- Discard invalid saved fan calibration with a configuration warning, allowing
  settings to remain saveable. Source ZIP tests explicitly skip the Git-index-only
  check while retaining local artifact integrity tests.
- Add native Tk layout tests and regressions for the verified findings. Hardware
  curves, runtime dependencies, drivers and autostart security are unchanged.

## 1.0.2 — 2026-09-05

- Fix intermittent Windows Access denied errors that stopped automatic case fan
  control when its status file was being read. Shared snapshot reads and native
  replacement avoid the original reader/writer conflict.
- Retry temporary Windows file contention with bounded delays. Keep the last
  verified report through brief read failures, with the existing PID and freshness
  checks; persistent write failures still restore original fan control.
- Add native Windows regressions for open readers, transient and permanent locks,
  concurrent status traffic, expired cached reports and restoration after a
  status publication failure. No fan curve, driver or dependency changes.

## 1.0.1 — 2026-09-05

- Accept the real worker process behind Windows virtualenv redirectors; a valid
  native report no longer fails because its PID differs from the launcher PID.
- Preserve terminal restore/error reports after shutdown instead of marking them
  stale after ten seconds. Reject malformed active-status data and report directory
  write failures without crashing the UI.
- Persist verified full-speed RPM for safe restart checks when fans are already
  spinning fast. Validate complete channel correspondence for response/restore.
- Distinguish a failed control operation from a failed restoration in activation
  diagnostics. Require verified RPM evidence before saving automatic activation.
- Real B550 AORUS PRO AC activation, command/RPM response, original-control
  restoration and subsequent automatic regulation verified; see the linked report
  in README. This does not establish gaming temperature improvement.


## 1.0.0 — 2026-09-05

- GPU Core, Hotspot, memory temperature and Hotspot–Core are separate primary rows.
  Each temperature has independent color and alert thresholds; persistent hotspot
  gaps produce an actionable warning even when the ordinary GPU temperature is low.
- CPU and GPU fans show measured RPM and, when available, explicitly labelled
  controller duty. Removed observed-maximum estimates and ambiguous CPU fan/control
  matching. Added CPU Optional and detected system fan RPM.
- Always-visible warnings cover temperature, persistent fan stalls under load,
  hotspot gaps and memory/disk capacity; muted sound is labelled.
- SSD primary/composite temperature takes priority over additional sensors, which
  remain visible in Details. Samsung 980 PRO / 860 EVO use distinct warning limits.
- Added opt-in B550 AORUS PRO AC case airflow control for SYS1/SYS2/SYS4, conservative
  CPU/GPU curves, full-speed/RPM verification, heartbeat, ownership checks and
  readback of restored controls. Unsupported boards/wiring are rejected.
- One-time `enable_case_fans.bat` verifies the hardware profile, enables alerts and
  existing consent-based autostart, and launches the new overlay.
- No dependency, driver, DLL or silent-elevation changes. Native control recovery
  cannot be guaranteed after killing its worker or a hung driver; see README.

Validation: automated regressions, runtime preflight/integrity checks and hidden Tk
layout checks. Live activation results are machine-specific; consult the activation
report and AIRFLOW status. Physical multi-monitor/Explorer acceptance remains in AUDIT.md.
