import hashlib
import io
import os
import subprocess
import sys
import unittest
import zipfile


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(REPO_ROOT, "tools")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

import check_third_party_licenses as licenses


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        self.close()


def _archive(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for path, data in files.items():
            archive.writestr(path, data)
    return output.getvalue()


def _nuspec(license_type="expression", value="MIT"):
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<package xmlns="http://schemas.microsoft.com/packaging/2013/05/nuspec.xsd">'
        '<metadata><id>Fixture</id><version>1.0.0</version>'
        f'<license type="{license_type}">{value}</license>'
        '</metadata></package>'
    ).encode("utf-8")


class ThirdPartyLicenseTests(unittest.TestCase):
    def test_hash_pinned_artifacts_disable_git_text_normalization(self):
        attributes_path = os.path.join(REPO_ROOT, ".gitattributes")
        with open(attributes_path, "r", encoding="utf-8") as stream:
            attributes = stream.read()

        self.assertIn("third_party_licenses/package_artifacts/** -text", attributes)
        self.assertIn("third_party_licenses/spdx/** -text", attributes)

    def test_hash_pinned_artifact_bytes_are_exact_in_git_index(self):
        if not os.path.exists(os.path.join(REPO_ROOT, ".git")):
            self.skipTest("Source archive has no Git index; local artifact hashes are checked separately")
        manifest, _packages = licenses.validate_local()
        artifacts = list(manifest["canonical_licenses"].values())
        for package in manifest["packages"]:
            artifacts.extend(package["artifacts"])

        by_path = {artifact["path"]: artifact for artifact in artifacts}
        for path, metadata in by_path.items():
            tracked_bytes = subprocess.check_output(
                ["git", "show", f":{path}"],
                cwd=REPO_ROOT,
            )
            self.assertEqual(len(tracked_bytes), metadata["size"], path)
            self.assertEqual(hashlib.sha256(tracked_bytes).hexdigest(), metadata["sha256"], path)

    def test_tracked_manifest_covers_every_locked_package_and_exact_local_bytes(self):
        manifest, packages = licenses.validate_local()

        self.assertEqual(len(packages), 23)
        self.assertEqual(len(manifest["packages"]), len(packages))
        self.assertEqual(
            {item["license"]["expression"] for item in manifest["packages"]},
            {"Apache-2.0", "MIT", "MPL-2.0"},
        )

    def test_nupkg_hash_is_checked_before_zip_parsing(self):
        package = {
            "package": "Fixture",
            "version": "1.0.0",
            "nupkg_sha256": "0" * 64,
            "license": "MIT",
        }

        with self.assertRaisesRegex(licenses.LicenseCheckError, "NuGet hash mismatch"):
            licenses._open_locked_package(
                package,
                urlopen=lambda _request, **_kwargs: _Response(b"not a zip"),
            )

    def test_nuspec_license_expression_must_match_runtime_lock(self):
        package = {
            "package": "Fixture",
            "version": "1.0.0",
            "nupkg_sha256": "0" * 64,
            "license": "MIT",
        }
        expected = {
            "license": {
                "path": "Fixture.nuspec",
                "type": "expression",
                "value": "MIT",
            },
            "artifacts": [],
        }
        data = _archive({"Fixture.nuspec": _nuspec(value="MPL-2.0")})

        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            with self.assertRaisesRegex(licenses.LicenseCheckError, "nuspec value mismatch"):
                licenses._verify_archive_provenance(archive, package, expected)

    def test_new_package_notice_cannot_be_omitted_from_manifest(self):
        package = {
            "package": "Fixture",
            "version": "1.0.0",
            "nupkg_sha256": "0" * 64,
            "license": "MIT",
        }
        expected = {
            "license": {
                "path": "Fixture.nuspec",
                "type": "expression",
                "value": "MIT",
            },
            "artifacts": [],
        }
        data = _archive(
            {
                "Fixture.nuspec": _nuspec(),
                "THIRD-PARTY-NOTICES.TXT": b"required notice\r\n",
            }
        )

        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            with self.assertRaisesRegex(licenses.LicenseCheckError, "legal artifact set changed"):
                licenses._verify_archive_provenance(archive, package, expected)

    def test_package_artifact_must_match_exact_tracked_bytes(self):
        notice = b"required notice\r\n"
        package = {
            "package": "Fixture",
            "version": "1.0.0",
            "nupkg_sha256": "0" * 64,
            "license": "MIT",
        }
        metadata = {
            "package_path": "THIRD-PARTY-NOTICES.TXT",
            "path": "third_party_licenses/package_artifacts/fixture.txt",
            "sha256": hashlib.sha256(notice).hexdigest(),
            "size": len(notice),
        }
        expected = {
            "license": {
                "path": "Fixture.nuspec",
                "type": "expression",
                "value": "MIT",
            },
            "artifacts": [metadata],
        }
        data = _archive(
            {
                "Fixture.nuspec": _nuspec(),
                "THIRD-PARTY-NOTICES.TXT": notice,
            }
        )

        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            with self.assertRaisesRegex(licenses.LicenseCheckError, "tracked bytes differ"):
                licenses._verify_archive_provenance(
                    archive,
                    package,
                    expected,
                    local_file_reader=lambda _metadata, _label: b"normalized notice\n",
                )


if __name__ == "__main__":
    unittest.main()
