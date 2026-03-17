from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from signal_log_utils import build_signal_log_metadata, infer_recorded_at, parse_utc_datetime


UTC = ZoneInfo("UTC")


class SignalLogUtilsTests(unittest.TestCase):
    def test_parse_utc_datetime_accepts_z_suffix(self):
        parsed = parse_utc_datetime("2026-03-12T12:04:00Z")
        self.assertEqual(parsed, datetime(2026, 3, 12, 12, 4, tzinfo=UTC))

    def test_infer_recorded_at_falls_back_to_filename(self):
        path = Path("signal_20260313_125455.json")
        recorded_at = infer_recorded_at(path, {})
        self.assertEqual(recorded_at, datetime(2026, 3, 13, 12, 54, 55, tzinfo=UTC))

    def test_metadata_marks_old_success_as_stale(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "signal_20260312_120454.json"
            payload = {"timestamp": "2026-03-12T12:04:00Z", "signal": {"confidence": 35}}
            path.write_text(json.dumps(payload))

            metadata = build_signal_log_metadata(
                path,
                payload,
                now_utc=datetime(2026, 3, 16, 14, 43, 58, tzinfo=UTC),
                stale_after_seconds=3600,
            )

        self.assertEqual(metadata["recorded_at"], "2026-03-12T12:04:00+00:00")
        self.assertTrue(metadata["is_stale"])
        self.assertEqual(metadata["status"], "STALE")
        self.assertGreater(metadata["age_seconds"], 3600)

    def test_metadata_marks_recent_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "signal_20260313_125455.json"
            payload = {
                "error": "credit balance too low",
                "validator_overrides": ["BLOCKED: Claude API unavailable"],
            }
            path.write_text(json.dumps(payload))

            metadata = build_signal_log_metadata(
                path,
                payload,
                now_utc=datetime(2026, 3, 13, 12, 55, 0, tzinfo=UTC),
                stale_after_seconds=3600,
            )

        self.assertFalse(metadata["is_stale"])
        self.assertEqual(metadata["status"], "FAILED")


if __name__ == "__main__":
    unittest.main()
