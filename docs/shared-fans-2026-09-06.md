# Four case fans: software commissioning, 2026-09-06

User-requested scope: Gigabyte B550 AORUS PRO AC, four stock Deepcool MATREXX 55
MESH ADD-RGB 4F case fans, no liquid cooler; avoid BIOS setup. GPU is Gigabyte
RX 7900 XT Gaming OC. CPU/GPU fan settings are outside this change.

## Evidence and implementation

Initial live inventory confirmed IT8688E plus the IT8792E family (raw chip ID
0x8733), with nonzero SYS1/2/4/5 and zero SYS3/6. SYS4 is physically running,
but was firmware-controlled at approximately half command during GPU heating.
Header numbers do not identify intake/exhaust placement.

The pinned LHM 0.9.5 source is 30395c48a7f912894a7c392db8c11e1b97859658.
The native shared backend on this machine is IsaBridgeGigabyteController, not
EcioPortGigabyteController. ECIO read attempts timed out and were not used for
fan writes. The ECIO version=1 convention is not a SMFI firmware-version contract;
this machine's MMIO area byte 0x900 reads 0, while the supported enable byte 0x947
reads 1 repeatedly. Signed-module mapping restoration was verified after reads.

The old bundled IsaBridge module predates the upstream AMD decode-bit preservation
fix. The new profile loads only the official signed IsaBridgeEC module 0.2.10,
source c683032770575d7705d1149f9d7fa7fd381766fc. The archive digest matches GitHub's
published SHA-256; file integrity and lock consistency are checked before loading.
No installed driver, NuGet DLL, Python dependency or BIOS configuration was changed.

`pawnio_shared.py` uses the documented PawnIO ioctl format and the signed module's
MMIO operations. `shared_fans.py` uses narrow reflection to the exact pinned LHM
IT87XX ReadByte/WriteByte methods, not its cached shared-controller Enable/Restore
or SetSoftware path. It accepts only this board/controller identity and register
allowlist. The existing ISA mutex serializes transactions. Upstream register
addresses are 0x13, 0x15/16/17 and PWM bytes 0x63/6B/73.

Before takeover, actual EC mode must be 1, selected output bits enabled, SYS4/5
tachometers running and SYS6 unused. Every secondary PWM is primed to 100% before
clearing automatic bits; the independent LHM controls also start with a preloaded
100% SoftwareValue. SYS4/5 then follow the existing 60–100% thermal demand; SYS6
stays at 100%. The application displays the four connected headers in Mode.

Partial writes remain in the restoration scope. Restoration attempts every saved
secondary register, re-enables EC and reads it afresh, and checks original output
bits. A register error cannot skip the EC restore attempt. Worker heartbeat,
owner lifetime, sensor/command checks and competing-tool exclusions still apply.
Lost worker reports label potentially owned headers unknown rather than firmware.

## Local hardware acceptance

First successful commissioning report:
`%LOCALAPPDATA%/HeatMap/shared-controller-2026-09-06/commission-1788695889228974700.json`.

- SYS4 baseline: 665.68 RPM, 49% reported command.
- Full-speed calibration: SYS1 1247.69, SYS2 1211.85, SYS4 1188.38, SYS5 1188.38 RPM.
- SYS6: 0 RPM, 100% protected unused output.
- Normal stop: `restore_confirmed=true`, no restore errors.
- The four-fan flag was saved only after that restoration passed.
- Restarted worker was active with all four fans at 67% and approximately
  956/923/901/917 RPM, confirming a lower-speed response as well as full speed.

Final normal restart also confirmed restoration, then six fresh active samples
over ten seconds. Evidence: `shared-controller-2026-09-06/restart_final.json` under
the local HeatMap directory. Its recorded source SHA-256 values match the final
overlay, worker, shared controller and signed-module adapter files.

Configuration backups and diagnostic probes are in the same local report directory.
The module/signature acceptance and RPM/restore observations are real elevated
hardware checks. Synthetic tests exercise fault paths; they do not prove recovery
from a kernel hang or forced controller termination. No release/commit/push was requested.

## Follow-up and rollback

Automated verification: 507 unittest cases, compilation of changed Python modules,
`setup.py --verify` (DLLs and signed module), `setup.py --preflight`, runtime manifest
synchronization and `pip check`. The added fault tests use fake register/EC adapters
and validate ordering, partial writes, lost ownership, corrupt worker status,
unused-header handling and mapping cleanup. The connected four-header calibration
schema is now explicitly accepted; unrelated and incomplete schemas remain rejected.

Cooling → Automatic case fans OFF restores motherboard ownership. The pre-change
configuration is retained in the commissioning report's `config_backup` path.
Reverting to the independent profile requires normal controller shutdown before
setting `case_fans_shared_enabled=false`; never kill an active worker to roll back.

Check normal-game noise and stability, and compare Core/Hotspot at matching GPU
power, GPU fan RPM, workload and ambient conditions. The lower idle temperatures
during commissioning are not evidence that the repaste or case airflow fixed the
GPU hotspot gap. Physical fan placement and long-run Windows stability remain manual.
