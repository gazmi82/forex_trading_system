from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.runtime_health import (
    begin_demo_loop_iteration,
    complete_demo_loop_iteration,
    fail_demo_loop_iteration,
    get_demo_loop_health,
    load_demo_loop_heartbeat,
    stop_demo_loop,
)


class RuntimeHealthTests(unittest.TestCase):
    def test_completed_iteration_reports_healthy(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            begin_demo_loop_iteration(log_dir, loop_count=1)
            complete_demo_loop_iteration(
                log_dir,
                session="NY Kill Zone",
                runtime_mode="ENTRY_ANALYSIS",
                analysis_allowed_now=True,
                schedule_reason="Allowed trade window",
                next_poll_seconds=600,
                price=1.1542,
                open_trades_count=0,
                signal_summary={"direction": "BUY", "confluence_score": 72},
            )

            health = get_demo_loop_health(log_dir)
            heartbeat = load_demo_loop_heartbeat(log_dir)

        self.assertEqual(health["status"], "HEALTHY")
        self.assertEqual(health["state"], "SLEEPING")
        self.assertEqual(heartbeat["session"], "NY Kill Zone")
        self.assertEqual(heartbeat["last_signal"]["direction"], "BUY")

    def test_health_reports_stalled_when_expected_run_is_missed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            begin_demo_loop_iteration(log_dir, loop_count=2)
            complete_demo_loop_iteration(
                log_dir,
                session="London Close",
                runtime_mode="ENTRY_ANALYSIS",
                analysis_allowed_now=True,
                schedule_reason="Allowed trade window",
                next_poll_seconds=600,
                price=1.155,
                open_trades_count=1,
            )
            future = datetime.now(timezone.utc) + timedelta(seconds=1801)

            health = get_demo_loop_health(log_dir, now_utc=future)

        self.assertEqual(health["status"], "STALLED")
        self.assertIn("Missed expected next run", health["reason"])

    def test_begin_iteration_sees_previous_stall(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            begin_demo_loop_iteration(log_dir, loop_count=3)
            complete_demo_loop_iteration(
                log_dir,
                session="London Session",
                runtime_mode="MONITOR_ONLY",
                analysis_allowed_now=False,
                schedule_reason="Outside allowed trade window",
                next_poll_seconds=60,
                price=1.15,
                open_trades_count=0,
            )
            future = datetime.now(timezone.utc) + timedelta(seconds=301)

            health_before = begin_demo_loop_iteration(log_dir, loop_count=4)
            health_at_future = get_demo_loop_health(log_dir, now_utc=future)

        self.assertEqual(health_before["status"], "HEALTHY")
        self.assertEqual(health_at_future["status"], "STALLED")

    def test_failed_iteration_reports_degraded(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            begin_demo_loop_iteration(log_dir, loop_count=5)
            fail_demo_loop_iteration(
                log_dir,
                error="market_data_fetch_failed: timeout",
                retry_after_seconds=60,
                session="NY Kill Zone",
                runtime_mode="ENTRY_ANALYSIS",
            )

            health = get_demo_loop_health(log_dir)

        self.assertEqual(health["status"], "DEGRADED")
        self.assertEqual(health["state"], "ERROR_WAIT")
        self.assertEqual(health["last_error"], "market_data_fetch_failed: timeout")

    def test_stopped_loop_reports_stopped(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            begin_demo_loop_iteration(log_dir, loop_count=6)
            stop_demo_loop(log_dir, reason="stopped_by_operator")

            health = get_demo_loop_health(log_dir)

        self.assertEqual(health["status"], "STOPPED")
        self.assertEqual(health["reason"], "stopped_by_operator")


if __name__ == "__main__":
    unittest.main()
