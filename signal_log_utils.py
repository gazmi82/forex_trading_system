from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo


UTC = ZoneInfo("UTC")
FAILURE_OVERRIDE = "BLOCKED: Claude API unavailable"
PARSE_FAILURE_OVERRIDE = "BLOCKED: Claude response parsing failed"
LOG_FILENAME_TIMESTAMP = re.compile(r"(?:^|_)(\d{8}_\d{6})(?:\.json)?$")


def parse_utc_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None

    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_filename_datetime(path: Path) -> datetime | None:
    match = LOG_FILENAME_TIMESTAMP.search(path.name)
    if not match:
        return None

    try:
        return datetime.strptime(match.group(1), "%Y%m%d_%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def infer_recorded_at(
    path: Path,
    data: Mapping[str, Any],
    *,
    modified_at: datetime | None = None,
) -> datetime | None:
    for key in ("logged_at_utc", "timestamp"):
        parsed = parse_utc_datetime(data.get(key))
        if parsed is not None:
            return parsed

    parsed = parse_filename_datetime(path)
    if parsed is not None:
        return parsed

    return modified_at


def is_signal_failure(data: Mapping[str, Any]) -> bool:
    if data.get("error"):
        return True

    overrides = data.get("validator_overrides")
    if not isinstance(overrides, list):
        return False

    return FAILURE_OVERRIDE in overrides or PARSE_FAILURE_OVERRIDE in overrides


def build_signal_log_metadata(
    path: Path,
    data: Mapping[str, Any],
    *,
    now_utc: datetime,
    stale_after_seconds: int,
) -> dict[str, Any]:
    modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    recorded_at_dt = infer_recorded_at(path, data, modified_at=modified_at)
    age_seconds = None
    if recorded_at_dt is not None:
        age_seconds = max(int((now_utc - recorded_at_dt).total_seconds()), 0)

    is_stale = age_seconds is None or age_seconds > stale_after_seconds
    failure = is_signal_failure(data)

    if failure and is_stale:
        status = "STALE_FAILED"
    elif failure:
        status = "FAILED"
    elif is_stale:
        status = "STALE"
    else:
        status = "OK"

    return {
        "modified_at": modified_at.isoformat(),
        "recorded_at": recorded_at_dt.isoformat() if recorded_at_dt else None,
        "age_seconds": age_seconds,
        "is_stale": is_stale,
        "status": status,
    }
