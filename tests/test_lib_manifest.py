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

    def test_runtime_lock_matches_manifest_sources(self):
        manifest = setup.load_lib_manifest()
        _runtime_lock, locked = setup.load_runtime_lock()
        manifest_entries, messages = setup._manifest_entries_by_file(manifest)

        self.assertEqual(messages, [])
        self.assertEqual(set(locked), set(manifest_entries))
        setup._validate_runtime_lock_against_manifest(locked)
        self.assertTrue(all(
            entry["source"]["type"] == "runtime-lock"
            for entry in manifest["files"]
        ))

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
            mock.patch.object(setup, "run_preflight") as preflight,
        ):
            self.assertEqual(setup.main(["--verify"]), 0)
        runtime_check.assert_not_called()
        preflight.assert_not_called()

        with (
            mock.patch.object(setup, "verify_lib_manifest", return_value=(False, ["bad"])),
            mock.patch.object(setup, "_print_manifest_result"),
            mock.patch.object(setup, "_unsupported_runtime_message") as runtime_check,
            mock.patch.object(setup, "run_preflight") as preflight,
        ):
            self.assertEqual(setup.main(["--verify"]), 1)
        runtime_check.assert_not_called()
        preflight.assert_not_called()

    def test_preflight_main_returns_success_when_checks_pass(self):
        with (
            mock.patch.object(setup, "_unsupported_runtime_message", return_value=None),
            mock.patch.object(setup, "_check_preflight_dependencies", return_value=[]),
            mock.patch.object(setup, "_check_lhm_bridge", return_value=[]),
            mock.patch.object(setup, "_check_pawnio_driver", return_value=[]),
            mock.patch.object(setup, "verify_lib_manifest", return_value=(True, [])) as verify,
            mock.patch.object(setup, "restore_runtime") as download,
            mock.patch("builtins.print") as printed,
        ):
            self.assertEqual(setup.main(["--preflight"]), 0)

        verify.assert_called_once_with(allow_extra_dlls=True)
        download.assert_not_called()
        printed.assert_called_with("Preflight OK")

    def test_preflight_main_rejects_unsupported_runtime_without_download(self):
        with (
            mock.patch.object(setup, "_unsupported_runtime_message", return_value="unsupported runtime"),
            mock.patch.object(setup, "_check_preflight_dependencies", return_value=[]),
            mock.patch.object(setup, "_check_pawnio_driver", return_value=[]),
            mock.patch.object(setup, "verify_lib_manifest", return_value=(True, [])),
            mock.patch.object(setup, "restore_runtime") as download,
            mock.patch("builtins.print") as printed,
        ):
            self.assertEqual(setup.main(["--preflight"]), 1)

        download.assert_not_called()
        output = "\n".join(call.args[0] for call in printed.call_args_list)
        self.assertIn("ERROR: unsupported runtime", output)

    def test_preflight_main_returns_failure_when_dependency_import_fails(self):
        with (
            mock.patch.object(setup, "_unsupported_runtime_message", return_value=None),
            mock.patch.object(setup, "_check_preflight_dependencies", return_value=["missing pythonnet"]),
            mock.patch.object(setup, "_check_pawnio_driver", return_value=[]),
            mock.patch.object(setup, "verify_lib_manifest", return_value=(True, [])),
            mock.patch.object(setup, "restore_runtime") as download,
            mock.patch("builtins.print") as printed,
        ):
            self.assertEqual(setup.main(["--preflight"]), 1)

        download.assert_not_called()
        output = "\n".join(call.args[0] for call in printed.call_args_list)
        self.assertIn("ERROR: missing pythonnet", output)

    def test_preflight_dependency_check_rejects_stale_locked_version(self):
        expected_versions = setup._read_known_good_versions()

        def distribution_version(package_name):
            if package_name == "pythonnet":
                return "3.0.5"
            return expected_versions[package_name]

        messages = setup._check_preflight_dependencies(
            import_module=lambda _module_name: object(),
            distribution_version=distribution_version,
        )

        self.assertEqual(
            messages,
            ["unsupported dependency version pythonnet: expected 3.1.0, got 3.0.5"],
        )

    def test_preflight_dependency_check_accepts_all_locked_versions(self):
        expected_versions = setup._read_known_good_versions()

        messages = setup._check_preflight_dependencies(
            import_module=lambda _module_name: object(),
            distribution_version=expected_versions.__getitem__,
        )

        self.assertEqual(messages, [])

    def test_preflight_main_warns_but_succeeds_when_pawnio_driver_missing(self):
        with (
            mock.patch.object(setup, "_unsupported_runtime_message", return_value=None),
            mock.patch.object(setup, "_check_preflight_dependencies", return_value=[]),
            mock.patch.object(setup, "_check_lhm_bridge", return_value=[]),
            mock.patch.object(setup, "_check_pawnio_driver", return_value=["PawnIO driver is not installed"]),
            mock.patch.object(setup, "verify_lib_manifest", return_value=(True, [])),
            mock.patch.object(setup, "restore_runtime") as download,
            mock.patch("builtins.print") as printed,
        ):
            self.assertEqual(setup.main(["--preflight"]), 0)

        download.assert_not_called()
        output = "\n".join(call.args[0] for call in printed.call_args_list)
        self.assertIn("Preflight OK", output)
        self.assertIn("WARNING: PawnIO driver is not installed", output)

    def test_preflight_main_returns_failure_when_manifest_verify_fails(self):
        with (
            mock.patch.object(setup, "_unsupported_runtime_message", return_value=None),
            mock.patch.object(setup, "_check_preflight_dependencies", return_value=[]),
            mock.patch.object(setup, "_check_pawnio_driver", return_value=[]),
            mock.patch.object(setup, "verify_lib_manifest", return_value=(False, ["missing DLL: lib/a.dll"])),
            mock.patch.object(setup, "restore_runtime") as download,
            mock.patch("builtins.print") as printed,
        ):
            self.assertEqual(setup.main(["--preflight"]), 1)

        download.assert_not_called()
        output = "\n".join(call.args[0] for call in printed.call_args_list)
        self.assertIn("ERROR: DLL runtime: missing DLL: lib/a.dll", output)

    def test_run_preflight_rejects_broken_lhm_bridge_after_manifest_passes(self):
        with (
            mock.patch.object(setup, "_unsupported_runtime_message", return_value=None),
            mock.patch.object(setup, "_check_preflight_dependencies", return_value=[]),
            mock.patch.object(setup, "verify_lib_manifest", return_value=(True, [])),
            mock.patch.object(setup, "_check_lhm_bridge", return_value=["CLR bridge failed"]) as bridge,
        ):
            ok, messages = setup.run_preflight()

        self.assertFalse(ok)
        self.assertEqual(messages, ["CLR bridge failed"])
        bridge.assert_called_once_with()

    def test_pawnio_driver_check_points_to_verified_download_command(self):
        with (
            mock.patch.object(setup, "is_pawnio_driver_installed", return_value=False),
            mock.patch.object(setup, "is_windows_restart_pending", return_value=False),
        ):
            messages = setup._check_pawnio_driver()

        self.assertEqual(len(messages), 1)
        self.assertIn("python setup.py --download-pawnio", messages[0])
        self.assertIn("restart Windows", messages[0])

    def test_pawnio_driver_check_passes_when_installed(self):
        with mock.patch.object(setup, "is_pawnio_driver_installed", return_value=True):
            self.assertEqual(setup._check_pawnio_driver(), [])

    def test_pawnio_driver_check_requires_restart_before_install_when_pending(self):
        with (
            mock.patch.object(setup, "is_pawnio_driver_installed", return_value=False),
            mock.patch.object(setup, "is_windows_restart_pending", return_value=True),
        ):
            messages = setup._check_pawnio_driver()

        self.assertIn("restart Windows before installing PawnIO", messages[0])

    def test_default_main_downloads_then_verifies(self):
        with (
            mock.patch.object(setup, "_unsupported_runtime_message", return_value=None),
            mock.patch.object(setup, "restore_runtime") as download,
            mock.patch.object(setup, "verify_lib_manifest", return_value=(True, [])) as verify,
            mock.patch.object(setup, "_check_pawnio_driver", return_value=[]),
            mock.patch.object(setup, "_print_manifest_result"),
            mock.patch("builtins.print"),
        ):
            self.assertEqual(setup.main([]), 0)

        download.assert_called_once()
        verify.assert_called_once()

    def test_default_main_returns_failure_when_manifest_verification_fails(self):
        with (
            mock.patch.object(setup, "_unsupported_runtime_message", return_value=None),
            mock.patch.object(setup, "restore_runtime"),
            mock.patch.object(setup, "verify_lib_manifest", return_value=(False, ["missing"])),
            mock.patch.object(setup, "_check_pawnio_driver", return_value=[]),
            mock.patch.object(setup, "_print_manifest_result"),
            mock.patch("builtins.print"),
        ):
            self.assertEqual(setup.main([]), 1)

    def test_default_main_warns_but_succeeds_when_pawnio_driver_missing(self):
        with (
            mock.patch.object(setup, "_unsupported_runtime_message", return_value=None),
            mock.patch.object(setup, "restore_runtime") as download,
            mock.patch.object(setup, "verify_lib_manifest", return_value=(True, [])),
            mock.patch.object(setup, "_check_pawnio_driver", return_value=["PawnIO driver is not installed"]),
            mock.patch.object(setup, "_print_manifest_result"),
            mock.patch("builtins.print") as printed,
        ):
            self.assertEqual(setup.main([]), 0)

        download.assert_called_once()
        output = "\n".join(call.args[0] for call in printed.call_args_list)
        self.assertIn("Setup complete!", output)
        self.assertIn("WARNING: PawnIO driver is not installed", output)

    def test_default_main_returns_failure_when_download_fails(self):
        with (
            mock.patch.object(setup, "_unsupported_runtime_message", return_value=None),
            mock.patch.object(setup, "restore_runtime", side_effect=setup.SetupError("network down")),
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
            mock.patch.object(setup, "restore_runtime") as download,
            mock.patch.object(setup, "verify_lib_manifest") as verify,
            mock.patch("builtins.print") as printed,
        ):
            self.assertEqual(setup.main([]), 1)

        download.assert_not_called()
        verify.assert_not_called()
        self.assertEqual(printed.call_args.args[0], "Setup failed: unsupported runtime")

    def test_restore_runtime_raises_on_download_failure_without_touching_current_lib(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = _write_runtime_fixture(tmpdir)
            os.mkdir(fixture["lib_dir"])
            current_path = os.path.join(fixture["lib_dir"], "current.dll")
            with open(current_path, "wb") as f:
                f.write(b"current")

            with self.assertRaisesRegex(setup.SetupError, "network down"):
                setup.restore_runtime(
                    **fixture,
                    urlopen=mock.Mock(side_effect=OSError("network down")),
                )

            with open(current_path, "rb") as f:
                self.assertEqual(f.read(), b"current")

    def test_restore_runtime_stops_before_download_when_overlay_holds_dlls(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = _write_runtime_fixture(tmpdir)
            urlopen = mock.Mock()
            with (
                mock.patch.object(setup, "LIB_DIR", fixture["lib_dir"]),
                mock.patch.object(setup, "_is_overlay_running", return_value=True),
                mock.patch.object(setup, "_recover_runtime_transaction") as recover,
            ):
                with self.assertRaisesRegex(setup.SetupError, "close the overlay"):
                    setup.restore_runtime(**fixture, urlopen=urlopen)

            urlopen.assert_not_called()
            recover.assert_not_called()

    def test_restore_runtime_rejects_bad_zip_after_package_hash_passes(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = _write_runtime_fixture(tmpdir, package_data=b"not a zip")
            with self.assertRaisesRegex(setup.SetupError, "not a valid zip"):
                setup.restore_runtime(**fixture, urlopen=lambda *_a, **_k: _FakeResponse(b"not a zip"))

    def test_restore_runtime_rejects_missing_exact_package_path(self):
        package_data = _zip_bytes({"other/Test.dll": TEST_DLL_DATA})
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = _write_runtime_fixture(tmpdir, package_data=package_data)
            with self.assertRaisesRegex(setup.SetupError, "could not find exact DLL path"):
                setup.restore_runtime(
                    **fixture,
                    urlopen=lambda *_a, **_k: _FakeResponse(package_data),
                )

    def test_restore_runtime_rejects_nupkg_hash_mismatch(self):
        expected_package = _zip_bytes({TEST_PACKAGE_DLL_PATH: TEST_DLL_DATA})
        downloaded_package = _zip_bytes({TEST_PACKAGE_DLL_PATH: b"evil"})
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = _write_runtime_fixture(tmpdir, package_data=expected_package)
            with self.assertRaisesRegex(setup.SetupError, "NuGet hash mismatch"):
                setup.restore_runtime(
                    **fixture,
                    urlopen=lambda *_a, **_k: _FakeResponse(downloaded_package),
                )

    def test_restore_runtime_rejects_extracted_dll_mismatch(self):
        package_data = _zip_bytes({TEST_PACKAGE_DLL_PATH: b"evil"})
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = _write_runtime_fixture(tmpdir, package_data=package_data)
            with self.assertRaisesRegex(setup.SetupError, "does not match manifest"):
                setup.restore_runtime(
                    **fixture,
                    urlopen=lambda *_a, **_k: _FakeResponse(package_data),
                )

    def test_restore_runtime_publishes_complete_verified_staging_directory(self):
        package_data = _zip_bytes({TEST_PACKAGE_DLL_PATH: TEST_DLL_DATA})
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = _write_runtime_fixture(tmpdir, package_data=package_data)
            setup.restore_runtime(
                **fixture,
                urlopen=lambda *_a, **_k: _FakeResponse(package_data),
            )

            with open(os.path.join(fixture["lib_dir"], TEST_DLL_NAME), "rb") as f:
                self.assertEqual(f.read(), TEST_DLL_DATA)

    def test_publish_runtime_restores_previous_directory_when_swap_fails(self):
        real_replace = os.replace
        with tempfile.TemporaryDirectory() as tmpdir:
            lib_dir = os.path.join(tmpdir, "lib")
            staging_dir = os.path.join(tmpdir, "staging")
            os.mkdir(lib_dir)
            os.mkdir(staging_dir)
            with open(os.path.join(lib_dir, "old.dll"), "wb") as f:
                f.write(b"old")
            with open(os.path.join(staging_dir, "new.dll"), "wb") as f:
                f.write(b"new")
            def replace_with_failure(source, destination):
                if source == staging_dir and destination == lib_dir:
                    raise OSError("publish denied")
                return real_replace(source, destination)

            with mock.patch.object(setup.os, "replace", side_effect=replace_with_failure):
                with self.assertRaisesRegex(setup.SetupError, "previous runtime restored"):
                    setup._publish_runtime(staging_dir, lib_dir=lib_dir)

            with open(os.path.join(lib_dir, "old.dll"), "rb") as f:
                self.assertEqual(f.read(), b"old")

    def test_recover_runtime_restores_valid_backup_after_interrupted_swap(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = _write_runtime_fixture(tmpdir)
            backup_dir = f"{fixture['lib_dir']}.runtime-backup"
            os.mkdir(backup_dir)
            with open(os.path.join(backup_dir, TEST_DLL_NAME), "wb") as f:
                f.write(TEST_DLL_DATA)
            journal = f"{fixture['lib_dir']}.runtime-restore.json"
            with open(journal, "w", encoding="utf-8") as f:
                json.dump({"schema_version": 1, "phase": "backup-created"}, f)

            setup._recover_runtime_transaction(
                lib_dir=fixture["lib_dir"],
                manifest_path=fixture["manifest_path"],
            )

            self.assertTrue(os.path.isfile(os.path.join(fixture["lib_dir"], TEST_DLL_NAME)))
            self.assertFalse(os.path.exists(backup_dir))
            self.assertFalse(os.path.exists(journal))

    def test_recover_runtime_prefers_valid_published_runtime_and_cleans_backup(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = _write_runtime_fixture(tmpdir)
            for directory in (fixture["lib_dir"], f"{fixture['lib_dir']}.runtime-backup"):
                os.mkdir(directory)
                with open(os.path.join(directory, TEST_DLL_NAME), "wb") as f:
                    f.write(TEST_DLL_DATA)
            journal = f"{fixture['lib_dir']}.runtime-restore.json"
            with open(journal, "w", encoding="utf-8") as f:
                json.dump({"schema_version": 1, "phase": "published"}, f)

            setup._recover_runtime_transaction(
                lib_dir=fixture["lib_dir"],
                manifest_path=fixture["manifest_path"],
            )

            self.assertTrue(os.path.isdir(fixture["lib_dir"]))
            self.assertFalse(os.path.exists(f"{fixture['lib_dir']}.runtime-backup"))

    def test_recover_runtime_clears_prepared_journal_before_any_swap(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = _write_runtime_fixture(tmpdir)
            os.mkdir(fixture["lib_dir"])
            invalid_path = os.path.join(fixture["lib_dir"], TEST_DLL_NAME)
            with open(invalid_path, "wb") as f:
                f.write(b"invalid current runtime")
            journal = f"{fixture['lib_dir']}.runtime-restore.json"
            with open(journal, "w", encoding="utf-8") as f:
                json.dump({"schema_version": 1, "phase": "prepared"}, f)

            setup._recover_runtime_transaction(
                lib_dir=fixture["lib_dir"],
                manifest_path=fixture["manifest_path"],
            )

            self.assertTrue(os.path.isfile(invalid_path))
            self.assertFalse(os.path.exists(journal))
            self.assertFalse(os.path.exists(f"{fixture['lib_dir']}.runtime-backup"))

    def test_restore_runtime_does_not_publish_when_staged_clr_smoke_fails(self):
        package_data = _zip_bytes({TEST_PACKAGE_DLL_PATH: TEST_DLL_DATA})
        with tempfile.TemporaryDirectory() as tmpdir:
            fixture = _write_runtime_fixture(tmpdir, package_data=package_data)
            fixture["bridge_checker"] = lambda _lib_dir: ["broken CLR graph"]
            with mock.patch.object(setup, "_publish_runtime") as publish:
                with self.assertRaisesRegex(setup.SetupError, "broken CLR graph"):
                    setup.restore_runtime(
                        **fixture,
                        urlopen=lambda *_a, **_k: _FakeResponse(package_data),
                    )

            publish.assert_not_called()


def _entry(file_path, size=3, sha256=None, source=None):
    return {
        "file": file_path,
        "sha256": sha256 or hashlib.sha256(b"abc").hexdigest(),
        "size": size,
        "required": True,
        "source": source or {
            "type": "runtime-lock",
            "lock_file": "runtime-lock.json",
            "package": "Test.Package",
            "version": "1.0.0",
            "package_path": f"lib/net472/{os.path.basename(file_path)}",
        },
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


def _write_runtime_fixture(directory, package_data=None):
    package_data = package_data or _zip_bytes({TEST_PACKAGE_DLL_PATH: TEST_DLL_DATA})
    manifest_path = os.path.join(directory, "lib_manifest.json")
    runtime_lock_path = os.path.join(directory, "runtime-lock.json")
    lib_dir = os.path.join(directory, "lib")
    manifest_entry = _entry(
        f"lib/{TEST_DLL_NAME}",
        size=len(TEST_DLL_DATA),
        sha256=hashlib.sha256(TEST_DLL_DATA).hexdigest(),
        source={
            "type": "runtime-lock",
            "lock_file": os.path.basename(runtime_lock_path),
            "package": "Test.Package",
            "version": "1.2.3",
            "package_path": TEST_PACKAGE_DLL_PATH,
        },
    )
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump({"manifest_version": setup.MANIFEST_VERSION, "files": [manifest_entry]}, f)
    lock_entry = {
        "file": f"lib/{TEST_DLL_NAME}",
        "package": "Test.Package",
        "version": "1.2.3",
        "package_path": TEST_PACKAGE_DLL_PATH,
        "nupkg_sha256": hashlib.sha256(package_data).hexdigest(),
        "license": "MIT",
    }
    with open(runtime_lock_path, "w", encoding="utf-8") as f:
        json.dump({
            "schema_version": 1,
            "target_framework": "net472",
            "source_commit": "0" * 40,
            "files": [lock_entry],
        }, f)
    return {
        "runtime_lock_path": runtime_lock_path,
        "manifest_path": manifest_path,
        "lib_dir": lib_dir,
        "bridge_checker": lambda _lib_dir: [],
    }


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._data


if __name__ == "__main__":
    unittest.main()
