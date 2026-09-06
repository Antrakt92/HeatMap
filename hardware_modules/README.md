# Shared fan runtime

`IsaBridgeEC.bin` is the unmodified, signed PawnIO module from
[PawnIO.Modules 0.2.10](https://github.com/namazso/PawnIO.Modules/releases/tag/0.2.10),
published 2026-07-27. The 2026-08-30 release was younger than the project's 14-day
dependency gate and was not adopted. This is a module for the installed PawnIO
2.0.1 driver, not a new driver installation.

Exact archive/file hashes, size, source commit and URL are in `runtime_sources.json`;
`runtime-lock.json` locks the supplemental file. `pawnio_shared.verified_module`
checks both manifests and bytes before loading; `setup.py --verify` checks it too.
The driver accepts only appropriately signed modules.

Corresponding source and build files:
[immutable source tree](https://github.com/namazso/PawnIO.Modules/tree/c683032770575d7705d1149f9d7fa7fd381766fc),
[IsaBridgeEC.p](https://github.com/namazso/PawnIO.Modules/blob/c683032770575d7705d1149f9d7fa7fd381766fc/IsaBridgeEC.p).
Copyright (C) 2025 namazso and contributors; LGPL-2.1-or-later, see `COPYING`.
No upstream source was modified. Reproduce the binary by extracting only
`IsaBridgeEC.bin` from the exact hash-verified official archive; never substitute
an unsigned build or a floating release.
