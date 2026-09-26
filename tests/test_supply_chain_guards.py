"""Supply-chain guards: orphan artifacts, version pins, preflight integrity."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import setup


class OrphanArtifactGuardTests(unittest.TestCase):
    def test_repo_root_contains_no_orphan_pawnio_installer(self):
        orphan = Path(setup.APP_DIR) / "PawnIO_setup.exe"
        if orphan.exists():
            self.fail(
                "orphan PawnIO_setup.exe found in the repository root; "
                "the single source of truth for the PawnIO installer is "
                "'python setup.py --download-pawnio' pinned by runtime_sources.json "
                "(size + sha256 + Authenticode) — remove the orphan instead of "
                "keeping an untracked binary next to the tree."
            )

    def test_repo_contains_no_executable_orphans(self):
        orphans = sorted(
            str(path.relative_to(setup.APP_DIR)) for path in Path(setup.APP_DIR).rglob("*.exe")
            if path.is_file() and ".venv" not in path.parts
        )
        self.assertEqual(
            orphans,
            [],
            "orphan executables in the repository; the runtime is restored "
            "from runtime-lock.json and the PawnIO installer via "
            "'python setup.py --download-pawnio', never from committed binaries.",
        )


class LockedVersionPinTests(unittest.TestCase):
    def test_constraints_header_names_the_locked_lhm_runtime(self):
        # Per-file LHM 0.9.5 pins are covered by test_runtime_sources.py and
        # test_setup_metadata.py; this guard covers the remaining surface:
        # the human-readable verified-environment header.
        with open(setup.CONSTRAINTS_PATH, "r", encoding="utf-8") as f:
            header = [line for line in f.read().splitlines() if line.startswith("#")]
        self.assertTrue(
            any("0.9.5" in line for line in header),
            "constraints-known-good.txt header must name the locked LHM 0.9.5 runtime",
        )

    def test_lhm_pin_agrees_across_lock_sources_and_constraints(self):
        with open(setup.RUNTIME_SOURCES_PATH, "r", encoding="utf-8") as f:
            sources = json.load(f)
        _runtime_lock, entries = setup.load_runtime_lock()
        self.assertEqual(sources["libre_hardware_monitor"]["version"], "0.9.5")
        self.assertEqual(entries["lib/librehardwaremonitorlib.dll"]["version"], "0.9.5")
        self.assertEqual(sources["pawnio"]["compatible_lhm"], "0.9.5")


class PreflightSupplyChainTests(unittest.TestCase):
    def test_preflight_fails_when_shared_fan_module_is_tampered(self):
        with (
            mock.patch.object(setup, "_unsupported_runtime_message", return_value=None),
            mock.patch.object(setup, "_check_preflight_dependencies", return_value=[]),
            mock.patch.object(setup, "verify_lib_manifest", return_value=(True, [])),
            mock.patch.object(setup, "_check_lhm_bridge", return_value=[]),
            mock.patch(
                "pawnio_shared.verified_module",
                side_effect=RuntimeError("Shared fan module integrity check failed"),
            ),
        ):
            ok, messages = setup.run_preflight()

        self.assertFalse(ok)
        self.assertTrue(
            any("Shared fan module verification failed" in message for message in messages),
            messages,
        )

    def test_preflight_accepts_intact_shared_fan_module(self):
        with (
            mock.patch.object(setup, "_unsupported_runtime_message", return_value=None),
            mock.patch.object(setup, "_check_preflight_dependencies", return_value=[]),
            mock.patch.object(setup, "verify_lib_manifest", return_value=(True, [])),
            mock.patch.object(setup, "_check_lhm_bridge", return_value=[]),
        ):
            ok, messages = setup.run_preflight()

        self.assertTrue(ok, messages)

    def test_preflight_rejects_extra_dlls_for_elevated_launch(self):
        with (
            mock.patch.object(setup, "_unsupported_runtime_message", return_value=None),
            mock.patch.object(setup, "_check_preflight_dependencies", return_value=[]),
            mock.patch.object(
                setup, "verify_lib_manifest", return_value=(True, [])
            ) as verify,
            mock.patch.object(setup, "_check_lhm_bridge", return_value=[]),
            mock.patch("pawnio_shared.verified_module", return_value=b"ok"),
        ):
            ok, _messages = setup.run_preflight()

        self.assertTrue(ok)
        verify.assert_called_once_with(allow_extra_dlls=False)


class SharedFanModuleSourceTests(unittest.TestCase):
    def _write_sources(self, tmpdir, mutate):
        with open(setup.RUNTIME_SOURCES_PATH, "r", encoding="utf-8") as f:
            sources = json.load(f)
        mutate(sources)
        path = os.path.join(tmpdir, "runtime_sources.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sources, f)
        return path

    def test_rejects_shared_module_hash_mismatch_against_lock(self):
        def mutate(sources):
            sources["shared_fan_module"]["sha256"] = "ab" * 32

        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_sources(tmpdir, mutate)
            with self.assertRaisesRegex(setup.SetupError, "does not match runtime-lock"):
                setup._load_runtime_sources(path)

    def test_rejects_shared_module_size_mismatch_against_lock(self):
        def mutate(sources):
            sources["shared_fan_module"]["size"] += 1

        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write_sources(tmpdir, mutate)
            with self.assertRaisesRegex(setup.SetupError, "does not match runtime-lock"):
                setup._load_runtime_sources(path)


if __name__ == "__main__":
    unittest.main()
