# GPU cooling policy review

## Decision

Retain the existing cooling-first RX 7900 XT profile. No manufacturer source
reviewed supplies an optimal universal temperature/duty table for this card.
Raising intervention or full-speed thresholds would trade temperature for noise
without matched-load measurements supporting that trade. The existing goal is
stronger cooling and full airflow before Hotspot reaches 100 degrees Celsius.

At idle, retain the saved driver curve and Zero RPM. Intervene only on valid,
fresh GPU readings: Core >=70, Hotspot >=85, or Memory >=85 degrees Celsius.
The software requests full speed at Core 75, Hotspot 90, or Memory 90. Integer
duty is rounded upwards, so 100% can be requested just below an endpoint
(for Hotspot, above 89.5 degrees). This is deliberate conservative rounding,
not an idle-temperature trigger. Driver duty can exceed the software floor.

After takeover, return the saved curve and Zero RPM after ten seconds of fresh
samples with Core <=60, Hotspot <=75, and Memory <=75. Missing data cannot earn
cooling time. Missing/stalled measurements or tachometers after takeover retain
the existing failsafe and verified restoration. Startup does not run a 100% test.

## Manufacturer evidence

- [AMD Adrenalin fan tuning](https://www.amd.com/en/resources/support-articles/faqs/DH3-020.html)
  documents Zero RPM at light load and adjustable fan curves. It does not prescribe
  this application's intervention/full-speed thresholds.
- [Gigabyte RX 7900 XT Gaming OC](https://www.gigabyte.com/Graphics-Card/GV-R79XTGAMING-OC-20GD)
  documents semi-passive fans staying off at low load. Thus 0 RPM at idle is not
  itself a reason for HeatMap to take over.
- [AMD ADLX Hotspot metric](https://gpuopen.com/manuals/adlx/adlx-sdk-references/adlx-interfaces/performance-monitoring/iadlxgpumetrics/gpuhotspottemperature/)
  defines a separate junction-temperature reading. Core, Hotspot and Memory are
  read separately by the GPU worker.

These sources support the architecture and idle behavior; the numeric profile
is an application choice, not a manufacturer rating or proven optimum.

## Verification and limits

The complete worker runs against recording fake adapters for two minutes at
Core/Hotspot 40 and Memory 56 with a hot CPU: no fan-setting writes occur.
Boundary cases cover Hotspot 84/84.9 (standby), 85 (90%), 89 (98%), 89.5 (99%),
and 90/95 (100%), including the native-curve request and exact restoration.
A Hotspot 90-to-40 transition releases the saved curve after the cooling hold.
All temperatures and elapsed times in these regressions are synthetic.

Local verification: all 939 unit tests passed, including 24 startup/policy worker
tests. Compilation, DLL/shared-module integrity, runtime-manifest consistency,
and Git whitespace checks passed. GitHub-hosted execution was not requested.

Live read-only observations before this review showed three fresh idle samples,
Core 42-43, Hotspot 43, Memory 58, 0 RPM, and standby. No commissioning, fan-speed
commands, workload launch, driver installation or hardware tuning was performed
for this review. Sustained gaming, noise, temperature reduction and physical
100% response remain hardware acceptance tasks. Observe normal user-operated
gaming at matched settings before choosing a quieter profile.
