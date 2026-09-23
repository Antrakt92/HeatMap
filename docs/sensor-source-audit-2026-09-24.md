# Sensor source and display audit

## Scope and completion

- [x] Trace the AMD activity counter and compare it with Windows engines.
- [x] Inspect CPU, GPU, RAM, VRAM, tachometers, duty, disk mapping and units.
- [x] Reproduce source-selection failures before changing production behavior.
- [x] Fix confirmed selection/label errors and retain unrelated local work.
- [x] Run regressions, full tests, compilation, integrity and whitespace checks.
- [x] Restart the installed overlay and verify actual readings and Tk row text.

No dependency, DLL, driver, clock, voltage or fan-control settings were changed.
The disconnected case-fan profile remains disabled. These changes are local;
this audit did not publish a new commit or release.

## What the 35% meant, and what remains unknown

This is a software telemetry counter, not a separate physical temperature sensor.
LibreHardwareMonitor 0.9.5 maps AMD ADL `ADL_PMLOG_INFO_ACTIVITY_GFX` into its
`GPU Core` Load sensor. HeatMap previously preferred that sensor over Windows D3D
engine running-time counters, without identifying the source in the UI.

On the same RX 7900 XT, an actual sample gave AMD activity 35% and D3D 3D 5.44%.
Eight later samples gave Windows 5.06-5.41% and AMD activity 34-35%. The selected
Windows-engine reading and the real Tk label now agree at approximately 5%.
The LHM device report independently exposes PMLOG activity 34 and GFXCLK 1414;
legacy Overdrive activity APIs returned zero in that report. That confirms which
software counter was being displayed, not that all AMD APIs measure the same thing.

The public ADL documentation does not specify enough counter accounting or
normalization detail to explain the exact 35-to-5 ratio. There is no evidence of a
hidden process consuming the difference. A driver/firmware telemetry discrepancy
versus a different activity definition remains unresolved; neither is established
as the sole cause. We must not invent a conversion factor or claim Windows proves
the hardware is active for precisely the same fraction of cycles.

An independent, read-only ADLX probe on the explicitly selected RX 7900 XT gave
0-6% instantaneous usage. Core/hotspot/VRAM temperatures agreed with adjacent LHM
samples within 1 C, and RPM agreed. Clock samples differed, notably ADLX 43 MHz
versus adjacent LHM 1401 MHz initially, then low reported frequencies in both.
These unsynchronized samples do not establish which instantaneous clock was
correct. Frequency remains explicitly driver telemetry, not a load measurement.

## Confirmed defects and corrections

| Surface | Previous behavior | Corrected behavior |
| --- | --- | --- |
| GPU percentage | Preferred AMD activity; fallback D3D selection sorted names before values and omitted video/compute nodes | Maximum valid Windows engine; no sum; driver fallback marked `drv` |
| CPU temperature | A hotter CCD Tdie could replace Package/Tctl in the Package row | Package/Tctl has higher priority; selected sensor included in diagnostics |
| CPU clock | Ordinary and effective clocks competed in one maximum | Ordinary core clocks only; `GHz max` distinguishes it from average/effective frequency |
| Adapter selection | Hotter adapter could replace an idle discrete GPU | Available devices, vendor priority, dedicated capacity and stable identity determine the bundle |
| VRAM | Last enumerated used/total values could mix Windows and driver sources | Complete same-source pair; Windows dedicated memory preferred; shared memory excluded |
| RAM | LHM percentage could coexist with Windows used/total bytes | Percentage and bytes from one Windows snapshot |
| Capacity labels | Binary capacities labeled GB | GiB labels in main and detail rows |
| Diagnostics | Source choice difficult to reconstruct | Selected GPU identity, load source/node, VRAM source and CPU temperature sensor included |

Six new parser tests failed on the previous implementation and passed after the
corrections. Existing tests that deliberately encoded the old RAM/GPU preference
were updated to the documented source policy. Missing/invalid data, idle zero,
order permutations, incomplete VRAM pairs and unavailable temperatures remain
covered; no missing temperature is replaced by a different GPU's temperature.

## Remaining readings checked

| Reading | Evidence and boundary |
| --- | --- |
| CPU temperature/load/clock | Actual Tctl/Tdie, CPU Total, and ordinary core-clock inputs match parsed values. LHM busy-time calculation inspected; it is not necessarily identical to Task Manager's frequency-weighted utility. |
| GPU Core/Hotspot/Memory | Separate, correctly mapped raw temperatures; ADLX comparison within 1 C across adjacent samples. This is two APIs, not an external calibrated thermometer. |
| CPU Fan 1 / Fan 2 | Exact IT8696E tach identities 0/4 map to CPU/CPU OPT; values match raw tach readings. Both control values were unavailable; shown `~%` correctly uses the saved 2000 RPM reference. |
| GPU fan | Raw RPM and reported duty map to the same GPU. ADLX agrees on RPM. Duty is a command percentage, not percentage of measured maximum RPM. |
| RAM | Windows reports 50,620,768,256 usable bytes = 47.1 GiB; displayed percentage and capacity agree after rounding. Hardware-reserved memory is not usable RAM. |
| VRAM | Windows dedicated used/total pair is on `/gpu-amd/5`, RX 7900 XT; shared RAM is excluded. Reported available capacity can be below marketed physical VRAM. |
| C/D/E/F | Get-Partition independently confirms physical disk numbers 2/0/3/1. Get-Volume confirms total/free capacity; small changes between polls are expected. |
| Disk temperatures | Primary Temperature/Composite selected; extra Temperature 2 kept in Details; warning/critical SMART thresholds are excluded from live temperatures. |
| Storage percentages | Volume fullness, not disk activity or whole-device used space. The raw LHM activity counters are not displayed. |
| Colors/alerts/peaks | Shared existing threshold tests pass. SSD 55 C remains the documented conservative generic app threshold, not a drive-reported overheating threshold. Historical peaks and auxiliary board temperatures remain separately identified. |

## Verification

- `.venv/Scripts/python.exe -m unittest discover -s tests`: 789 passed.
- `.venv/Scripts/python.exe -m compileall -q overlay.py setup.py hardware_access_guard.py tests`: passed.
- `.venv/Scripts/python.exe setup.py --verify`: DLL and shared-module integrity passed.
- `git diff --check`: passed.
- One intermediate full run had a non-reproducing GPU worker fake-test failure;
  the isolated test returned successful verified restoration, and the final full
  run passed. No fan-worker production code was changed to accommodate it.
- Final elevated overlay: 12 consecutive samples over about 22 seconds had no
  mismatches between raw inputs and parsed CPU temperature/load/clock, three GPU
  temperatures, both CPU RPMs, GPU RPM/duty and VRAM arithmetic. RAM differed from
  an adjacent Windows sample by at most 0.4 percentage points (display rounding).
- Actual Tk rows show `GHz max`, GiB units and Windows GPU load. Measured window
  width and required width were both 297 px, with no mapped row text clipped.
- GCC and Ryzen Master remained running; both HeatMap fan workers remained absent.

Local, non-versioned evidence is under `%LOCALAPPDATA%/HeatMap/`:
`sensor-audit-live.json`, `sensor-audit-verification.json`, `ryzen-live-ui.json`,
`amd-report-audit.json`, `adlx-sensor-audit.json`, and `gpu-load-comparison.json`.
Snapshots include timestamps and are historical once their capture window ends.

Extended gaming, all possible multi-GPU topologies, physical sensor calibration,
and native-driver recovery are not established by this audit. A newly introduced
or unrecognized GPU may still require an explicit selection policy; missing
dedicated-memory capacity uses a deterministic name/ID tie-break.

## Source references

- [LHM 0.9.5 AmdGpu.cs](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/blob/v0.9.5/LibreHardwareMonitorLib/Hardware/Gpu/AmdGpu.cs): AMD and Windows reading paths.
- [LHM 0.9.5 CpuLoad.cs](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/blob/v0.9.5/LibreHardwareMonitorLib/Hardware/Cpu/CpuLoad.cs): busy-time calculation.
- [Microsoft GPU utilization](https://devblogs.microsoft.com/directx/gpus-in-the-task-manager/): busiest-engine aggregation.
- [AMD ADL documentation](https://gpuopen-librariesandsdks.github.io/adl/): activity APIs and their documented limits.
- [Pinned ADLX metrics interface](https://github.com/GPUOpen-LibrariesAndSDKs/ADLX/blob/d9f04a9bba022d6cf6333f005dd540b4ad19fb63/SDK/Include/IPerformanceMonitoring.h): independent read-only usage/clock/temperature/VRAM/RPM probe.
