# Contributing to HeatMap

[Overview](README.md) · [Setup](docs/SETUP.md) · [Technical reference](docs/REFERENCE.md)

## Report a problem

Use [GitHub Issues](https://github.com/Antrakt92/HeatMap/issues). Include the
version, relevant hardware, Windows version, reproduction steps and expected
result. Redact device identifiers and private paths from diagnostics.

## Set up and check

Follow the [Windows setup guide](docs/SETUP.md). From the repository root, run:

```powershell
.venv\Scripts\python -m unittest discover -s tests
.venv\Scripts\python -m compileall -q overlay.py setup.py tests
.venv\Scripts\python setup.py --verify
git diff --check
```

These checks cover modeled behavior, syntax and bundled-runtime integrity.
Sensor, desktop, elevation and fan-control changes also need checks on the
affected Windows hardware. Describe those results separately from unit tests.

## Source layout

| Location | Contents |
|---|---|
| `overlay.py` | Window, settings and sensor presentation |
| Root controller and probe modules | Fan control, hardware access and storage reads |
| `setup.py`, runtime manifests and constraints | Verified runtime setup and dependency pins |
| `tools/`, `tests/` | Diagnostic helpers and regression coverage |
| `docs/` | Setup, technical reference and historical investigation reports |

Keep changes focused and add regression coverage for behavior fixes. Preserve
runtime hashes, hardware identity checks and controller-restoration behavior.
Do not commit real diagnostic output, local configuration or downloaded installers.
