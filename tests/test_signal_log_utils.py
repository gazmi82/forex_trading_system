from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.logs import (
    build_signal_log_metadata,
    consolidate_signal_logs as packaged_consolidate_signal_logs_from_init,
    infer_recorded_at,
    latest_signal_log_entry,
    parse_utc_datetime,
    read_signal_log_entries,
    read_signal_log_entry,
    write_signal_log,
)
from app.logs.signal_logs import (
    build_signal_log_metadata as packaged_build_signal_log_metadata,
    consolidate_signal_logs,
    infer_recorded_at as packaged_infer_recorded_at,
    latest_signal_log_entry as packaged_latest_signal_log_entry,
    parse_utc_datetime as packaged_parse_utc_datetime,
    read_signal_log_entries as packaged_read_signal_log_entries,
    read_signal_log_entry as packaged_read_signal_log_entry,
    write_signal_log as packaged_write_signal_log,
)


UTC = ZoneInfo("UTC")


class SignalLogUtilsTests(unittest.TestCase):
    def test_app_logs_init_reexports_signal_log_helpers(self):
        self.assertIs(build_signal_log_metadata, packaged_build_signal_log_metadata)
        self.assertIs(packaged_consolidate_signal_logs_from_init, consolidate_signal_logs)
        self.assertIs(infer_recorded_at, packaged_infer_recorded_at)
        self.assertIs(latest_signal_log_entry, packaged_latest_signal_log_entry)
        self.assertIs(parse_utc_datetime, packaged_parse_utc_datetime)
        self.assertIs(read_signal_log_entries, packaged_read_signal_log_entries)
        self.assertIs(read_signal_log_entry, packaged_read_signal_log_entry)
        self.assertIs(write_signal_log, packaged_write_signal_log)

    def test_parse_utc_datetime_accepts_z_suffix(self):
        parsed = parse_utc_datetime("2026-03-12T12:04:00Z")
        self.assertEqual(parsed, datetime(2026, 3, 12, 12, 4, tzinfo=UTC))

    def test_infer_recorded_at_falls_back_to_filename(self):
        path = Path("signal_20260313_125455.json")
        recorded_at = infer_recorded_at(path, {})
        self.assertEqual(recorded_at, datetime(2026, 3, 13, 12, 54, 55, tzinfo=UTC))

    def test_infer_recorded_at_accepts_daily_filename(self):
        path = Path("signal_20260313.json")
        recorded_at = infer_recorded_at(path, {})
        self.assertEqual(recorded_at, datetime(2026, 3, 13, 0, 0, tzinfo=UTC))

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

    def test_write_signal_log_persists_metadata(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output = write_signal_log({"signal": {"confidence": 35}}, log_dir=Path(tmpdir))
            payload = json.loads(output.read_text())
            entries = payload["entries"]

        self.assertTrue(output.name.startswith("signal_"))
        self.assertEqual(entries[-1]["signal"]["confidence"], 35)
        self.assertIn("logged_at_utc", entries[-1])
        self.assertEqual(entries[-1]["log_filename"], output.name)
        self.assertIn("log_entry_id", entries[-1])

    def test_write_signal_log_appends_multiple_entries_to_daily_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_one = write_signal_log({"timestamp": "2026-03-12T12:04:00Z"}, log_dir=Path(tmpdir))
            output_two = write_signal_log({"timestamp": "2026-03-12T12:14:00Z"}, log_dir=Path(tmpdir))
            entries = read_signal_log_entries(output_one)

        self.assertEqual(output_one, output_two)
        self.assertEqual(len(entries), 2)
        self.assertNotEqual(entries[0]["log_entry_id"], entries[1]["log_entry_id"])

    def test_latest_signal_log_entry_reads_latest_aggregate_entry(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "signal_20260313.json"
            path.write_text(
                json.dumps(
                    {
                        "log_type": "signal",
                        "log_date_utc": "2026-03-13",
                        "entries": [
                            {
                                "timestamp": "2026-03-13T12:04:00Z",
                                "log_filename": path.name,
                                "log_entry_id": "first",
                            },
                            {
                                "timestamp": "2026-03-13T12:54:55Z",
                                "log_filename": path.name,
                                "log_entry_id": "second",
                            },
                        ],
                    }
                )
            )

            latest = latest_signal_log_entry(path)
            exact = read_signal_log_entry(path, entry_id="first")

        self.assertEqual(latest["log_entry_id"], "second")
        self.assertEqual(exact["log_entry_id"], "first")

    def test_consolidate_signal_logs_archives_legacy_files_into_daily_log(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            date_slug = datetime.now(tz=UTC).strftime("%Y%m%d")
            legacy_one = log_dir / f"signal_{date_slug}_120400.json"
            legacy_two = log_dir / f"signal_{date_slug}_121400.json"
            legacy_one.write_text(json.dumps({"signal": {"confidence": 25}}), encoding="utf-8")
            legacy_two.write_text(json.dumps({"signal": {"confidence": 35}}), encoding="utf-8")

            daily_path = consolidate_signal_logs(log_dir=log_dir, date_slug=date_slug)

            self.assertIsNotNone(daily_path)
            self.assertTrue(daily_path.exists())
            entries = read_signal_log_entries(daily_path)
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0]["log_filename"], daily_path.name)
            self.assertEqual(entries[0]["legacy_log_filename"], legacy_one.name)
            self.assertFalse(legacy_one.exists())
            self.assertFalse(legacy_two.exists())
            self.assertTrue((log_dir / "legacy_signal_archive" / legacy_one.name).exists())
            self.assertTrue((log_dir / "legacy_signal_archive" / legacy_two.name).exists())

    def test_read_signal_log_entry_resolves_archived_legacy_filename(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            date_slug = datetime.now(tz=UTC).strftime("%Y%m%d")
            legacy_name = f"signal_{date_slug}_120400.json"
            archive_dir = log_dir / "legacy_signal_archive"
            archive_dir.mkdir()
            archived_path = archive_dir / legacy_name
            archived_path.write_text(
                json.dumps({"signal": {"confidence": 42}, "log_filename": legacy_name}),
                encoding="utf-8",
            )

            entry = read_signal_log_entry(log_dir / legacy_name)

            self.assertIsNotNone(entry)
            self.assertEqual(entry["signal"]["confidence"], 42)


if __name__ == "__main__":
    unittest.main()
