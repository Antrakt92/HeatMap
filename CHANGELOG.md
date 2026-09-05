# Changelog

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
