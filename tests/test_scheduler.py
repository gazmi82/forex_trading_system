import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from app.analysis.scheduler import (
    ENTRY_ANALYSIS_INTERVAL_SECONDS,
    MONITOR_ONLY_INTERVAL_SECONDS,
    OPEN_TRADE_MONITOR_INTERVAL_SECONDS,
    get_next_entry_window_start_ny,
    get_demo_loop_schedule_state,
)
from scheduler import (
    ENTRY_ANALYSIS_INTERVAL_SECONDS as RootEntryAnalysisIntervalSeconds,
    get_demo_loop_schedule_state as root_get_demo_loop_schedule_state,
)


def _market_data(session: str, trade_window_active: bool, open_trades: int = 0) -> dict:
    return {
        "fundamental": {
            "active_session": session,
            "trade_window_active": trade_window_active,
        },
        "portfolio": {
            "open_trades": open_trades,
        },
    }


class SchedulerStateTests(unittest.TestCase):
    def test_root_scheduler_reexports_shared_functions(self):
        self.assertEqual(
            RootEntryAnalysisIntervalSeconds,
            ENTRY_ANALYSIS_INTERVAL_SECONDS,
        )
        self.assertIs(root_get_demo_loop_schedule_state, get_demo_loop_schedule_state)

    def test_midday_low_liquidity_blocks_new_entries(self):
        now_ny = datetime(2026, 3, 16, 12, 21, tzinfo=ZoneInfo("America/New_York"))
        schedule = get_demo_loop_schedule_state(
            _market_data("Low Liquidity", False),
            now_ny=now_ny,
        )

        self.assertFalse(schedule["analysis_allowed_now"])
        self.assertEqual(schedule["runtime_mode"], "MONITOR_ONLY")
        self.assertEqual(schedule["next_poll_seconds"], MONITOR_ONLY_INTERVAL_SECONDS)
        self.assertFalse(schedule["trade_management_active"])
        self.assertFalse(schedule["trade_window_active"])
        self.assertIn("lunch hours", schedule["schedule_reason"].lower())
        self.assertEqual(
            get_next_entry_window_start_ny(now_ny, schedule["analysis_allowed_now"]),
            "2026-03-17T03:00:00-04:00",
        )

    def test_open_trades_keep_monitoring_fast_outside_entry_window(self):
        now_ny = datetime(2026, 3, 16, 12, 21, tzinfo=ZoneInfo("America/New_York"))
        schedule = get_demo_loop_schedule_state(
            _market_data("Low Liquidity", False, open_trades=2),
            now_ny=now_ny,
        )

        self.assertFalse(schedule["analysis_allowed_now"])
        self.assertEqual(schedule["runtime_mode"], "MONITOR_OPEN_TRADES")
        self.assertEqual(schedule["next_poll_seconds"], OPEN_TRADE_MONITOR_INTERVAL_SECONDS)
        self.assertTrue(schedule["trade_management_active"])
        self.assertEqual(schedule["open_trades_count"], 2)
        self.assertIn("monitoring 2 open trades", schedule["schedule_reason"].lower())

    def test_allowed_window_enables_entry_analysis(self):
        now_ny = datetime(2026, 3, 16, 8, 30, tzinfo=ZoneInfo("America/New_York"))
        schedule = get_demo_loop_schedule_state(
            _market_data("NY Kill Zone", True),
            now_ny=now_ny,
        )

        self.assertTrue(schedule["analysis_allowed_now"])
        self.assertEqual(schedule["runtime_mode"], "ENTRY_ANALYSIS")
        self.assertEqual(schedule["next_poll_seconds"], ENTRY_ANALYSIS_INTERVAL_SECONDS)
        self.assertTrue(schedule["trade_window_active"])
        self.assertEqual(schedule["schedule_reason"], "Allowed trade window")

    def test_upcoming_entry_window_shortens_monitor_sleep(self):
        now_ny = datetime(2026, 3, 16, 7, 50, tzinfo=ZoneInfo("America/New_York"))
        schedule = get_demo_loop_schedule_state(
            _market_data("London Session", False),
            now_ny=now_ny,
        )

        self.assertFalse(schedule["analysis_allowed_now"])
        self.assertEqual(schedule["runtime_mode"], "MONITOR_ONLY")
        self.assertEqual(schedule["next_poll_seconds"], 600)
        self.assertEqual(
            get_next_entry_window_start_ny(now_ny, schedule["analysis_allowed_now"]),
            "2026-03-16T08:00:00-04:00",
        )

    def test_upcoming_entry_window_shortens_open_trade_monitor_sleep(self):
        now_ny = datetime(2026, 3, 16, 7, 55, tzinfo=ZoneInfo("America/New_York"))
        schedule = get_demo_loop_schedule_state(
            _market_data("London Session", False, open_trades=1),
            now_ny=now_ny,
        )

        self.assertFalse(schedule["analysis_allowed_now"])
        self.assertEqual(schedule["runtime_mode"], "MONITOR_OPEN_TRADES")
        self.assertEqual(schedule["next_poll_seconds"], 300)
        self.assertTrue(schedule["trade_management_active"])

    def test_weekend_overrides_raw_trade_window_flag(self):
        now_ny = datetime(2026, 3, 14, 3, 15, tzinfo=ZoneInfo("America/New_York"))
        schedule = get_demo_loop_schedule_state(
            _market_data("London Kill Zone", True),
            now_ny=now_ny,
        )

        self.assertFalse(schedule["analysis_allowed_now"])
        self.assertEqual(schedule["runtime_mode"], "WEEKEND_BLOCK")
        self.assertEqual(schedule["next_poll_seconds"], MONITOR_ONLY_INTERVAL_SECONDS)
        self.assertFalse(schedule["trade_window_active"])
        self.assertIn("weekend block", schedule["schedule_reason"].lower())


if __name__ == "__main__":
    unittest.main()
