from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dateutil.parser import isoparse


UTC = ZoneInfo("UTC")
FAILURE_OVERRIDE = "BLOCKED: Claude API unavailable"
PARSE_FAILURE_OVERRIDE = "BLOCKED: Claude response parsing failed"
LOG_FILENAME_TIMESTAMP = re.compile(r"(?:^|_)(\d{8}(?:_\d{6})?)(?:\.json)?$")


def parse_utc_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None

    try:
        parsed = isoparse(value.strip())
    except (TypeError, ValueError):
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _timestamp_slug(value: datetime) -> str:
    return value.strftime("%Y%m%d_%H%M%S_%f")


def _json_utc(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _entry_sort_key(path: Path, entry: Mapping[str, Any], *, index: int = 0) -> tuple[float, int]:
    modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    recorded_at = infer_recorded_at(path, entry, modified_at=modified_at) or modified_at
    return (recorded_at.timestamp(), index)


def _coerce_signal_entries(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return []

    entries = payload.get("entries")
    if isinstance(entries, list):
        return [dict(item) for item in entries if isinstance(item, Mapping)]

    return [dict(payload)]


def _latest_entry_from_entries(path: Path, entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not entries:
        return None
    indexed = enumerate(entries)
    _, latest = max(indexed, key=lambda item: _entry_sort_key(path, item[1], index=item[0]))
    return latest


def read_signal_log_entries(path: Path) -> list[dict[str, Any]]:
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    return _coerce_signal_entries(payload)


def latest_signal_log_entry(path: Path) -> dict[str, Any] | None:
    entries = read_signal_log_entries(path)
    return _latest_entry_from_entries(path, entries)


def read_signal_log_entry(
    path: Path,
    *,
    entry_id: str | None = None,
    signal_timestamp: str | None = None,
) -> dict[str, Any] | None:
    entries = read_signal_log_entries(path)
    if not entries:
        return None

    if entry_id:
        for entry in reversed(entries):
            if str(entry.get("log_entry_id") or "").strip() == entry_id:
                return entry

    if signal_timestamp:
        for entry in reversed(entries):
            if str(entry.get("timestamp") or "").strip() == signal_timestamp:
                return entry

    return latest_signal_log_entry(path)


def write_signal_log(signal: Mapping[str, Any], prefix: str = "signal", log_dir: Path | None = None) -> Path:
    """Persist any analysis result, including fallback/API-failure payloads."""
    timestamp = datetime.now(tz=UTC)
    date_slug = timestamp.strftime("%Y%m%d")
    output_dir = log_dir or Path("logs")
    output_file = output_dir / f"{prefix}_{date_slug}.json"
    output_file.parent.mkdir(exist_ok=True)

    payload = dict(signal)
    payload["logged_at_utc"] = _json_utc(timestamp)
    payload["log_filename"] = output_file.name
    payload["log_entry_id"] = _timestamp_slug(timestamp)

    container: dict[str, Any] = {
        "log_type": prefix,
        "log_date_utc": timestamp.strftime("%Y-%m-%d"),
        "entries": [],
    }
    if output_file.exists():
        with open(output_file, encoding="utf-8") as f:
            existing_payload = json.load(f)
        existing_entries = _coerce_signal_entries(existing_payload)
        if isinstance(existing_payload, Mapping) and isinstance(existing_payload.get("entries"), list):
            container = dict(existing_payload)
        container["entries"] = existing_entries

    container.setdefault("log_type", prefix)
    container.setdefault("log_date_utc", timestamp.strftime("%Y-%m-%d"))
    container["entries"].append(payload)
    container["entry_count"] = len(container["entries"])
    container["last_logged_at_utc"] = payload["logged_at_utc"]

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(container, f, indent=2)

    if isinstance(signal, dict):
        signal["logged_at_utc"] = payload["logged_at_utc"]
        signal["log_filename"] = payload["log_filename"]
        signal["log_entry_id"] = payload["log_entry_id"]

    return output_file


def parse_filename_datetime(path: Path) -> datetime | None:
    match = LOG_FILENAME_TIMESTAMP.search(path.name)
    if not match:
        return None

    try:
        value = match.group(1)
        fmt = "%Y%m%d_%H%M%S" if "_" in value else "%Y%m%d"
        return datetime.strptime(value, fmt).replace(tzinfo=UTC)
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
    entries = _coerce_signal_entries(data)
    metadata_source = data
    if entries:
        latest_entry = _latest_entry_from_entries(path, entries)
        if latest_entry is not None:
            metadata_source = latest_entry

    recorded_at_dt = infer_recorded_at(path, metadata_source, modified_at=modified_at)
    age_seconds = None
    if recorded_at_dt is not None:
        age_seconds = max(int((now_utc - recorded_at_dt).total_seconds()), 0)

    is_stale = age_seconds is None or age_seconds > stale_after_seconds
    failure = is_signal_failure(metadata_source)

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
