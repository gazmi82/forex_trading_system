from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from app.api.models import LogEnvelope
from app.core.config import LOGS_DIR
from app.logs.signal_logs import build_signal_log_metadata, infer_recorded_at


def read_json(path: Path) -> dict[str, Any]:
    with open(path) as f:
        return json.load(f)


def latest_file(pattern: str, *, logs_dir: Path = LOGS_DIR) -> Path | None:
    matches = sorted(logs_dir.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def latest_signal_file(
    kind: Literal["signal", "test_signal"],
    *,
    logs_dir: Path = LOGS_DIR,
) -> Path | None:
    matches = list(logs_dir.glob(f"{kind}_*.json"))
    if not matches:
        return None

    def _sort_key(path: Path) -> tuple[float, float]:
        data = read_json(path)
        modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=ZoneInfo("UTC"))
        recorded_at = infer_recorded_at(path, data, modified_at=modified_at) or modified_at
        return (recorded_at.timestamp(), modified_at.timestamp())

    return max(matches, key=_sort_key)


def latest_snapshot_file(*, logs_dir: Path = LOGS_DIR) -> Path | None:
    return latest_file("live_data_check_*.json", logs_dir=logs_dir)


def load_jsonl_tail(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    rows: list[dict[str, Any]] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows[-limit:]


def load_csv_tail(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    with open(path) as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    return rows[-limit:]


def log_envelope(
    path: Path,
    *,
    now_utc: datetime,
    stale_after_seconds: int,
) -> LogEnvelope:
    data = read_json(path)
    metadata = build_signal_log_metadata(
        path,
        data,
        now_utc=now_utc,
        stale_after_seconds=stale_after_seconds,
    )
    return LogEnvelope(
        filename=path.name,
        modified_at=metadata["modified_at"],
        recorded_at=metadata["recorded_at"],
        age_seconds=metadata["age_seconds"],
        is_stale=metadata["is_stale"],
        status=metadata["status"],
        data=data,
    )
