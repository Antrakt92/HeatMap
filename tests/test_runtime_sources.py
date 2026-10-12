import hashlib
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import setup
import hardware_access_guard


class RuntimeSourceTests(unittest.TestCase):
    def test_runtime_sources_pin_lhm_compatible_pawnio(self):
        with open(setup.RUNTIME_SOURCES_PATH, "r", encoding="utf-8") as f:
            sources = json.load(f)

        pawnio = sources["pawnio"]
        self.assertEqual(sources["libre_hardware_monitor"]["version"], "0.9.5")
        self.assertEqual(pawnio["compatible_lhm"], "0.9.5")
        self.assertEqual(len(pawnio["sha256"]), 64)
        self.assertEqual(len(pawnio["authenticode"]["thumbprint"]), 40)
        self.assertTrue(pawnio["url"].startswith("https://github.com/namazso/PawnIO.Setup/"))

        loaded = setup._load_runtime_sources()
        self.assertEqual(loaded["pawnio"]["compatible_lhm"], "0.9.5")

    def test_runtime_sources_reject_pawnio_lhm_version_mismatch(self):
        with open(setup.RUNTIME_SOURCES_PATH, "r", encoding="utf-8") as f:
            sources = json.load(f)
        sources["pawnio"]["compatible_lhm"] = "0.9.6"
        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = os.path.join(tmpdir, "runtime_sources.json")
            with open(source_path, "w", encoding="utf-8") as f:
                json.dump(sources, f)

            with self.assertRaisesRegex(setup.SetupError, "compatibility"):
                setup._load_runtime_sources(source_path)

    def test_runtime_sources_reject_missing_signature_metadata(self):
        with open(setup.RUNTIME_SOURCES_PATH, "r", encoding="utf-8") as f:
            sources = json.load(f)
        del sources["pawnio"]["authenticode"]["thumbprint"]
        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = os.path.join(tmpdir, "runtime_sources.json")
            with open(source_path, "w", encoding="utf-8") as f:
                json.dump(sources, f)

            with self.assertRaisesRegex(setup.SetupError, "thumbprint"):
                setup._load_runtime_sources(source_path)

    def test_runtime_sources_reject_path_escaping_pawnio_version(self):
        with open(setup.RUNTIME_SOURCES_PATH, "r", encoding="utf-8") as f:
            sources = json.load(f)
        sources["pawnio"]["version"] = "../../x"
        with tempfile.TemporaryDirectory() as tmpdir:
            source_path = os.path.join(tmpdir, "runtime_sources.json")
            with open(source_path, "w", encoding="utf-8") as f:
                json.dump(sources, f)

            with self.assertRaisesRegex(setup.SetupError, "plain dotted version"):
                setup._load_runtime_sources(source_path)

    def test_lhm_bridge_checks_add_reference_and_required_types(self):
        calls = []
        clr = SimpleNamespace(AddReference=lambda path: calls.append(path))
        hardware = SimpleNamespace(Computer=object, HardwareType=object, SensorType=object)

        def import_module(name):
            return {"clr": clr, "LibreHardwareMonitor.Hardware": hardware}[name]

        messages = setup._check_lhm_bridge(import_module=import_module, lib_dir=r"C:\HeatMap\lib")

        self.assertEqual(messages, [])
        self.assertEqual(calls, [os.path.abspath(r"C:\HeatMap\lib\LibreHardwareMonitorLib.dll")])

    def test_lhm_bridge_reports_post_import_assembly_failure(self):
        clr = SimpleNamespace(AddReference=mock.Mock(side_effect=RuntimeError("bad ABI")))

        messages = setup._check_lhm_bridge(import_module=lambda _name: clr)

        self.assertEqual(len(messages), 1)
        self.assertIn("CLR bridge failed", messages[0])
        self.assertIn("bad ABI", messages[0])

    def test_staged_lhm_bridge_reports_child_process_failure(self):
        result = SimpleNamespace(returncode=1, stdout="", stderr="missing dependency")
        with mock.patch.object(setup.subprocess, "run", return_value=result) as run:
            messages = setup._check_staged_lhm_bridge(r"C:\HeatMap\staged")

        self.assertIn("missing dependency", messages[0])
        self.assertEqual(run.call_args.args[0][0], setup.sys.executable)

    def test_hardware_smoke_requires_cpu_temperature_and_always_closes(self):
        cpu_type = object()
        temperature_type = object()
        cpu = SimpleNamespace(
            HardwareType=cpu_type,
            Sensors=[SimpleNamespace(SensorType=temperature_type, Value=None)],
            SubHardware=[],
            Update=mock.Mock(),
        )
        computer = SimpleNamespace(
            Hardware=[cpu],
            Open=mock.Mock(),
            Close=mock.Mock(),
        )
        clr = SimpleNamespace(AddReference=mock.Mock())
        hardware = SimpleNamespace(
            Computer=lambda: computer,
            HardwareType=SimpleNamespace(Cpu=cpu_type),
            SensorType=SimpleNamespace(Temperature=temperature_type),
        )

        ok, messages = setup.run_hardware_smoke(
            import_module=lambda name: {"clr": clr, "LibreHardwareMonitor.Hardware": hardware}[name]
        )

        self.assertFalse(ok)
        self.assertIn("PawnIO", "\n".join(messages))
        computer.Close.assert_called_once_with()

    def test_hardware_smoke_opens_cpu_only_to_avoid_unbounded_storage_discovery(self):
        cpu_type, temperature_type = object(), object()
        cpu = SimpleNamespace(HardwareType=cpu_type,
                              Sensors=[SimpleNamespace(SensorType=temperature_type, Value=45)],
                              SubHardware=[], Update=mock.Mock())
        computer = SimpleNamespace(Hardware=[cpu], Open=mock.Mock(), Close=mock.Mock())
        hardware = SimpleNamespace(Computer=lambda: computer,
                                   HardwareType=SimpleNamespace(Cpu=cpu_type),
                                   SensorType=SimpleNamespace(Temperature=temperature_type))
        with mock.patch.object(hardware_access_guard, "require_hardware_access", return_value="full"):
            ok, messages = setup.run_hardware_smoke(import_module=lambda name: {
                "clr": SimpleNamespace(AddReference=mock.Mock()),
                "LibreHardwareMonitor.Hardware": hardware}[name])
        self.assertTrue(ok, messages)
        self.assertTrue(computer.IsCpuEnabled)
        for field in ("IsGpuEnabled", "IsStorageEnabled", "IsMemoryEnabled", "IsMotherboardEnabled"):
            self.assertFalse(getattr(computer, field), field)
        computer.Close.assert_called_once_with()

    def test_hardware_smoke_checks_driver_access_before_native_import_or_open(self):
        import_module = mock.Mock()
        with mock.patch.object(hardware_access_guard, "require_hardware_access",
                               side_effect=hardware_access_guard.HardwareAccessConflict("driver changed")) as guard:
            ok, messages = setup.run_hardware_smoke(import_module=import_module)
        self.assertFalse(ok)
        self.assertIn("driver changed", " ".join(messages))
        guard.assert_called_once_with("monitor")
        import_module.assert_not_called()

    def test_verify_pawnio_rejects_hash_before_authenticode(self):
        metadata = {
            "size": 4,
            "sha256": hashlib.sha256(b"good").hexdigest(),
            "authenticode": {},
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, "PawnIO.exe")
            with open(path, "wb") as f:
                f.write(b"evil")
            authenticode = mock.Mock(return_value=[])

            messages = setup._verify_pawnio_file(path, metadata, authenticode)

        self.assertTrue(any("hash mismatch" in message for message in messages))
        authenticode.assert_not_called()

    def test_download_pawnio_publishes_only_verified_file(self):
        data = b"verified installer"
        metadata = {
            "version": "2.0.1",
            "url": "https://example.invalid/PawnIO.exe",
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "authenticode": {},
        }
        response = _FakeResponse(data)
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            mock.patch.object(setup, "_load_runtime_sources", return_value={"pawnio": metadata}),
            mock.patch.object(setup, "_verify_pawnio_file", return_value=[]),
        ):
            path = setup.download_pawnio(
                urlopen=lambda *_args, **_kwargs: response,
                env={"LOCALAPPDATA": tmpdir},
            )

            with open(path, "rb") as f:
                self.assertEqual(f.read(), data)
            self.assertFalse(os.path.exists(f"{path}.download"))

    def test_download_pawnio_rejects_oversized_response_with_bounded_read(self):
        metadata = {"version": "2.0.1", "url": "https://example.invalid/PawnIO.exe",
                    "size": 4, "sha256": hashlib.sha256(b"good").hexdigest(),
                    "authenticode": {}}
        response = _FakeResponse(b"good plus oversized payload")
        response.read = mock.Mock(wraps=response.read)
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            mock.patch.object(setup, "_load_runtime_sources", return_value={"pawnio": metadata}),
            mock.patch.object(setup, "_verify_pawnio_file") as verify,
        ):
            with self.assertRaisesRegex(setup.SetupError, "exceeds.*locked size"):
                setup.download_pawnio(urlopen=lambda *_a, **_k: response,
                                      env={"LOCALAPPDATA": tmpdir})
            self.assertFalse(os.path.exists(setup._pawnio_download_path(metadata, {"LOCALAPPDATA": tmpdir})))
            verify.assert_not_called()
        response.read.assert_called_once_with(metadata["size"] + 1)

    def test_overlapping_pawnio_downloads_keep_separate_verified_staging_files(self):
        data = b"verified installer"
        metadata = {"version": "2.0.1", "url": "https://example.invalid/PawnIO.exe",
                    "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                    "authenticode": {}}
        staged_paths = []
        completed = []
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            mock.patch.object(setup, "_load_runtime_sources", return_value={"pawnio": metadata}),
        ):
            env = {"LOCALAPPDATA": tmpdir}
            urlopen = lambda *_a, **_k: _FakeResponse(data)

            def verify(path, _metadata):
                staged_paths.append(path)
                with open(path, "rb") as staged:
                    self.assertEqual(staged.read(), data)
                if len(staged_paths) == 1:
                    completed.append(setup.download_pawnio(urlopen=urlopen, env=env))
                return []

            with mock.patch.object(setup, "_verify_pawnio_file", side_effect=verify):
                completed.append(setup.download_pawnio(urlopen=urlopen, env=env))
            self.assertEqual(len(completed), 2)
            self.assertEqual(len(set(staged_paths)), 2)
            self.assertEqual(completed[0], completed[1])
            with open(completed[0], "rb") as downloaded:
                self.assertEqual(downloaded.read(), data)
            self.assertEqual(os.listdir(os.path.dirname(completed[0])), [os.path.basename(completed[0])])


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size=-1):
        return self._data if size < 0 else self._data[:size]


if __name__ == "__main__":
    unittest.main()
