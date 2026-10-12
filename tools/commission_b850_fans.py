"""Explicit B850 SYS1/2/3 response test, verified handback and opt-in activation."""
import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import overlay
from b850_fans import BOARD, TARGETS
from case_fans import FanWorkerClient
from enable_case_fans import close_previous_overlay, verify_worker
from hardware_access_guard import require_hardware_access


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable", action="store_true")
    args = parser.parse_args()
    directory = Path(os.environ["LOCALAPPDATA"]) / "HeatMap"
    result = dict(state="checking", started=time.time(), board=BOARD, samples=[])
    report = directory / "b850-case-activation.json"
    def save():
        temporary = report.with_suffix(".tmp")
        temporary.write_text(json.dumps(result, indent=2), encoding="utf-8")
        temporary.replace(report)
    save()
    try:
        if not overlay._is_admin() or overlay._supported_case_fan_board(True) is not True:
            raise RuntimeError("Administrator access and the exact B850 board are required")
        require_hardware_access("independent_case_control")
        close_previous_overlay()
        config, error = overlay.load_config_result()
        if error:
            raise RuntimeError(error)
        for key in ("cpu_fan_reference_rpm", "gpu_fan_max_rpm"):
            if not isinstance(config.get(key), (int, float)) or not 200 <= config[key] <= 10000:
                raise RuntimeError(f"Verified CPU/GPU RPM reference required: {key}")
        client = FanWorkerClient(str(ROOT), b850=True, commission=True)
        result["restore"] = verify_worker(client, result["samples"], duration=6)
        reference = result["samples"][-1].get("verified_full_rpm")
        if not isinstance(reference, dict) or set(reference) != set(TARGETS):
            raise RuntimeError("B850 full-speed evidence missing")
        result.update(state="commissioned_and_restored", full_rpm=reference)
        if args.enable:
            backup = directory / f"config-before-b850-case-{time.time_ns()}.json"
            backup.write_text(json.dumps(config, indent=2), encoding="utf-8")
            result["config_backup"] = str(backup)
            config.update(case_fans_b850_enabled=True, case_fans_enabled=True,
                          case_fans_shared_enabled=False, case_fan_full_rpm=reference)
            saved, message = overlay.save_config(config)
            if not saved:
                raise RuntimeError(message)
            result["state"] = "enabled_after_verified_restore"
    except Exception as exc:
        result.update(state="error", reason=str(exc))
    finally:
        result["finished"] = time.time()
        save()
    return 1 if result["state"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
