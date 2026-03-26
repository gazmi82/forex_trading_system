from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


HEARTBEAT_FILENAME = "demo_loop_heartbeat.json"
DEFAULT_STALL_GRACE_SECONDS = 180


def begin_demo_loop_iteration(
    log_dir: Path | None = None,
    *,
    loop_count: int,
) -> dict[str, Any]:
    output_dir = Path(log_dir or "logs")
    output_dir.mkdir(parents=True, exist_ok=True)
    now = _utc_now()
    heartbeat = load_demo_loop_heartbeat(output_dir) or {"service": "demo_loop"}
    health_before = get_demo_loop_health(output_dir, now_utc=now)

    heartbeat.update(
        {
            "service": "demo_loop",
            "state": "RUNNING",
            "loop_count": int(loop_count),
            "updated_at_utc": _iso_z(now),
            "last_started_at_utc": _iso_z(now),
        }
    )
    _write_heartbeat(output_dir, heartbeat)
    return health_before


def complete_demo_loop_iteration(
    log_dir: Path | None = None,
    *,
    session: str,
    runtime_mode: str,
    analysis_allowed_now: bool,
    schedule_reason: str,
    next_poll_seconds: int,
    price: Any,
    open_trades_count: int,
    signal_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    output_dir = Path(log_dir or "logs")
    output_dir.mkdir(parents=True, exist_ok=True)
    now = _utc_now()
    heartbeat = load_demo_loop_heartbeat(output_dir) or {"service": "demo_loop"}
    next_expected = now + timedelta(seconds=max(int(next_poll_seconds), 1))

    heartbeat.update(
        {
            "service": "demo_loop",
            "state": "SLEEPING",
            "updated_at_utc": _iso_z(now),
            "last_completed_at_utc": _iso_z(now),
            "session": session,
            "runtime_mode": runtime_mode,
            "analysis_allowed_now": bool(analysis_allowed_now),
            "schedule_reason": schedule_reason,
            "next_poll_seconds": int(next_poll_seconds),
            "next_expected_run_at_utc": _iso_z(next_expected),
            "price": price,
            "open_trades_count": int(open_trades_count),
            "last_error": None,
        }
    )
    if signal_summary is not None:
        heartbeat["last_signal"] = _make_json_safe(signal_summary)

    _write_heartbeat(output_dir, heartbeat)
    return heartbeat


def fail_demo_loop_iteration(
    log_dir: Path | None = None,
    *,
    error: str,
    retry_after_seconds: int,
    session: str | None = None,
    runtime_mode: str | None = None,
) -> dict[str, Any]:
    output_dir = Path(log_dir or "logs")
    output_dir.mkdir(parents=True, exist_ok=True)
    now = _utc_now()
    heartbeat = load_demo_loop_heartbeat(output_dir) or {"service": "demo_loop"}
    next_expected = now + timedelta(seconds=max(int(retry_after_seconds), 1))

    heartbeat.update(
        {
            "service": "demo_loop",
            "state": "ERROR_WAIT",
            "updated_at_utc": _iso_z(now),
            "last_failed_at_utc": _iso_z(now),
            "next_poll_seconds": int(retry_after_seconds),
            "next_expected_run_at_utc": _iso_z(next_expected),
            "last_error": str(error),
        }
    )
    if session is not None:
        heartbeat["session"] = session
    if runtime_mode is not None:
        heartbeat["runtime_mode"] = runtime_mode

    _write_heartbeat(output_dir, heartbeat)
    return heartbeat


def stop_demo_loop(
    log_dir: Path | None = None,
    *,
    reason: str = "stopped_by_operator",
) -> dict[str, Any]:
    output_dir = Path(log_dir or "logs")
    output_dir.mkdir(parents=True, exist_ok=True)
    now = _utc_now()
    heartbeat = load_demo_loop_heartbeat(output_dir) or {"service": "demo_loop"}
    heartbeat.update(
        {
            "service": "demo_loop",
            "state": "STOPPED",
            "updated_at_utc": _iso_z(now),
            "stopped_at_utc": _iso_z(now),
            "stop_reason": reason,
            "next_expected_run_at_utc": None,
        }
    )
    _write_heartbeat(output_dir, heartbeat)
    return heartbeat


def load_demo_loop_heartbeat(log_dir: Path | None = None) -> dict[str, Any] | None:
    path = Path(log_dir or "logs") / HEARTBEAT_FILENAME
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def get_demo_loop_health(
    log_dir: Path | None = None,
    *,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    output_dir = Path(log_dir or "logs")
    heartbeat = load_demo_loop_heartbeat(output_dir)
    now = now_utc or _utc_now()

    if not heartbeat:
        return {
            "service": "demo_loop",
            "status": "NEVER_STARTED",
            "reason": "No heartbeat file found",
            "checked_at_utc": _iso_z(now),
        }

    state = str(heartbeat.get("state") or "UNKNOWN").upper()
    next_expected = _parse_utc(heartbeat.get("next_expected_run_at_utc"))
    last_started = _parse_utc(heartbeat.get("last_started_at_utc"))
    last_completed = _parse_utc(heartbeat.get("last_completed_at_utc"))
    last_contact = last_completed or last_started
    next_poll_seconds = _safe_int(heartbeat.get("next_poll_seconds"))
    stall_grace = max(DEFAULT_STALL_GRACE_SECONDS, (next_poll_seconds or 0) * 2)

    status = "HEALTHY"
    reason = f"Loop state: {state}"
    if state == "STOPPED":
        status = "STOPPED"
        reason = str(heartbeat.get("stop_reason") or "Loop stopped")
    elif state == "ERROR_WAIT":
        status = "DEGRADED"
        reason = str(heartbeat.get("last_error") or "Loop waiting after error")
    elif next_expected is not None and now > next_expected + timedelta(seconds=stall_grace):
        status = "STALLED"
        overdue_seconds = int((now - next_expected).total_seconds())
        reason = f"Missed expected next run by {overdue_seconds}s"
    elif state == "RUNNING" and last_started is not None and now > last_started + timedelta(seconds=stall_grace):
        status = "STALLED"
        overdue_seconds = int((now - last_started).total_seconds())
        reason = f"Loop has been running without completion for {overdue_seconds}s"

    age_since_last_contact = None
    if last_contact is not None:
        age_since_last_contact = max(int((now - last_contact).total_seconds()), 0)

    result = dict(heartbeat)
    result.update(
        {
            "service": "demo_loop",
            "status": status,
            "reason": reason,
            "checked_at_utc": _iso_z(now),
            "age_since_last_contact_seconds": age_since_last_contact,
            "stall_grace_seconds": stall_grace,
        }
    )
    return result


def _write_heartbeat(log_dir: Path, payload: dict[str, Any]) -> Path:
    path = Path(log_dir) / HEARTBEAT_FILENAME
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return path


def _make_json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _make_json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_make_json_safe(item) for item in value]
    return str(value)


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _safe_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
