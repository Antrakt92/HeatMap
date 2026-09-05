# Case airflow acceptance — 2026-09-05

HeatMap 1.0.1 was activated on Gigabyte B550 AORUS PRO AC with Ryzen 5600X,
RX 7900 XT and the previously identified case fan channels. Helldivers 2 was not
running during this acceptance test. No BIOS, GPU tuning or driver changes were made.

## Measured response

| Channel | Initial RPM | Verified RPM at 100% |
|---------|-------------|---------------------|
| System Fan #1 | 801 | 1232 |
| System Fan #2 | 670 | 1152 |
| System Fan #4 | 690 | 1103 |

The control sensors reported 100%. The helper then stopped the worker normally.
Readback confirmed the original automatic modes on SYS1/SYS2 and 55% on SYS4;
`restore_confirmed=true`, `restore_errors=[]`. No command was issued to CPU, GPU
or SYS5/SYS6 pump-capable headers. The shared-controller 100% guard passed.

The new overlay subsequently launched a new worker. Fifteen fresh samples over
approximately thirty seconds remained active, with commands between 73% and 80%.
The last sample reported 77%, matching control readback, with RPM 1056/963/941.
This confirms normal automatic reduction as well as the initial full-speed response.

The configuration retained automatic case fans and enabled sound alerts.
The existing consent-based Windows autostart task was confirmed enabled.

## Findings fixed during acceptance

The first attempt correctly restored controls but its parent rejected the report:
the Windows virtualenv redirector PID differed from the native worker PID.
The client now validates the descendant process and preserves terminal reports.
A non-elevated native subprocess check also confirms the correct administrator-
required error reaches the parent through this virtualenv launcher.

Fast-restart checks now accept previously verified full-speed RPM as an alternative
to an additional 8% acceleration; no slowdown under high load is forced for calibration.

## Limits

The last temperatures in this non-game observation were CPU 63°C, GPU Core 50°C,
Hotspot 67°C and memory 68°C. They cannot be fairly compared with earlier Helldivers
readings at a different workload. A matched game scene/power comparison and an
independent Adrenalin Current/Junction comparison remain required before drawing
conclusions about GPU cooling contact or thermal paste.

Actual logon/reboot, physical multi-monitor/Explorer interactions and killing the
native control worker were not exercised. Recovery from a killed/hung native
worker is not guaranteed; this test does not replace the README recovery guidance.
