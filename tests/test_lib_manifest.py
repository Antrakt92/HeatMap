import hashlib
import io
import json
import os
import tempfile
import unittest
import zipfile
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

    def test_verify_manifest_can_allow_extra_dlls_for_runtime_startup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            lib_dir = os.path.join(tmpdir, "lib")
            os.mkdir(lib_dir)
            with open(os.path.join(lib_dir, "a.dll"), "wb") as f:
                f.write(b"abc")
            with open(os.path.join(lib_dir, "extra.dll"), "wb") as f:
                f.write(b"extra")
            manifest_path = _write_manifest(tmpdir, [_entry("lib/a.dll")])

            ok, messages = setup.verify_lib_manifest(
                lib_dir=lib_dir,
                manifest_path=manifest_path,
                allow_extra_dlls=True,
            )

        self.assertTrue(ok, messages)
        self.assertEqual(messages, [])

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
            mock.patch.object(setup, "_unsupported_runtime_message") as runtime_check,
        ):
            self.assertEqual(setup.main(["--verify"]), 0)
        runtime_check.assert_not_called()

        with (
            mock.patch.object(setup, "verify_lib_manifest", return_value=(False, ["bad"])),
            mock.patch.object(setup, "_print_manifest_result"),
            mock.patch.object(setup, "_unsupported_runtime_message") as runtime_check,
        ):
            self.assertEqual(setup.main(["--verify"]), 1)
        runtime_check.assert_not_called()

    def test_default_main_downloads_then_verifies(self):
        with (
            mock.patch.object(setup, "_unsupported_runtime_message", return_value=None),
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
            mock.patch.object(setup, "_unsupported_runtime_message", return_value=None),
            mock.patch.object(setup, "download_and_extract"),
            mock.patch.object(setup, "verify_lib_manifest", return_value=(False, ["missing"])),
            mock.patch.object(setup, "_print_manifest_result"),
            mock.patch("builtins.print"),
        ):
            self.assertEqual(setup.main([]), 1)

    def test_default_main_returns_failure_when_download_fails(self):
        with (
            mock.patch.object(setup, "_unsupported_runtime_message", return_value=None),
            mock.patch.object(setup, "download_and_extract", side_effect=setup.SetupError("network down")),
            mock.patch.object(setup, "verify_lib_manifest") as verify,
            mock.patch("builtins.print") as printed,
        ):
            self.assertEqual(setup.main([]), 1)

        verify.assert_not_called()
        self.assertIn("network down", printed.call_args.args[0])

    def test_runtime_policy_accepts_windows_x64_variants(self):
        for machine in ("AMD64", "amd64", "x86_64", "X64"):
            with self.subTest(machine=machine):
                self.assertIsNone(
                    setup._unsupported_runtime_message(
                        sys_platform="win32",
                        maxsize=2 ** 63,
                        machine=machine,
                    )
                )

    def test_runtime_policy_rejects_unsupported_platforms(self):
        cases = [
            ("linux", 2 ** 63, "x86_64", "Windows"),
            ("darwin", 2 ** 63, "x86_64", "Windows"),
            ("win32", 2 ** 31 - 1, "AMD64", "64-bit Python"),
            ("win32", 2 ** 63, "ARM64", "x64"),
            ("win32", 2 ** 63, "x86", "x64"),
            ("win32", 2 ** 63, "", "unknown"),
        ]
        for sys_platform, maxsize, machine, expected in cases:
            with self.subTest(sys_platform=sys_platform, maxsize=maxsize, machine=machine):
                message = setup._unsupported_runtime_message(
                    sys_platform=sys_platform,
                    maxsize=maxsize,
                    machine=machine,
                )

            self.assertIsNotNone(message)
            self.assertIn(expected, message)

    def test_default_main_rejects_unsupported_runtime_before_download(self):
        with (
            mock.patch.object(setup, "_unsupported_runtime_message", return_value="unsupported runtime"),
            mock.patch.object(setup, "download_and_extract") as download,
            mock.patch.object(setup, "verify_lib_manifest") as verify,
            mock.patch("builtins.print") as printed,
        ):
            self.assertEqual(setup.main([]), 1)

        download.assert_not_called()
        verify.assert_not_called()
        self.assertEqual(printed.call_args.args[0], "Setup failed: unsupported runtime")

    def test_download_and_extract_raises_on_download_failure(self):
        with self._patched_download_setup():
            with (
                mock.patch.object(setup.urllib.request, "urlopen", side_effect=OSError("network down")),
                mock.patch("builtins.print"),
            ):
                with self.assertRaisesRegex(setup.SetupError, "network down"):
                    setup.download_and_extract()

    def test_download_and_extract_raises_on_bad_zip(self):
        with self._patched_download_setup(response_data=b"not a zip"):
            with mock.patch("builtins.print"):
                with self.assertRaisesRegex(setup.SetupError, "not a valid zip file"):
                    setup.download_and_extract()

    def test_download_and_extract_raises_on_missing_exact_package_path(self):
        zip_data = _zip_bytes({"other/Test.dll": b"good"})

        with self._patched_download_setup(response_data=zip_data):
            with mock.patch("builtins.print"):
                with self.assertRaisesRegex(setup.SetupError, "could not find exact DLL path"):
                    setup.download_and_extract()

    def test_download_and_extract_raises_on_hash_mismatch(self):
        zip_data = _zip_bytes({TEST_PACKAGE_DLL_PATH: b"bad"})

        with self._patched_download_setup(response_data=zip_data):
            with mock.patch("builtins.print"):
                with self.assertRaisesRegex(setup.SetupError, "hash verification failed"):
                    setup.download_and_extract()

    def test_download_and_extract_raises_on_write_failure(self):
        zip_data = _zip_bytes({TEST_PACKAGE_DLL_PATH: TEST_DLL_DATA})

        with self._patched_download_setup(response_data=zip_data):
            with (
                mock.patch("builtins.open", side_effect=OSError("denied")),
                mock.patch("builtins.print"),
            ):
                with self.assertRaisesRegex(setup.SetupError, "denied"):
                    setup.download_and_extract()

    def test_download_and_extract_writes_verified_dll(self):
        zip_data = _zip_bytes({TEST_PACKAGE_DLL_PATH: TEST_DLL_DATA})

        with self._patched_download_setup(response_data=zip_data) as lib_dir:
            with mock.patch("builtins.print"):
                setup.download_and_extract()

            with open(os.path.join(lib_dir, TEST_DLL_NAME), "rb") as f:
                self.assertEqual(f.read(), TEST_DLL_DATA)

    def _patched_download_setup(self, response_data=None):
        tmpdir = tempfile.TemporaryDirectory()
        package = {
            "url": "https://example.invalid/test.nupkg",
            "dlls": [TEST_PACKAGE_DLL_PATH],
            "sha256": {TEST_DLL_NAME: hashlib.sha256(TEST_DLL_DATA).hexdigest()},
        }
        patches = [
            mock.patch.object(setup, "LIB_DIR", tmpdir.name),
            mock.patch.object(setup, "PACKAGES", {"TestPackage": package}),
        ]
        if response_data is not None:
            patches.append(mock.patch.object(
                setup.urllib.request,
                "urlopen",
                return_value=_FakeResponse(response_data),
            ))
        return _PatchContext(tmpdir, patches)


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


TEST_DLL_NAME = "Test.dll"
TEST_PACKAGE_DLL_PATH = f"lib/net35/{TEST_DLL_NAME}"
TEST_DLL_DATA = b"good"


def _zip_bytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, data in files.items():
            zf.writestr(path, data)
    return buf.getvalue()


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._data


class _PatchContext:
    def __init__(self, tmpdir, patches):
        self._tmpdir = tmpdir
        self._patches = patches

    def __enter__(self):
        self._tmpdir.__enter__()
        for patch in self._patches:
            patch.__enter__()
        return self._tmpdir.name

    def __exit__(self, exc_type, exc, tb):
        for patch in reversed(self._patches):
            patch.__exit__(exc_type, exc, tb)
        return self._tmpdir.__exit__(exc_type, exc, tb)


if __name__ == "__main__":
    unittest.main()
