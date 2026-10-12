# B850 SYS1/SYS2/SYS3 assistance

## Policy

Three separately connected case fans assist the greater CPU/CPU OPT or GPU fan
fraction. Actual controller duty has priority. An unavailable duty uses RPM /
saved reference: the current installation has a 2000 RPM CPU rated reference and
3442 RPM saved GPU full-speed reference. The UI labels these estimates. Different
fan designs and PWM/DC responses mean matching percentages does not establish
matching airflow or physical power.

Independent thermal requests retain priority: CPU 70°C requests at least 85%,
reaching 100% at 80°C. GPU requests use the existing GPU curves once Core reaches
60°C or Hotspot/memory reaches 78°C, reaching full speed at 75/90/90°C. GPU Zero
RPM is normal and does not stop case assistance for the CPU. The commissioned
minimum is 60%. Normal decreases retain the existing 15-second hold, three-point
deadband and two percentage points/second rate. Normal increases are limited to
10 percentage points/second; full-temperature/missing/stale/stopped-feedback
protection bypasses that limit. Ordinary startup does not repeat full-speed testing.

## Ownership and recovery

The exact motherboard name, IT8696E identifier, chip report/base address, three
tachometer identities and positive readings must match. The pinned LHM 0.9.5
`IT87XX.cs` register layout is used through a narrow adapter with the same ISA
bus mutex. The manifest is checked before any controller access. No new runtime,
DLL, driver or supplemental module is installed.

Only SYS1/2/3 mode/duty registers are writable. The original automatic-mode bit
is cleared without changing PWM/DC selection. CPU, CPU OPT, SYS4 and the main
output-enable register are read and verified, never written. This chip has no
LHM shared Gigabyte EC adapter; detecting one blocks takeover.

The original registers and each intended command are saved atomically before
writes. A partial write or interrupted process can be recovered at the next
start only when all current registers match known original/intended states.
Malformed journals, foreign identity or external changes refuse recovery and
preserve the journal/settings. Normal OFF/exit restores the exact original
register snapshot and removes recovery evidence only after readback succeeds.
Software cannot guarantee recovery while Windows or the native driver is hung.

Background GCC may coexist: both the main sensor owner and worker still block
other monitoring tools and live driver installers. B850 ownership is checked
before each command and every poll. Change motherboard fan settings with HeatMap
case control OFF. A conflicting active GCC change stops assistance and preserves
the observed external settings. Still-owned case channels are restored without
rewriting changed CPU/CPU OPT/SYS4 settings. A changed case channel may leave
restoration unconfirmed, requiring
manual firmware/GCC restoration and review of the recovery journal.

## Local hardware evidence

The elevated read-only discovery on 2026-10-12 found SYS1/2/3 rotating at roughly
686/749/665 RPM, with CPU and CPU OPT on separate channels. The explicit
commissioning check then measured 1243/1250/1208 RPM at a verified 100% command.
At 60%, four fresh sustained samples settled to about 833/865/805 RPM. All three
fans responded, and exact motherboard restoration was confirmed with no errors
before ON was saved. CPU/GPU were cool during this test; no gaming or deliberate
thermal stress was used.

Raw local evidence: `%LOCALAPPDATA%/HeatMap/b850-case-readonly-20261012.json`,
`b850-case-activation.json`, and its referenced configuration backup. These are
machine-specific observations; they do not establish improved gaming temperature
or noise, physical fan orientation, long-duration stability, sleep/resume or a
physical reboot/login result.

## Verification and manual follow-up

Regressions exercise CPU/GPU arbitration, actual duty versus RPM estimates, each
full-speed boundary, missing/invalid readings, exact target selection, untouched
outputs, partial writes, interrupted-session recovery, malformed journals,
external changes, profile separation and GCC scope restrictions.

Run the local unittest suite, compileall, runtime manifest consistency,
`setup.py --verify`, `setup.py --preflight`, and `git diff --check` before publishing.
All 956 local tests passed; compilation, manifest consistency, integrity,
preflight and whitespace checks also passed. GitHub-hosted execution was not requested.

After a normal elevated restart, five fresh reports showed CPU-driven assistance
at 72% while the cool RX 7900 XT remained in driver standby with zero RPM and no
GPU command. A second restart exercised the final identity checks and restored
the previous case owner successfully. Live evidence is in
`%LOCALAPPDATA%/HeatMap/b850-case-live-20261012.json` and `b850-case-restart.json`.
The launcher commissioning helper also now recognizes a checkout accessed through
the existing C: to F: junction; it still closes only the same overlay file.

For normal elevated acceptance, check fresh `AUTO …% · SYS 1/2/3` reports and
measured RPM after restart, then ordinary CPU/GPU workload transitions. In a
later user-owned session, check OFF handback, sleep/resume and a real reboot/login.
For cooling comparisons use the same workload, ambient conditions and fan
settings with the case open and closed. The short response test is not that
comparison.

## Reboot startup follow-up

After the user's 2026-10-12 reboot and GPU repaste, the initial controller sample
reported SYS1 at zero and stopped before takeover. The user confirmed all three
fans physically rotating, and a later 20-sample elevated read-only probe measured
positive readings on all three exact headers. This establishes a transient
startup-reading failure, without proving its firmware/native-driver cause.

The startup-only selector now retries zero/sub-200 readings within the existing
60-second deadline, then requires two consecutive complete running samples.
The strict post-readiness selector and runtime stop checks remain unchanged.
Persistent startup zero, invalid readings, changed channel identity, conflicts
and timeout still send no new fan commands. The old early-zero failure is
reproduced by a whole-worker regression; its recovery and timeout paths now pass.
All 958 local tests, compilation, runtime integrity and preflight checks passed.

After normal elevated restart during the user-operated Witcher workload, SYS1/2/3
reported approximately 1227/1252/1195 RPM with a 100% read-back command and reason
`GPU fans`. This was synchronization with the GPU's actual driver-controlled
fan percentage, independent of HeatMap GPU thermal intervention. A physical
reboot with the updated readiness behavior has not yet been performed.

Sources: the exact installed GCC `Fan/Profile-0.xml` header ordering and the
[pinned LHM backend](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/blob/v0.9.5/LibreHardwareMonitorLib/Hardware/Motherboard/Lpc/IT87XX.cs).
Case airflow guidance: [Noctua fundamentals](https://www.noctua.at/en/support/faqs/airflow-guide-fundamentals).
