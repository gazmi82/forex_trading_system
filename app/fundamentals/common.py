from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone


def cache_fresh(cached_at: datetime | None, max_age: timedelta) -> bool:
    return cached_at is not None and (datetime.now(timezone.utc) - cached_at) < max_age


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None

    cleaned = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def humanize_delta(target_time: datetime, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    total_minutes = int((target_time - now).total_seconds() // 60)
    is_past = total_minutes < 0
    total_minutes = abs(total_minutes)
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        text = f"{hours} hour{'s' if hours != 1 else ''} {minutes} minutes"
    elif hours:
        text = f"{hours} hour{'s' if hours != 1 else ''}"
    else:
        text = f"{minutes} minutes"
    return f"{text} ago" if is_past else text


def relative_minutes(value: str | None) -> int | None:
    if not value:
        return None

    text = value.lower()
    mins_match = re.search(r"(\d+)\s*min", text)
    hours_match = re.search(r"(\d+)\s*hour", text)
    total_minutes = 0
    if hours_match:
        total_minutes += int(hours_match.group(1)) * 60
    if mins_match:
        total_minutes += int(mins_match.group(1))
    if total_minutes == 0:
        return None
    return -total_minutes if "ago" in text else total_minutes


def classify_news_risk(event_name: str | None, time_to_event: str | None) -> str:
    event_name = event_name or ""
    if event_name.startswith("CLEAR"):
        return "CLEAR"
    if event_name.startswith("MANUAL_CHECK"):
        return "HIGH"

    minutes = relative_minutes(time_to_event)
    if minutes is None:
        return "LOW"
    if -30 <= minutes <= 30:
        return "HIGH"
    if 30 < minutes <= 240:
        return "MEDIUM"
    return "LOW"


def is_high_impact_event(event_name: str, raw_importance) -> bool:
    text = str(raw_importance or "").strip().lower()
    if text:
        if text.isdigit() and int(text) >= 3:
            return True
        if text in {"high", "3", "high impact"}:
            return True

    lowered = event_name.lower()
    high_impact_keywords = (
        "nonfarm",
        "nfp",
        "cpi",
        "consumer price",
        "fomc",
        "interest rate",
        "rate decision",
        "ecb",
        "gdp",
        "retail sales",
        "pmi",
        "ifo",
        "powell",
        "lagarde",
        "payrolls",
        "inflation",
    )
    return any(keyword in lowered for keyword in high_impact_keywords)


def format_rate_differential(diff: float) -> str:
    if diff > 0:
        return f"+{diff:.2f}% USD favor supports bearish EUR/USD bias"
    if diff < 0:
        return f"{diff:.2f}% EUR favor supports bullish EUR/USD bias"
    return "0.00% rate differential — neutral macro rate bias"
