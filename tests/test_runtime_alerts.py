from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from app.core.runtime_alerts import (
    load_recent_alerts,
    process_health_status_for_alerts,
    process_runtime_event_for_alerts,
)
from app.core.runtime_logging import record_runtime_event


class RuntimeAlertTests(unittest.TestCase):
    def test_record_runtime_event_raises_alert_after_repeated_claude_failures(self):
        with TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)

            record_runtime_event(
                component="analysis.agent",
                action="claude_api_call",
                message="Claude API call failed",
                log_dir=log_dir,
            )
            self.assertEqual(load_recent_alerts(log_dir), [])

            record_runtime_event(
                component="analysis.agent",
                action="claude_api_call",
                message="Claude API call failed again",
                log_dir=log_dir,
            )

            alerts = load_recent_alerts(log_dir)

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["alert_key"], "claude_provider_degraded")
        self.assertEqual(alerts[0]["severity"], "WARNING")

    def test_runtime_event_alert_respects_cooldown(self):
        with TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            start = datetime(2026, 3, 26, 14, 0, tzinfo=timezone.utc)

            process_runtime_event_for_alerts(
                {
                    "timestamp_utc": start.isoformat().replace("+00:00", "Z"),
                    "level": "ERROR",
                    "component": "analysis.agent",
                    "action": "claude_api_call",
                    "message": "first failure",
                },
                log_dir=log_dir,
            )
            alert = process_runtime_event_for_alerts(
                {
                    "timestamp_utc": (start + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
                    "level": "ERROR",
                    "component": "analysis.agent",
                    "action": "claude_api_call",
                    "message": "second failure",
                },
                log_dir=log_dir,
            )
            suppressed = process_runtime_event_for_alerts(
                {
                    "timestamp_utc": (start + timedelta(minutes=2)).isoformat().replace("+00:00", "Z"),
                    "level": "ERROR",
                    "component": "analysis.agent",
                    "action": "claude_api_call",
                    "message": "third failure",
                },
                log_dir=log_dir,
            )

            alerts = load_recent_alerts(log_dir)

        self.assertIsNotNone(alert)
        self.assertIsNone(suppressed)
        self.assertEqual(len(alerts), 1)

    def test_health_alert_records_stalled_loop(self):
        with TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            start = datetime(2026, 3, 26, 14, 0, tzinfo=timezone.utc)
            health = {
                "status": "STALLED",
                "reason": "Missed expected next run by 1200s",
                "state": "SLEEPING",
                "session": "NY Kill Zone",
                "runtime_mode": "ENTRY_ANALYSIS",
                "last_error": None,
                "next_expected_run_at_utc": "2026-03-26T13:40:00Z",
            }

            alert = process_health_status_for_alerts(health, log_dir=log_dir, now_utc=start)
            suppressed = process_health_status_for_alerts(
                health,
                log_dir=log_dir,
                now_utc=start + timedelta(minutes=5),
            )
            alerts = load_recent_alerts(log_dir)

        self.assertIsNotNone(alert)
        self.assertIsNone(suppressed)
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["alert_key"], "demo_loop_stalled")


if __name__ == "__main__":
    unittest.main()
