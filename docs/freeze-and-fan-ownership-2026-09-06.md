# Freeze investigation and fan ownership — 2026-09-06

## Evidence and limits

Times below are local Europe/Dublin (UTC+1). The user reported a completely frozen
display/input followed by a forced reboot. System event 41 at 00:49:49 has
BugcheckCode 0 and WHEABootErrorCount 0. Event 6008 records the previous unexpected
shutdown at 00:46:49; this is not a precise freeze-onset timestamp. No matching new
Minidump or MEMORY.DMP was found. The repair workflow's elevated dump inventory
also showed no new LiveKernelReports dump (newest dated August 28). No relevant
pre-event WHEA, Display or storage error
was found in the inspected window. This does not rule out those subsystems.

Microsoft explains that [event 41 with zero error codes](https://learn.microsoft.com/en-us/troubleshoot/windows-client/performance/event-id-41-restart)
can accompany a hard hang and cannot identify its cause by itself.

CPU-Z driver registration occurred at 00:39:57. Ryzen Master uninstall/install
operations at 00:43–00:44 reported failures, a service-stop failure and a restart
requirement. These operations belonged to the separate Windows optimization task.
The installed graphics driver predates this incident. A WmiPrvSE heap-corruption
event at 00:49:59 occurred after reboot and is not evidence of the initial trigger.
CBS also reported corruption of bthmodem.sys. Neither temporal correlation nor
that corruption establishes the cause of this freeze.

The last retained pre-reboot HeatMap status controlled SYS1/2 only, with CPU 57°C,
GPU 34°C, hotspot 36°C and memory 52°C. It is a prior sample, not telemetry at the
instant of the hang. After reboot, the old selection policy included SYS4 because
both pump readings happened to be 100%. HeatMap was then closed normally for the
audit; its worker confirmed restoring all three acquired channels with no errors.

## Confirmed independent HeatMap fixes

The pinned LibreHardwareMonitor v0.9.5 source at commit
`30395c48a7f912894a7c392db8c11e1b97859658` maps SYS1/2 to IT8688E, while SYS4 and
pump-capable headers share IT8792E. Its `IT87XX.SetControl` disables the shared
Gigabyte controller, and `EcioPortGigabyteController.Enable` changes one global
EC mode rather than a per-header firmware mode. A momentary Pump 100% therefore
does not establish a fixed BIOS setting or permit independent SYS4 ownership.
The revised profile only acquires SYS1/2; SYS4 and pumps remain firmware-owned.

Known competing hardware-tool process names are checked before opening hardware
and during polling. A conflict stops reads, requests normal fan restoration and
latches the overlay until restart. This is a conservative process-name exclusion,
not a driver-level lock: renamed/unlisted tools, resident drivers and a process
starting between checks remain outside its guarantees. Ordinary Adrenalin is not
excluded. No external process is terminated.

Copy diagnostics uses the sensor owner's cached inventory with an age indication.
It no longer opens a third concurrent LHM Computer or closes such a diagnostic
instance while the main monitor and fan controller are active.

These changes remove specific risks. They do not prove that HeatMap caused the
freeze, or that a kernel/driver/hardware hang has been repaired.

## Windows repair and acceptance

An existing Windows repair workflow was already running from
`Documents/Codex/2026-09-06/windows-optimization/deep-pass`; no duplicate repair was
started. DISM RestoreHealth completed successfully at 01:13:16. SFC reported that
it repaired corrupted files at 01:14:40; the subsequent verify-only run reported
no integrity violations at 01:15:50. All three commands returned zero, and their
text results were checked. This proves system-file repair, not the cause of the
freeze. No BIOS change, driver installation, overclock change or forced reboot was
performed by this HeatMap audit.

The updated local overlay was started at approximately 01:20 after verification
and repair completed. Its worker passed commissioning and reported `active`,
SYS1/SYS2 controlled, SYS4 firmware-owned, with fresh telemetry and verified RPM
references. The source SHA256 of the launched overlay was
`6abe1b371cf804717ab883cce3937f2adcf7d23d1a3c8b24e0a32614ec5445f7`.
This is local runtime evidence; these additional changes are not a new published
release. DLL verification, preflight, compileall and runtime-manifest drift checks
passed. Regression tests cover access conflicts before/during control, restoration,
diagnostics without additional hardware ownership and paused UI behavior.

Required physical follow-up: ordinary-use stability after Windows repair, real
Explorer/multi-monitor acceptance of the compact overlay, and temperature checks
under the user's normal workload. Synthetic tests and a short runtime check cannot
establish long-term system stability or physical airflow.
