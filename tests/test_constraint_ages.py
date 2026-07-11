import io
import json
import unittest
from unittest import mock

from tools import check_constraint_ages


class ConstraintAgeTests(unittest.TestCase):
    def test_release_time_uses_newest_release_artifact(self):
        payload = {
            "urls": [
                {"upload_time_iso_8601": "2026-01-01T00:00:00Z"},
                {"upload_time_iso_8601": "2026-07-10T12:30:00Z"},
            ]
        }
        response = io.BytesIO(json.dumps(payload).encode("utf-8"))

        with mock.patch.object(check_constraint_ages.urllib.request, "urlopen", return_value=response):
            released = check_constraint_ages._release_time("Example", "1.0")

        self.assertEqual(released.isoformat(), "2026-07-10T12:30:00+00:00")


if __name__ == "__main__":
    unittest.main()
