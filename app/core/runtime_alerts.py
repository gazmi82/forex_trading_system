from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


ALERTS_FILENAME = "alerts.jsonl"
ALERT_STATE_FILENAME = "runtime_alert_state.json"
_HEALTH_ALERT_COOLDOWN_SECONDS = 900

_RUNTIME_EVENT_RULES: tuple[dict[str, Any], ...] = (
    {
        "id": "claude_provider_degraded",
        "components": {"analysis.agent"},
        "actions": {"claude_api_call", "claude_circuit_open", "claude_circuit_opened"},
        "threshold": 2,
        "window_seconds": 600,
        "cooldown_seconds": 1800,
        "severity": "WARNING",
        "summary": "Claude provider degraded",
    },
    {
        "id": "market_data_provider_degraded",
        "components": {"brokers.oanda", "api.live_snapshot_service", "cli.demo_loop"},
        "actions": {
            "request_candles",
            "live_market_data_build",
            "get_oanda_builder",
            "build_live_snapshot",
            "refresh_snapshot_cache",
            "market_data_fetch",
        },
        "threshold": 2,
        "window_seconds": 900,
        "cooldown_seconds": 1800,
        "severity": "WARNING",
        "summary": "Market-data provider degraded",
    },
    {
        "id": "execution_provider_degraded",
        "components": {"execution.trade_executor"},
        "actions": {
            "execute_signal",
            "monitor_open_trades",
            "check_order_filled",
            "close_partial",
            "move_stop_loss",
            "close_trade",
        },
        "threshold": 2,
        "window_seconds": 900,
        "cooldown_seconds": 1800,
        "severity": "WARNING",
        "summary": "Execution path degraded",
    },
)


def process_runtime_event_for_alerts(
    event: dict[str, Any],
    *,
    log_dir: Path | None = None,
) -> dict[str, Any] | None:
    output_dir = Path(log_dir or "logs")
    output_dir.mkdir(parents=True, exist_ok=True)
    state = _load_alert_state(output_dir)
    event_time = _parse_utc(event.get("timestamp_utc")) or _utc_now()
    triggered_alert: dict[str, Any] | None = None

    for rule in _matching_rules(event):
        rule_id = str(rule["id"])
        history = [
            stamp
            for stamp in state["event_windows"].get(rule_id, [])
            if event_time - stamp <= timedelta(seconds=int(rule["window_seconds"]))
        ]
        history.append(event_time)
        state["event_windows"][rule_id] = history

        last_alerted = state["last_alerted_at"].get(rule_id)
        if len(history) < int(rule["threshold"]):
            continue
        if _cooldown_active(last_alerted, event_time, int(rule["cooldown_seconds"])):
            continue

        triggered_alert = record_runtime_alert(
            alert_key=rule_id,
            severity=str(rule["severity"]),
            summary=str(rule["summary"]),
            details={
                "matched_component": event.get("component"),
                "matched_action": event.get("action"),
                "recent_failure_count": len(history),
                "window_seconds": int(rule["window_seconds"]),
                "last_event_message": event.get("message"),
                "last_event_level": event.get("level"),
            },
            log_dir=output_dir,
            timestamp_utc=event_time,
        )
        state["last_alerted_at"][rule_id] = event_time

    _write_alert_state(output_dir, state)
    return triggered_alert


def process_health_status_for_alerts(
    health: dict[str, Any],
    *,
    log_dir: Path | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any] | None:
    status = str(health.get("status") or "").upper()
    if status not in {"STALLED", "DEGRADED"}:
        return None

    output_dir = Path(log_dir or "logs")
    output_dir.mkdir(parents=True, exist_ok=True)
    state = _load_alert_state(output_dir)
    current_time = now_utc or _utc_now()
    alert_key = f"demo_loop_{status.lower()}"
    last_alerted = state["health_last_alerted_at"].get(alert_key)
    if _cooldown_active(last_alerted, current_time, _HEALTH_ALERT_COOLDOWN_SECONDS):
        return None

    alert = record_runtime_alert(
        alert_key=alert_key,
        severity="WARNING",
        summary=f"Demo loop {status.lower()}",
        details={
            "reason": health.get("reason"),
            "state": health.get("state"),
            "session": health.get("session"),
            "runtime_mode": health.get("runtime_mode"),
            "last_error": health.get("last_error"),
            "next_expected_run_at_utc": health.get("next_expected_run_at_utc"),
        },
        log_dir=output_dir,
        timestamp_utc=current_time,
    )
    state["health_last_alerted_at"][alert_key] = current_time
    _write_alert_state(output_dir, state)
    return alert


def record_runtime_alert(
    *,
    alert_key: str,
    severity: str,
    summary: str,
    details: dict[str, Any] | None = None,
    log_dir: Path | None = None,
    timestamp_utc: datetime | None = None,
) -> dict[str, Any]:
    output_dir = Path(log_dir or "logs")
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp_utc": _iso_z(timestamp_utc or _utc_now()),
        "alert_key": str(alert_key),
        "severity": str(severity).upper(),
        "summary": str(summary),
        "details": _make_json_safe(details or {}),
    }

    path = output_dir / ALERTS_FILENAME
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")
    return payload


def load_recent_alerts(
    log_dir: Path | None = None,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    path = Path(log_dir or "logs") / ALERTS_FILENAME
    if not path.exists():
        return []

    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows[-limit:]


def _matching_rules(event: dict[str, Any]) -> list[dict[str, Any]]:
    component = str(event.get("component") or "")
    action = str(event.get("action") or "")
    return [
        rule
        for rule in _RUNTIME_EVENT_RULES
        if component in rule["components"] and action in rule["actions"]
    ]


def _load_alert_state(log_dir: Path) -> dict[str, Any]:
    path = log_dir / ALERT_STATE_FILENAME
    if not path.exists():
        return _empty_alert_state()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_alert_state()
    if not isinstance(payload, dict):
        return _empty_alert_state()

    event_windows = {
        str(key): [stamp for stamp in (_parse_utc_list(values))]
        for key, values in dict(payload.get("event_windows") or {}).items()
    }
    last_alerted_at = {
        str(key): stamp
        for key, stamp in (
            (key, _parse_utc(value)) for key, value in dict(payload.get("last_alerted_at") or {}).items()
        )
        if stamp is not None
    }
    health_last_alerted_at = {
        str(key): stamp
        for key, stamp in (
            (key, _parse_utc(value)) for key, value in dict(payload.get("health_last_alerted_at") or {}).items()
        )
        if stamp is not None
    }
    return {
        "event_windows": event_windows,
        "last_alerted_at": last_alerted_at,
        "health_last_alerted_at": health_last_alerted_at,
    }


def _write_alert_state(log_dir: Path, state: dict[str, Any]) -> Path:
    path = log_dir / ALERT_STATE_FILENAME
    payload = {
        "event_windows": {
            str(key): [_iso_z(stamp) for stamp in values]
            for key, values in dict(state.get("event_windows") or {}).items()
        },
        "last_alerted_at": {
            str(key): _iso_z(value)
            for key, value in dict(state.get("last_alerted_at") or {}).items()
        },
        "health_last_alerted_at": {
            str(key): _iso_z(value)
            for key, value in dict(state.get("health_last_alerted_at") or {}).items()
        },
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    return path


def _cooldown_active(last_alerted_at: datetime | None, now_utc: datetime, cooldown_seconds: int) -> bool:
    if last_alerted_at is None:
        return False
    return now_utc - last_alerted_at < timedelta(seconds=max(int(cooldown_seconds), 0))


def _empty_alert_state() -> dict[str, Any]:
    return {
        "event_windows": {},
        "last_alerted_at": {},
        "health_last_alerted_at": {},
    }


def _parse_utc_list(values: Any) -> list[datetime]:
    if not isinstance(values, list):
        return []
    parsed: list[datetime] = []
    for value in values:
        stamp = _parse_utc(value)
        if stamp is not None:
            parsed.append(stamp)
    return parsed


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


def _make_json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _make_json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_make_json_safe(item) for item in value]
    return str(value)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
