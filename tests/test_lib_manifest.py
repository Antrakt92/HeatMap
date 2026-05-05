import hashlib
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import setup


class LibManifestTests(unittest.TestCase):
    def test_manifest_covers_exact_current_lib_dlls(self):
        manifest = setup.load_lib_manifest()
        manifest_files = sorted(entry["file"] for entry in manifest["files"])
        actual_files = sorted(
            f"lib/{name}"
            for name in os.listdir(setup.LIB_DIR)
            if name.lower().endswith(".dll")
        )

        self.assertEqual(manifest_files, actual_files)

    def test_manifest_hashes_and_sizes_match_current_files(self):
        manifest = setup.load_lib_manifest()

        for entry in manifest["files"]:
            path = os.path.join(setup.APP_DIR, entry["file"])
            with open(path, "rb") as f:
                data = f.read()
            self.assertEqual(entry["size"], len(data), entry["file"])
            self.assertEqual(entry["sha256"], hashlib.sha256(data).hexdigest(), entry["file"])

    def test_direct_nuget_entries_match_setup_packages(self):
        manifest = setup.load_lib_manifest()
        by_name = {os.path.basename(entry["file"]): entry for entry in manifest["files"]}

        for package_name, package in setup.PACKAGES.items():
            for package_path in package["dlls"]:
                dll_name = os.path.basename(package_path)
                entry = by_name[dll_name]
                source = entry["source"]
                self.assertEqual(source["type"], "nuget")
                self.assertEqual(source["package"], package_name)
                self.assertEqual(source["version"], package["version"])
                self.assertEqual(source["url"], package["url"])
                self.assertEqual(source["package_path"], package_path)
                self.assertEqual(entry["sha256"], package["sha256"][dll_name])

    def test_verify_manifest_succeeds_for_current_repo_lib(self):
        ok, messages = setup.verify_lib_manifest()

        self.assertTrue(ok, messages)
        self.assertEqual(messages, [])

    def test_verify_manifest_reports_missing_extra_size_and_hash(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            lib_dir = os.path.join(tmpdir, "lib")
            os.mkdir(lib_dir)
            a_path = os.path.join(lib_dir, "a.dll")
            extra_path = os.path.join(lib_dir, "extra.dll")
            with open(a_path, "wb") as f:
                f.write(b"abc")
            with open(extra_path, "wb") as f:
                f.write(b"extra")
            manifest_path = _write_manifest(tmpdir, [
                _entry("lib/a.dll", size=99, sha256="0" * 64),
                _entry("lib/missing.dll", size=1, sha256="1" * 64),
            ])

            ok, messages = setup.verify_lib_manifest(lib_dir=lib_dir, manifest_path=manifest_path)

        self.assertFalse(ok)
        joined = "\n".join(messages)
        self.assertIn("missing DLL: lib/missing.dll", joined)
        self.assertIn("extra DLL not in manifest: lib/extra.dll", joined)
        self.assertIn("size mismatch for lib/a.dll", joined)
        self.assertIn("hash mismatch for lib/a.dll", joined)

    def test_verify_manifest_rejects_duplicate_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            lib_dir = os.path.join(tmpdir, "lib")
            os.mkdir(lib_dir)
            manifest_path = _write_manifest(tmpdir, [
                _entry("lib/a.dll"),
                _entry("lib/a.dll"),
            ])

            ok, messages = setup.verify_lib_manifest(lib_dir=lib_dir, manifest_path=manifest_path)

        self.assertFalse(ok)
        self.assertIn("duplicate manifest file: lib/a.dll", "\n".join(messages))

    def test_verify_manifest_rejects_invalid_manifest_shape(self):
        cases = [
            _entry("../a.dll"),
            _entry("lib/sub/a.dll"),
            _entry("lib/a.dll", sha256="not-a-hash"),
            _entry("lib/a.dll", source={"type": "mystery"}),
        ]
        for entry in cases:
            with self.subTest(entry=entry):
                with tempfile.TemporaryDirectory() as tmpdir:
                    lib_dir = os.path.join(tmpdir, "lib")
                    os.mkdir(lib_dir)
                    manifest_path = _write_manifest(tmpdir, [entry])

                    ok, messages = setup.verify_lib_manifest(lib_dir=lib_dir, manifest_path=manifest_path)

                self.assertFalse(ok)
                self.assertTrue(messages)

    def test_cli_verify_returns_expected_exit_code(self):
        with (
            mock.patch.object(setup, "verify_lib_manifest", return_value=(True, [])),
            mock.patch.object(setup, "_print_manifest_result"),
        ):
            self.assertEqual(setup.main(["--verify"]), 0)

        with (
            mock.patch.object(setup, "verify_lib_manifest", return_value=(False, ["bad"])),
            mock.patch.object(setup, "_print_manifest_result"),
        ):
            self.assertEqual(setup.main(["--verify"]), 1)

    def test_default_main_downloads_then_verifies(self):
        with (
            mock.patch.object(setup, "download_and_extract") as download,
            mock.patch.object(setup, "verify_lib_manifest", return_value=(True, [])) as verify,
            mock.patch.object(setup, "_print_manifest_result"),
            mock.patch("builtins.print"),
        ):
            self.assertEqual(setup.main([]), 0)

        download.assert_called_once()
        verify.assert_called_once()

    def test_default_main_returns_failure_when_manifest_verification_fails(self):
        with (
            mock.patch.object(setup, "download_and_extract"),
            mock.patch.object(setup, "verify_lib_manifest", return_value=(False, ["missing"])),
            mock.patch.object(setup, "_print_manifest_result"),
            mock.patch("builtins.print"),
        ):
            self.assertEqual(setup.main([]), 1)


def _entry(file_path, size=3, sha256=None, source=None):
    return {
        "file": file_path,
        "sha256": sha256 or hashlib.sha256(b"abc").hexdigest(),
        "size": size,
        "required": True,
        "source": source or {"type": "bundled-unknown"},
        "notes": "test entry",
    }


def _write_manifest(directory, entries):
    manifest_path = os.path.join(directory, "lib_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump({"manifest_version": setup.MANIFEST_VERSION, "files": entries}, f)
    return manifest_path


if __name__ == "__main__":
    unittest.main()
