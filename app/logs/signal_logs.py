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
LEGACY_LOG_FILENAME = re.compile(r"^(?P<prefix>.+)_(?P<date>\d{8})_(?P<time>\d{6})\.json$")
LEGACY_ARCHIVE_DIRNAME = "legacy_signal_archive"


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


def _daily_output_file(output_dir: Path, prefix: str, date_slug: str) -> Path:
    return output_dir / f"{prefix}_{date_slug}.json"


def _entry_sort_key(path: Path, entry: Mapping[str, Any], *, index: int = 0) -> tuple[float, int]:
    if path.exists():
        modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
    else:
        modified_at = parse_filename_datetime(path) or datetime.fromtimestamp(0, tz=UTC)
    recorded_at = infer_recorded_at(path, entry, modified_at=modified_at) or modified_at
    return (recorded_at.timestamp(), index)


def _coerce_signal_entries(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return []

    entries = payload.get("entries")
    if isinstance(entries, list):
        return [dict(item) for item in entries if isinstance(item, Mapping)]

    return [dict(payload)]


def _load_container(path: Path, prefix: str, date_slug: str) -> dict[str, Any]:
    container: dict[str, Any] = {
        "log_type": prefix,
        "log_date_utc": datetime.strptime(date_slug, "%Y%m%d").strftime("%Y-%m-%d"),
        "entries": [],
    }
    if not path.exists():
        return container

    with open(path, encoding="utf-8") as f:
        existing_payload = json.load(f)

    existing_entries = _coerce_signal_entries(existing_payload)
    if isinstance(existing_payload, Mapping) and isinstance(existing_payload.get("entries"), list):
        container = dict(existing_payload)
    container["entries"] = existing_entries
    container.setdefault("log_type", prefix)
    container.setdefault("log_date_utc", datetime.strptime(date_slug, "%Y%m%d").strftime("%Y-%m-%d"))
    return container


def _entry_identity(entry: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(entry.get("legacy_log_filename") or "").strip(),
        str(entry.get("log_entry_id") or "").strip(),
        str(entry.get("timestamp") or "").strip(),
        str(entry.get("logged_at_utc") or "").strip(),
    )


def _archive_legacy_file(path: Path, output_dir: Path):
    archive_dir = output_dir / LEGACY_ARCHIVE_DIRNAME
    archive_dir.mkdir(exist_ok=True)
    archive_path = archive_dir / path.name
    if archive_path.exists():
        archive_path.unlink()
    path.replace(archive_path)


def _normalize_legacy_entry(
    entry: Mapping[str, Any],
    *,
    legacy_path: Path,
    daily_filename: str,
    index: int,
) -> dict[str, Any]:
    normalized = dict(entry)
    recorded_at = infer_recorded_at(legacy_path, normalized)
    if recorded_at is not None:
        normalized.setdefault("logged_at_utc", _json_utc(recorded_at))
    normalized["log_filename"] = daily_filename
    normalized.setdefault("legacy_log_filename", legacy_path.name)
    if not str(normalized.get("log_entry_id") or "").strip():
        base = legacy_path.stem.replace(".", "_")
        normalized["log_entry_id"] = f"legacy_{base}_{index}"
    return normalized


def _consolidate_legacy_logs(
    *,
    output_dir: Path,
    prefix: str,
    date_slug: str,
    daily_path: Path,
    container: dict[str, Any],
) -> bool:
    legacy_paths = sorted(output_dir.glob(f"{prefix}_{date_slug}_*.json"))
    if not legacy_paths:
        return False

    entries = container.setdefault("entries", [])
    known_entry_ids = {_entry_identity(entry) for entry in entries if isinstance(entry, Mapping)}
    migrated_files = {
        str(name).strip()
        for name in container.get("migrated_legacy_files", [])
        if str(name).strip()
    }
    changed = False

    for legacy_path in legacy_paths:
        if not legacy_path.is_file() or legacy_path == daily_path:
            continue

        if legacy_path.name in migrated_files:
            _archive_legacy_file(legacy_path, output_dir)
            changed = True
            continue

        try:
            with open(legacy_path, encoding="utf-8") as f:
                payload = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue

        legacy_entries = _coerce_signal_entries(payload)
        imported_any = False
        for index, entry in enumerate(legacy_entries):
            normalized = _normalize_legacy_entry(
                entry,
                legacy_path=legacy_path,
                daily_filename=daily_path.name,
                index=index,
            )
            identity = _entry_identity(normalized)
            if identity in known_entry_ids:
                continue
            entries.append(normalized)
            known_entry_ids.add(identity)
            imported_any = True

        if imported_any or legacy_entries:
            migrated_files.add(legacy_path.name)
            _archive_legacy_file(legacy_path, output_dir)
            changed = True

    if migrated_files:
        container["migrated_legacy_files"] = sorted(migrated_files)

    return changed


def _latest_entry_from_entries(path: Path, entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not entries:
        return None
    indexed = enumerate(entries)
    _, latest = max(indexed, key=lambda item: _entry_sort_key(path, item[1], index=item[0]))
    return latest


def _resolve_signal_log_path(path: Path) -> Path:
    if path.exists():
        return path

    archive_path = path.parent / LEGACY_ARCHIVE_DIRNAME / path.name
    if archive_path.exists():
        return archive_path

    match = LEGACY_LOG_FILENAME.match(path.name)
    if match:
        daily_path = _daily_output_file(path.parent, match.group("prefix"), match.group("date"))
        if daily_path.exists():
            return daily_path

    return path


def read_signal_log_entries(path: Path) -> list[dict[str, Any]]:
    resolved_path = _resolve_signal_log_path(path)
    with open(resolved_path, encoding="utf-8") as f:
        payload = json.load(f)
    return _coerce_signal_entries(payload)


def latest_signal_log_entry(path: Path) -> dict[str, Any] | None:
    resolved_path = _resolve_signal_log_path(path)
    entries = read_signal_log_entries(resolved_path)
    return _latest_entry_from_entries(resolved_path, entries)


def read_signal_log_entry(
    path: Path,
    *,
    entry_id: str | None = None,
    signal_timestamp: str | None = None,
) -> dict[str, Any] | None:
    """
    Return one concrete analysis entry from either a daily aggregate file or a
    legacy timestamped file reference.

    Resolution order matters:
    1. explicit log_entry_id
    2. explicit signal timestamp
    3. legacy filename mapping after consolidation
    4. latest entry in the resolved container
    """
    resolved_path = _resolve_signal_log_path(path)
    entries = read_signal_log_entries(resolved_path)
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

    if resolved_path != path:
        for entry in reversed(entries):
            if str(entry.get("legacy_log_filename") or "").strip() == path.name:
                return entry

    return _latest_entry_from_entries(resolved_path, entries)


def consolidate_signal_logs(
    prefix: str = "signal",
    *,
    log_dir: Path | None = None,
    date_slug: str | None = None,
) -> Path | None:
    """
    Merge legacy per-analysis files for one UTC day into the daily aggregate.

    This is intentionally safe to run repeatedly. Already-migrated files are
    archived and duplicate entries are ignored by identity checks.
    """
    output_dir = log_dir or Path("logs")
    target_date_slug = date_slug or datetime.now(tz=UTC).strftime("%Y%m%d")
    daily_path = _daily_output_file(output_dir, prefix, target_date_slug)
    container = _load_container(daily_path, prefix, target_date_slug)

    if not _consolidate_legacy_logs(
        output_dir=output_dir,
        prefix=prefix,
        date_slug=target_date_slug,
        daily_path=daily_path,
        container=container,
    ):
        return daily_path if daily_path.exists() else None

    container["entry_count"] = len(container["entries"])
    if container["entries"]:
        latest_entry = _latest_entry_from_entries(daily_path, container["entries"])
        if latest_entry is not None:
            container["last_logged_at_utc"] = latest_entry.get("logged_at_utc")

    daily_path.parent.mkdir(exist_ok=True)
    with open(daily_path, "w", encoding="utf-8") as f:
        json.dump(container, f, indent=2)

    return daily_path


def write_signal_log(signal: Mapping[str, Any], prefix: str = "signal", log_dir: Path | None = None) -> Path:
    """
    Append one analysis result into the daily JSON container for its UTC date.

    The function also mutates dict inputs in place with `logged_at_utc`,
    `log_filename`, and `log_entry_id` so callers can carry an exact reference
    into trade timelines and feedback records.
    """
    timestamp = datetime.now(tz=UTC)
    date_slug = timestamp.strftime("%Y%m%d")
    output_dir = log_dir or Path("logs")
    output_file = _daily_output_file(output_dir, prefix, date_slug)
    output_file.parent.mkdir(exist_ok=True)

    payload = dict(signal)
    payload["logged_at_utc"] = _json_utc(timestamp)
    payload["log_filename"] = output_file.name
    payload["log_entry_id"] = _timestamp_slug(timestamp)

    container = _load_container(output_file, prefix, date_slug)
    # Always fold older timestamped files into the daily container before writing
    # the new entry so readers only need one canonical location going forward.
    _consolidate_legacy_logs(
        output_dir=output_dir,
        prefix=prefix,
        date_slug=date_slug,
        daily_path=output_file,
        container=container,
    )
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
    """
    Classify the freshness and health of a signal log for API/dashboard use.

    Metadata is derived from the newest entry inside an aggregate file when
    available, so a daily container reflects the latest loop state rather than
    its filesystem creation time.
    """
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
