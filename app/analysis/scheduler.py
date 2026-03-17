from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo


ENTRY_ANALYSIS_INTERVAL_SECONDS = 600
MONITOR_ONLY_INTERVAL_SECONDS = 1800
OPEN_TRADE_MONITOR_INTERVAL_SECONDS = 600
ALLOWED_ENTRY_SESSIONS = {"London Kill Zone", "NY Kill Zone", "London Close"}
ENTRY_WINDOW_START_HOURS_NY = (3, 8, 10)


def _outside_trade_window_reason(session: str) -> str:
    reasons = {
        "Asian Session": "Outside allowed trade window — Asian session",
        "London Session": "Outside allowed trade window — London session between entry windows",
        "New York Session": "Outside allowed trade window — New York session after London close",
        "Low Liquidity": "Outside allowed trade window — lunch hours / low liquidity",
        "Weekend": "Weekend block",
        "Unknown": "Outside allowed trade window — session unavailable",
    }
    return reasons.get(session, "Outside allowed trade window")


def _open_trades_count(market_data: dict[str, Any]) -> int:
    portfolio = market_data.get("portfolio", {})
    raw_value = portfolio.get("open_trades", 0)
    try:
        return max(int(raw_value), 0)
    except (TypeError, ValueError):
        return 0


def get_demo_loop_schedule_state(
    market_data: dict[str, Any],
    *,
    now_ny: datetime | None = None,
) -> dict[str, Any]:
    """Return the runtime scheduler state shared by the demo loop and API."""
    fundamental = market_data.get("fundamental", {})
    session = fundamental.get("active_session", "Unknown")
    trade_window_active = (
        bool(fundamental.get("trade_window_active")) and session in ALLOWED_ENTRY_SESSIONS
    )
    open_trades_count = _open_trades_count(market_data)

    if now_ny is None:
        now_ny = datetime.now(ZoneInfo("America/New_York"))
    weekday_name = now_ny.strftime("%A")

    if now_ny.weekday() >= 5:
        return {
            "analysis_allowed_now": False,
            "session": session,
            "next_poll_seconds": MONITOR_ONLY_INTERVAL_SECONDS,
            "schedule_reason": f"Weekend block ({weekday_name})",
            "runtime_mode": "WEEKEND_BLOCK",
            "trade_management_active": open_trades_count > 0,
            "open_trades_count": open_trades_count,
            "trade_window_active": False,
        }

    if trade_window_active:
        return {
            "analysis_allowed_now": True,
            "session": session,
            "next_poll_seconds": ENTRY_ANALYSIS_INTERVAL_SECONDS,
            "schedule_reason": "Allowed trade window",
            "runtime_mode": "ENTRY_ANALYSIS",
            "trade_management_active": open_trades_count > 0,
            "open_trades_count": open_trades_count,
            "trade_window_active": True,
        }

    schedule_reason = _outside_trade_window_reason(session)
    runtime_mode = "MONITOR_ONLY"
    next_poll_seconds = MONITOR_ONLY_INTERVAL_SECONDS

    if open_trades_count > 0:
        runtime_mode = "MONITOR_OPEN_TRADES"
        next_poll_seconds = OPEN_TRADE_MONITOR_INTERVAL_SECONDS
        trade_label = "trade" if open_trades_count == 1 else "trades"
        schedule_reason = (
            f"{schedule_reason} — monitoring {open_trades_count} open {trade_label}"
        )

    return {
        "analysis_allowed_now": False,
        "session": session,
        "next_poll_seconds": next_poll_seconds,
        "schedule_reason": schedule_reason,
        "runtime_mode": runtime_mode,
        "trade_management_active": open_trades_count > 0,
        "open_trades_count": open_trades_count,
        "trade_window_active": False,
    }


def get_next_entry_window_start_ny(now_ny: datetime, analysis_allowed_now: bool) -> str | None:
    if analysis_allowed_now:
        return None

    candidate = now_ny.replace(second=0, microsecond=0)
    for day_offset in range(0, 8):
        day = candidate + timedelta(days=day_offset)
        if day.weekday() >= 5:
            continue
        for hour in ENTRY_WINDOW_START_HOURS_NY:
            slot = day.replace(hour=hour, minute=0)
            if slot > now_ny:
                return slot.isoformat()
    return None


def get_demo_loop_schedule(market_data: dict[str, Any]) -> tuple[bool, str, int, str]:
    schedule = get_demo_loop_schedule_state(market_data)
    return (
        schedule["analysis_allowed_now"],
        schedule["session"],
        schedule["next_poll_seconds"],
        schedule["schedule_reason"],
    )
