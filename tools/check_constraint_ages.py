"""Fail when an exact production constraint is newer than the supply-chain hold."""

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
CONSTRAINTS = ROOT / "constraints-known-good.txt"
MINIMUM_AGE_DAYS = 14
_PIN_RE = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s;]+)$")


def _pins(path):
    result = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        match = _PIN_RE.fullmatch(line)
        if not match:
            raise ValueError(f"constraint is not an exact package pin: {raw_line}")
        result.append(match.groups())
    return result


def _release_time(package, version):
    url = f"https://pypi.org/pypi/{package}/{version}/json"
    request = urllib.request.Request(url, headers={"User-Agent": "HeatMap constraint age check"})
    with urllib.request.urlopen(request, timeout=30) as response:
        metadata = json.load(response)
    upload_times = [
        datetime.fromisoformat(item["upload_time_iso_8601"].replace("Z", "+00:00"))
        for item in metadata.get("urls", [])
        if item.get("upload_time_iso_8601")
    ]
    if not upload_times:
        raise ValueError(f"PyPI returned no release files for {package}=={version}")
    # A release can gain a platform-specific wheel after its initial sdist.
    # Hold the entire pinned release until its newest published artifact ages in.
    return max(upload_times)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--constraints", type=Path, default=CONSTRAINTS)
    parser.add_argument("--minimum-age-days", type=int, default=MINIMUM_AGE_DAYS)
    args = parser.parse_args(argv)
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.minimum_age_days)
    failures = []
    for package, version in _pins(args.constraints):
        released = _release_time(package, version)
        age = datetime.now(timezone.utc) - released
        print(f"{package}=={version}: {age.days} days old ({released.date()})")
        if released > cutoff:
            failures.append(f"{package}=={version} is only {age.days} days old")
    if failures:
        print("Constraint age check failed:")
        for failure in failures:
            print(f"  ERROR: {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
