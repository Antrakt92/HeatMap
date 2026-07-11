"""Verify and reproduce license artifacts for the locked NuGet runtime.

The default mode is read-only. ``--sync`` rewrites only generated files under
``third_party_licenses`` after every remote byte source passes its pinned hash.
"""

import argparse
import hashlib
import io
import json
import os
import re
import ssl
import sys
import urllib.request
import zipfile
import xml.etree.ElementTree as ET


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUNTIME_LOCK_PATH = os.path.join(REPO_ROOT, "runtime-lock.json")
LICENSE_ROOT = os.path.join(REPO_ROOT, "third_party_licenses")
MANIFEST_PATH = os.path.join(LICENSE_ROOT, "manifest.json")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_LEGAL_NAME_TOKENS = {"license", "licence", "copying", "copyright", "notice", "notices"}
_SPDX_COMMIT = "c4a7237ec8f4654e867546f9f409749300f1bf4c"
CANONICAL_LICENSES = {
    "MIT": {
        "path": "third_party_licenses/spdx/MIT.txt",
        "source_url": (
            "https://raw.githubusercontent.com/spdx/license-list-data/"
            f"{_SPDX_COMMIT}/text/MIT.txt"
        ),
        "sha256": "b05785f9f18e6716bab63424b11454513b9943a222595b70411009202fc592b5",
        "size": 1078,
    },
    "MPL-2.0": {
        "path": "third_party_licenses/spdx/MPL-2.0.txt",
        "source_url": (
            "https://raw.githubusercontent.com/spdx/license-list-data/"
            f"{_SPDX_COMMIT}/text/MPL-2.0.txt"
        ),
        "sha256": "66a3107d5ad6a058aab753eaac2047ccb2ed0e39465dd0fe5844da3e300d5172",
        "size": 16727,
    },
}


class LicenseCheckError(RuntimeError):
    """Raised when locked license provenance is incomplete or inconsistent."""


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _read_json(path, label):
    try:
        with open(path, "r", encoding="utf-8") as stream:
            return json.load(stream)
    except Exception as exc:
        raise LicenseCheckError(f"failed to load {label}: {exc}") from exc


def load_locked_packages(path=RUNTIME_LOCK_PATH):
    runtime_lock = _read_json(path, "runtime lock")
    if not isinstance(runtime_lock, dict) or runtime_lock.get("schema_version") != 1:
        raise LicenseCheckError("runtime lock schema_version must be 1")
    files = runtime_lock.get("files")
    if not isinstance(files, list) or not files:
        raise LicenseCheckError("runtime lock files must be a non-empty list")

    packages = {}
    for index, entry in enumerate(files):
        if not isinstance(entry, dict):
            raise LicenseCheckError(f"runtime lock files[{index}] must be an object")
        required = ("package", "version", "nupkg_sha256", "license")
        if any(not isinstance(entry.get(field), str) or not entry[field] for field in required):
            raise LicenseCheckError(f"runtime lock files[{index}] has invalid package metadata")
        if not _SHA256_RE.fullmatch(entry["nupkg_sha256"]):
            raise LicenseCheckError(f"invalid nupkg hash for {entry['package']} {entry['version']}")
        key = (entry["package"].casefold(), entry["version"].casefold())
        package = {
            "package": entry["package"],
            "version": entry["version"],
            "nupkg_sha256": entry["nupkg_sha256"],
            "license": entry["license"],
        }
        previous = packages.setdefault(key, package)
        if previous != package:
            raise LicenseCheckError(
                f"conflicting lock metadata for {entry['package']} {entry['version']}"
            )
    return packages


def _package_url(package):
    package_id = package["package"].casefold()
    version = package["version"].casefold()
    return (
        "https://api.nuget.org/v3-flatcontainer/"
        f"{package_id}/{version}/{package_id}.{version}.nupkg"
    )


def _download(url, urlopen=urllib.request.urlopen):
    request = urllib.request.Request(url, headers={"User-Agent": "HeatMap license provenance"})
    try:
        with urlopen(request, timeout=60, context=ssl.create_default_context()) as response:
            return response.read()
    except Exception as exc:
        raise LicenseCheckError(f"download failed for {url}: {exc}") from exc


def _open_locked_package(package, urlopen=urllib.request.urlopen):
    data = _download(_package_url(package), urlopen=urlopen)
    actual_hash = _sha256(data)
    if actual_hash != package["nupkg_sha256"]:
        # WARNING: do not parse an archive until its complete nupkg is authenticated.
        raise LicenseCheckError(
            f"NuGet hash mismatch for {package['package']} {package['version']}: "
            f"expected {package['nupkg_sha256']}, got {actual_hash}"
        )
    try:
        return zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise LicenseCheckError(
            f"hash-verified package is not a zip: {package['package']} {package['version']}"
        ) from exc


def _local_name(tag):
    return tag.rsplit("}", 1)[-1]


def _read_nuspec(archive, package):
    nuspec_paths = sorted(
        name
        for name in archive.namelist()
        if name.casefold().endswith(".nuspec") and "/" not in name.strip("/")
    )
    if len(nuspec_paths) != 1:
        raise LicenseCheckError(
            f"expected one root nuspec for {package['package']} {package['version']}, "
            f"found {nuspec_paths}"
        )
    path = nuspec_paths[0]
    try:
        root = ET.fromstring(archive.read(path))
    except ET.ParseError as exc:
        raise LicenseCheckError(f"invalid nuspec XML in {path}: {exc}") from exc
    license_nodes = [node for node in root.iter() if _local_name(node.tag) == "license"]
    if len(license_nodes) != 1:
        raise LicenseCheckError(f"expected one nuspec license element in {path}")
    node = license_nodes[0]
    license_type = (node.attrib.get("type") or "").strip()
    value = (node.text or "").strip()
    if license_type not in {"expression", "file"} or not value:
        raise LicenseCheckError(f"unsupported nuspec license metadata in {path}")
    return {"path": path, "type": license_type, "value": value}


def _is_legal_artifact(package_path):
    base_name = package_path.rsplit("/", 1)[-1].casefold()
    tokens = set(re.sub(r"[^a-z0-9]+", " ", base_name).split())
    return bool(tokens & _LEGAL_NAME_TOKENS)


def _legal_artifacts(archive):
    return sorted(
        name
        for name in archive.namelist()
        if not name.endswith("/") and _is_legal_artifact(name)
    )


def _safe_repo_path(relative_path):
    if (
        not isinstance(relative_path, str)
        or not relative_path
        or "\\" in relative_path
        or os.path.isabs(relative_path)
    ):
        raise LicenseCheckError(f"invalid tracked artifact path: {relative_path!r}")
    absolute = os.path.realpath(os.path.join(REPO_ROOT, *relative_path.split("/")))
    root = os.path.realpath(LICENSE_ROOT)
    if os.path.commonpath((absolute, root)) != root:
        raise LicenseCheckError(f"artifact escapes third_party_licenses: {relative_path}")
    return absolute


def _validate_local_file(metadata, label):
    path = _safe_repo_path(metadata.get("path"))
    expected_hash = metadata.get("sha256")
    expected_size = metadata.get("size")
    if not isinstance(expected_hash, str) or not _SHA256_RE.fullmatch(expected_hash):
        raise LicenseCheckError(f"invalid sha256 for {label}")
    if isinstance(expected_size, bool) or not isinstance(expected_size, int) or expected_size < 0:
        raise LicenseCheckError(f"invalid size for {label}")
    try:
        with open(path, "rb") as stream:
            data = stream.read()
    except OSError as exc:
        raise LicenseCheckError(f"could not read {metadata['path']}: {exc}") from exc
    if len(data) != expected_size or _sha256(data) != expected_hash:
        raise LicenseCheckError(f"tracked artifact bytes do not match manifest: {metadata['path']}")
    return data


def validate_local(
    runtime_lock_path=RUNTIME_LOCK_PATH,
    manifest_path=MANIFEST_PATH,
):
    packages = load_locked_packages(runtime_lock_path)
    manifest = _read_json(manifest_path, "third-party license manifest")
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise LicenseCheckError("third-party license manifest schema_version must be 1")
    canonical = manifest.get("canonical_licenses")
    if canonical != CANONICAL_LICENSES:
        raise LicenseCheckError("canonical license metadata differs from the pinned SPDX sources")
    for expression, metadata in CANONICAL_LICENSES.items():
        _validate_local_file(metadata, f"canonical {expression} license")

    manifest_packages = manifest.get("packages")
    if not isinstance(manifest_packages, list):
        raise LicenseCheckError("license manifest packages must be a list")
    by_key = {}
    referenced_paths = {metadata["path"] for metadata in CANONICAL_LICENSES.values()}
    content_paths = {}
    for item in manifest_packages:
        if not isinstance(item, dict):
            raise LicenseCheckError("license manifest package entry must be an object")
        key = (str(item.get("package", "")).casefold(), str(item.get("version", "")).casefold())
        if key in by_key:
            raise LicenseCheckError(f"duplicate license manifest package: {key}")
        by_key[key] = item
        locked = packages.get(key)
        if locked is None:
            raise LicenseCheckError(f"license manifest has package absent from runtime lock: {key}")
        for field in ("package", "version", "nupkg_sha256"):
            if item.get(field) != locked[field]:
                raise LicenseCheckError(f"license manifest {field} mismatch for {locked['package']}")
        license_metadata = item.get("license")
        if not isinstance(license_metadata, dict):
            raise LicenseCheckError(f"license metadata missing for {locked['package']}")
        if license_metadata.get("expression") != locked["license"]:
            raise LicenseCheckError(f"locked license mismatch for {locked['package']}")

        artifacts = item.get("artifacts")
        if not isinstance(artifacts, list):
            raise LicenseCheckError(f"artifacts must be a list for {locked['package']}")
        artifact_by_source = {}
        for artifact in artifacts:
            if not isinstance(artifact, dict) or not isinstance(artifact.get("package_path"), str):
                raise LicenseCheckError(f"invalid artifact entry for {locked['package']}")
            source_path = artifact["package_path"]
            if source_path in artifact_by_source:
                raise LicenseCheckError(f"duplicate package artifact {source_path}")
            artifact_by_source[source_path] = artifact
            data = _validate_local_file(artifact, f"{locked['package']} {source_path}")
            referenced_paths.add(artifact["path"])
            content_hash = _sha256(data)
            previous_path = content_paths.setdefault(content_hash, artifact["path"])
            if previous_path != artifact["path"]:
                raise LicenseCheckError(
                    f"byte-identical package artifacts were not deduplicated: "
                    f"{previous_path} and {artifact['path']}"
                )

        license_type = license_metadata.get("type")
        license_value = license_metadata.get("value")
        license_artifact = license_metadata.get("artifact")
        if license_type == "expression":
            canonical_metadata = CANONICAL_LICENSES.get(license_value)
            if license_value != locked["license"] or canonical_metadata is None:
                raise LicenseCheckError(f"unsupported SPDX expression for {locked['package']}")
            if license_artifact != canonical_metadata["path"]:
                raise LicenseCheckError(f"wrong canonical license artifact for {locked['package']}")
        elif license_type == "file":
            artifact = artifact_by_source.get(license_value)
            if artifact is None or license_artifact != artifact.get("path"):
                raise LicenseCheckError(f"nuspec license file is not tracked for {locked['package']}")
        else:
            raise LicenseCheckError(f"unsupported license type for {locked['package']}")

    if set(by_key) != set(packages):
        missing = sorted(set(packages) - set(by_key))
        raise LicenseCheckError(f"locked packages missing from license manifest: {missing}")

    actual_paths = set()
    for directory, _subdirs, files in os.walk(LICENSE_ROOT):
        for file_name in files:
            absolute = os.path.join(directory, file_name)
            relative = os.path.relpath(absolute, REPO_ROOT).replace(os.sep, "/")
            if os.path.normcase(absolute) != os.path.normcase(manifest_path):
                actual_paths.add(relative)
    if actual_paths != referenced_paths:
        raise LicenseCheckError(
            "tracked license file set differs from manifest "
            f"(missing={sorted(referenced_paths - actual_paths)}, "
            f"extra={sorted(actual_paths - referenced_paths)})"
        )
    return manifest, packages


def verify_provenance(
    runtime_lock_path=RUNTIME_LOCK_PATH,
    manifest_path=MANIFEST_PATH,
    urlopen=urllib.request.urlopen,
):
    manifest, packages = validate_local(runtime_lock_path, manifest_path)
    for expression, metadata in CANONICAL_LICENSES.items():
        upstream = _download(metadata["source_url"], urlopen=urlopen)
        if len(upstream) != metadata["size"] or _sha256(upstream) != metadata["sha256"]:
            raise LicenseCheckError(f"pinned SPDX source changed for {expression}")

    manifest_by_key = {
        (item["package"].casefold(), item["version"].casefold()): item
        for item in manifest["packages"]
    }
    for key in sorted(packages):
        locked = packages[key]
        expected = manifest_by_key[key]
        with _open_locked_package(locked, urlopen=urlopen) as archive:
            _verify_archive_provenance(archive, locked, expected)
    return len(packages)


def _verify_archive_provenance(archive, locked, expected, local_file_reader=None):
    local_file_reader = _validate_local_file if local_file_reader is None else local_file_reader
    nuspec = _read_nuspec(archive, locked)
    license_metadata = expected["license"]
    for field in ("path", "type", "value"):
        if nuspec[field] != license_metadata[field]:
            raise LicenseCheckError(
                f"nuspec {field} mismatch for {locked['package']}: "
                f"expected {license_metadata[field]!r}, got {nuspec[field]!r}"
            )
    if nuspec["type"] == "expression" and nuspec["value"] != locked["license"]:
        raise LicenseCheckError(f"nuspec license differs from runtime lock for {locked['package']}")

    artifacts = {item["package_path"]: item for item in expected["artifacts"]}
    discovered = _legal_artifacts(archive)
    if set(discovered) != set(artifacts):
        raise LicenseCheckError(
            f"package legal artifact set changed for {locked['package']} "
            f"(missing={sorted(set(artifacts) - set(discovered))}, "
            f"new={sorted(set(discovered) - set(artifacts))})"
        )
    for package_path in discovered:
        data = archive.read(package_path)
        metadata = artifacts[package_path]
        if len(data) != metadata["size"] or _sha256(data) != metadata["sha256"]:
            raise LicenseCheckError(
                f"package artifact bytes changed for {locked['package']} {package_path}"
            )
        local_data = local_file_reader(metadata, f"{locked['package']} {package_path}")
        if data != local_data:
            raise LicenseCheckError(
                f"tracked bytes differ from package for {locked['package']} {package_path}"
            )


def _artifact_path(data, package_path):
    base_name = re.sub(r"[^A-Za-z0-9._-]+", "-", package_path.rsplit("/", 1)[-1])
    return f"third_party_licenses/package_artifacts/{_sha256(data)}-{base_name}"


def _write_bytes(relative_path, data):
    path = _safe_repo_path(relative_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as stream:
        stream.write(data)


def sync_artifacts(runtime_lock_path=RUNTIME_LOCK_PATH, urlopen=urllib.request.urlopen):
    packages = load_locked_packages(runtime_lock_path)
    for expression, metadata in CANONICAL_LICENSES.items():
        data = _download(metadata["source_url"], urlopen=urlopen)
        if len(data) != metadata["size"] or _sha256(data) != metadata["sha256"]:
            raise LicenseCheckError(f"pinned SPDX source changed for {expression}")
        _write_bytes(metadata["path"], data)

    package_items = []
    artifact_bytes = {}
    for key in sorted(packages):
        locked = packages[key]
        with _open_locked_package(locked, urlopen=urlopen) as archive:
            nuspec = _read_nuspec(archive, locked)
            artifacts = []
            artifact_by_source = {}
            for package_path in _legal_artifacts(archive):
                data = archive.read(package_path)
                local_path = artifact_bytes.setdefault(_sha256(data), (_artifact_path(data, package_path), data))[0]
                metadata = {
                    "package_path": package_path,
                    "path": local_path,
                    "sha256": _sha256(data),
                    "size": len(data),
                }
                artifacts.append(metadata)
                artifact_by_source[package_path] = metadata

            if nuspec["type"] == "expression":
                if nuspec["value"] != locked["license"]:
                    raise LicenseCheckError(
                        f"nuspec license differs from runtime lock for {locked['package']}"
                    )
                canonical = CANONICAL_LICENSES.get(nuspec["value"])
                if canonical is None:
                    raise LicenseCheckError(
                        f"no pinned canonical text for {nuspec['value']} ({locked['package']})"
                    )
                license_artifact = canonical["path"]
            else:
                embedded = artifact_by_source.get(nuspec["value"])
                if embedded is None:
                    raise LicenseCheckError(
                        f"nuspec license file is not recognized as a legal artifact: {nuspec['value']}"
                    )
                license_artifact = embedded["path"]

            package_items.append(
                {
                    "package": locked["package"],
                    "version": locked["version"],
                    "nupkg_sha256": locked["nupkg_sha256"],
                    "license": {
                        "expression": locked["license"],
                        "type": nuspec["type"],
                        "value": nuspec["value"],
                        "path": nuspec["path"],
                        "artifact": license_artifact,
                    },
                    "artifacts": artifacts,
                }
            )

    package_dir = os.path.join(LICENSE_ROOT, "package_artifacts")
    os.makedirs(package_dir, exist_ok=True)
    wanted_package_files = set()
    for local_path, data in artifact_bytes.values():
        _write_bytes(local_path, data)
        wanted_package_files.add(os.path.basename(local_path))
    for file_name in os.listdir(package_dir):
        path = os.path.join(package_dir, file_name)
        if os.path.isfile(path) and file_name not in wanted_package_files:
            os.remove(path)

    manifest = {
        "schema_version": 1,
        "canonical_licenses": CANONICAL_LICENSES,
        "packages": package_items,
    }
    os.makedirs(LICENSE_ROOT, exist_ok=True)
    with open(MANIFEST_PATH, "w", encoding="utf-8", newline="\n") as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    validate_local(runtime_lock_path, MANIFEST_PATH)
    return len(packages), len(artifact_bytes)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Verify exact third-party license provenance for the locked NuGet runtime."
    )
    parser.add_argument(
        "--sync",
        action="store_true",
        help="Regenerate tracked artifacts after verifying all pinned remote bytes.",
    )
    args = parser.parse_args(argv)
    try:
        if args.sync:
            package_count, artifact_count = sync_artifacts()
            print(
                f"Synchronized licenses for {package_count} packages "
                f"({artifact_count} unique package artifacts)."
            )
        else:
            package_count = verify_provenance()
            print(f"Third-party license provenance OK for {package_count} packages.")
    except (LicenseCheckError, OSError) as exc:
        print(f"Third-party license provenance failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
