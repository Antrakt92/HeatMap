# Airflow audit — 2026-09-06

## Hardware interpretation

The user confirmed air cooling, a Noctua NH-U12A and a Deepcool MATREXX 55 MESH
ADD-RGB 4F case. There is no liquid-cooling pump. LHM's `System Fan #5 / Pump`
is a header designation, not device detection. Physical fan locations, splitters
and wiring have not been inspected; the four nonzero tachometers do not identify
intake versus exhaust. UI captions now use SYS numbers without inferring a pump.

SYS4 remains firmware-owned. A displayed `~47%` is current RPM divided by a saved
full-speed reference, whereas a plain `100%` is reported controller duty. Neither
is airflow, and equal percentages need not yield equal RPM across different fans.
The [Gigabyte B550/A520 BIOS guide, page 14](https://www.gigabyte.com/FileUpload/Global/WebPage/954/images/B550_A520%20BIOS_e_Web.pdf)
documents temperature curves, Full Speed, sensor selection and Fan Stop. A fixed
100% reading alone cannot distinguish that configuration from a curve at its peak.

## Why SYS4 takeover remains excluded

Read-only source audit used LibreHardwareMonitor v0.9.5, commit
`30395c48a7f912894a7c392db8c11e1b97859658`, matching the pinned runtime source baseline.
Relevant files under `LibreHardwareMonitorLib/Hardware/Motherboard`:

- `SuperIOHardware.cs`: B550 AORUS PRO AC maps primary SYS1/2 to IT8688E and
  SYS5/SYS6/SYS4 to IT8792E indices 0/1/2.
- `Lpc/IT87XX.cs`: software control disables the secondary controller globally.
  `SetControl` ignores the boolean result of that disable operation.
- `Lpc/EcioPortGigabyteController.cs`: enable byte is at EC offset 0x947. Restore
  ignores failure, and subsequent enable calls use cached state. Matching duty
  cannot prove the real firmware controller was restored.
- `Lpc/IT87XX.cs`: the initial fan-output state is saved as a whole-register boolean
  but restored per bit. Mixed enable bits can therefore be restored incorrectly.
- `Control.cs`: switching to software mode can apply the previous software value
  before the requested value. Its initial value can be zero.

Owning all secondary outputs does not by itself solve these restoration and
takeover issues. Zero RPM on SYS6 is not proof that the header is disconnected.
The public high-level GetReport does not expose EC 0x947. Public PawnIO MMIO access
is a possible future verifier only after backend/routing/binary compatibility is
established; this audit did not attempt raw EC/MMIO access, firmware writes, private
reflection or a replacement DLL. No new air-cooled shared-controller profile ships.

The primary IT8688E is independent of this shared EC. Its selected output bits can
be checked through the existing public register report; this guards the separate
whole-register/per-bit restoration defect without changing the runtime binary.
Startup now requires both selected output bits (mask 0x06 in register 0x13) already
enabled. An incomplete/unreadable report refuses takeover. Shutdown checks those
bits after normal restoration; mismatches leave restoration unconfirmed. This
checks the primary outputs only and does not establish the secondary EC state.

## Implemented airflow assistance

The base rule remains the maximum of CPU, GPU Core, Hotspot and Memory curve demands,
rounded upward. Missing mandatory temperatures and a large hot hotspot gap still
require 100%. Minimum duty, immediate rises and the existing delayed decline remain.

An upward-only trend adjustment uses a 10-second history, at least three samples
spanning six seconds, a net rise of at least 2°C and two positive steps. The latest
step must not fall. The existing curves evaluate a five-second linear projection,
limited to +5°C and at most +10 percentage points above the base demand. A sample
gap over five seconds, invalid/non-increasing time or GPU identity change resets
history. This is bounded anticipation, not a validated temperature prediction.

Example fixture: CPU 60, 62, 64, 65, 66, 68°C at two-second intervals produces 86%
instead of the base 81%. Stable 68°C, cooling and an isolated spike retain base
demand. No GPU/CPU load percentage or SYS5 duty is treated as heat by itself.

If an exact known SYS4/5/6 tachometer previously ran at least 200 RPM and then reads
zero on two fresh samples spanning two seconds while CPU ≥70°C, GPU Core ≥80°C,
Hotspot ≥85°C or Memory ≥85°C, SYS1/2 receive a 100% target. Missing/ambiguous readings
do not count as a confirmed stop. The temperature curves still enforce stronger
existing protections independently.

A present fan sensor whose value becomes unavailable is now retained by the parser.
After ten seconds under thermal load, the advisor reports missing feedback separately
from 0 RPM. Complete hardware-block disappearance remains covered by general partial
sensor status rather than this individual-tachometer rule.

## Display and verification boundaries

FW is now shown for all firmware-owned SYS headers, including automatic-control OFF
and confirmed restoration. Unconfirmed ownership uses `?`. The Cooling window explains
ownership, readback versus RPM estimates, and the active policy; its scrollable body
and pinned Close button fit small work areas at increased DPI.

Controller snapshots contain observed case-fan readings and separate base/policy
demands, without opening another LHM Computer. Tests use synthetic traces, fake
hardware and hidden Tk windows. Real cooling effectiveness, physical fan placement,
noise and long-term Windows stability require normal-workload observation. A brief
runtime sample cannot establish those outcomes.

Final local verification: 482 unittest cases passed, including the new mode-bit,
temperature-assist, ownership/DPI and parser-to-advisor regressions. Compileall,
DLL verification, preflight and runtime-manifest drift checks passed. Fake-hardware
tests now mock process-inventory access so running desktop utilities cannot change
their result; access-guard behavior remains independently tested.

Live application of this revision was deferred because `AMD Ryzen Master.exe` was
running during final verification. The previous HeatMap worker's terminal snapshot
reported normal restoration without errors; a subsequent pause helper found no
remaining HeatMap worker to stop. The updated primary report guard and airflow
policy have not yet had elevated hardware acceptance on this revision. No new
release was published by this audit.

SYS4's firmware curve can be adjusted in Smart Fan 5 (F6 in the BIOS), choosing the
SYS4 header and an appropriate temperature source. BIOS settings were not changed
in this audit. A GPU-sensitive software curve for SYS4 requires resolving the shared
controller/backend limitations above.
