# Third-Party Notices

HeatMap bundles the exact DLL assets locked in `runtime-lock.json` and verified
by `lib_manifest.json`. The lock records the NuGet package, version, asset path,
package hash, and license for every distributed DLL.

Local license texts and exact package-specific notices are stored under
`third_party_licenses/`. `third_party_licenses/manifest.json` maps every unique
locked package/version to its nuspec license declaration and local artifacts.
Byte-identical package files share one content-addressed local copy; different
upstream copyright/notice blocks remain separate.

## MPL-2.0 components

- LibreHardwareMonitorLib 0.9.5 — https://github.com/LibreHardwareMonitor/LibreHardwareMonitor
- BlackSharp.Core 1.0.7 — https://github.com/Blacktempel/BlackSharp
- DiskInfoToolkit 1.1.1 — https://github.com/Blacktempel/DiskInfoToolkit
- RAMSPDToolkit-NDD 1.4.2 — https://github.com/Blacktempel/RAMSPDToolkit

These components are licensed under the Mozilla Public License 2.0:
[`third_party_licenses/spdx/MPL-2.0.txt`](third_party_licenses/spdx/MPL-2.0.txt)

## Apache-2.0 component

- HidSharp 2.6.4 — https://software.seekye.com/hidsharp

HidSharp is licensed under the Apache License 2.0:
[`third_party_licenses/package_artifacts/`](third_party_licenses/package_artifacts/)
contains the exact `LICENSE.txt` from the hash-pinned HidSharp package, including
its copyright statement and the complete Apache-2.0 text. Its precise filename
and package path are recorded in the manifest.

## MIT components

The Microsoft.Bcl.* and System.* assemblies listed in `runtime-lock.json` are
distributed through their corresponding Microsoft NuGet packages under the
[`MIT license`](third_party_licenses/spdx/MIT.txt). Exact `LICENSE.TXT` and
`THIRD-PARTY-NOTICES.TXT` files present in those packages are retained in
`third_party_licenses/package_artifacts/` and mapped package-by-package in the
manifest.

This notice supplements, and does not replace, the exact license metadata and
upstream notices contained in each locked NuGet package.

## Reproducible provenance check

Run:

```powershell
python tools/check_third_party_licenses.py
```

The check downloads each locked NuGet package, verifies its full nupkg SHA-256
before opening it, compares nuspec license metadata with the lock/manifest, and
then compares every discovered license/notice file byte-for-byte with the
tracked artifact. Canonical SPDX texts are pinned to an exact
`spdx/license-list-data` commit and hash. Maintainers can regenerate the bundle
with `python tools/check_third_party_licenses.py --sync`; generation stops before
writing unverified remote bytes.
