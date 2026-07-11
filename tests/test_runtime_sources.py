import hashlib
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

import setup


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
