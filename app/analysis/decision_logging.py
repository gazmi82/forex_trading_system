from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.runtime_store import append_decision
from app.core.runtime_sync import sync_decision

_CALIBRATION_MIN_SAMPLES = 50


def log_analysis(
    log_dir: Path,
    *,
    pair: str,
    market_data: dict,
    signal: dict,
    retrieved_chunks: dict,
    raw_response: str | None = None,
    user_message: str | None = None,
    model: str | None = None,
    system_prompt: str | None = None,
) -> None:
    log_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "pair": pair,
        "price": market_data.get("price"),
        "signal": signal.get("signal", {}),
        "confluence_score": signal.get("confluence_score", 0),
        "signal_strength": signal.get("signal_strength", "NEUTRAL"),
        "execution_allowed": signal.get("execution_allowed", False),
        "execution_direction": signal.get("execution_direction", "NEUTRAL"),
        "reasoning": signal.get("reasoning", []),
        "key_risk": signal.get("key_risk", ""),
        "overrides": signal.get("validator_overrides", []),
        "rag_chunks_used": sum(len(value) for value in retrieved_chunks.values()),
        "rag_categories": list(retrieved_chunks.keys()),
        "knowledge_sources": signal.get("knowledge_sources_used", []),
    }

    log_file = log_dir / "agent_decisions.jsonl"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry) + "\n")
    append_decision(log_entry)
    sync_decision(log_entry, log_dir=log_dir)

    claude_score = _safe_score(signal.get("confluence_score"))
    direction = (signal.get("signal") or {}).get("direction", "NEUTRAL")
    session = signal.get("session", "")
    calibration_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "signal_timestamp": signal.get("timestamp", ""),
        "signal_log_filename": signal.get("log_filename", ""),
        "signal_log_entry_id": signal.get("log_entry_id", ""),
        "pair": pair,
        "session": session,
        "claude_score": claude_score,
        "direction": direction,
        "outcome": None,
    }
    cal_file = log_dir / "score_calibration.jsonl"
    with open(cal_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(calibration_entry) + "\n")

    live_capture = {
        "captured_at": datetime.utcnow().isoformat(),
        "analysis_source": "CLAUDE_LIVE",
        "pair": pair,
        "session": session,
        "signal_timestamp": signal.get("timestamp", ""),
        "signal_log_filename": signal.get("log_filename", ""),
        "signal_log_entry_id": signal.get("log_entry_id", ""),
        "model": model or "",
        "system_prompt_sha256": _sha256_text(system_prompt),
        "user_message_sha256": _sha256_text(user_message),
        "user_message": user_message or "",
        "raw_response": raw_response or "",
        "runtime_issue": signal.get("do_not_trade_reason") or signal.get("error") or "",
        "signal_summary": {
            "direction": direction,
            "confidence": (signal.get("signal") or {}).get("confidence", 0),
            "confluence_score": claude_score,
            "execution_allowed": signal.get("execution_allowed", False),
            "execution_direction": signal.get("execution_direction", "NEUTRAL"),
        },
        "validated_signal": _make_json_safe(signal),
        "market_snapshot": _build_market_snapshot(market_data),
        "rag_summary": {
            "chunks_used": sum(len(value) for value in retrieved_chunks.values()),
            "categories": list(retrieved_chunks.keys()),
            "knowledge_sources": signal.get("knowledge_sources_used", []),
        },
        "outcome": None,
    }
    capture_file = log_dir / "live_validation_capture.jsonl"
    with open(capture_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(live_capture) + "\n")


def attach_live_validation_log_reference(log_dir: Path, signal: dict[str, Any]) -> bool:
    capture_file = Path(log_dir) / "live_validation_capture.jsonl"
    if not capture_file.exists():
        return False

    signal_timestamp = str(signal.get("timestamp") or "").strip()
    pair = str(signal.get("pair") or "").strip()
    log_filename = str(signal.get("log_filename") or "").strip()
    log_entry_id = str(signal.get("log_entry_id") or "").strip()
    if not signal_timestamp or not log_filename or not log_entry_id:
        return False

    rows = _load_jsonl_rows(capture_file)
    for index in range(len(rows) - 1, -1, -1):
        row = rows[index]
        if (
            str(row.get("signal_timestamp") or "").strip() == signal_timestamp
            and str(row.get("pair") or "").strip() == pair
        ):
            row["signal_log_filename"] = log_filename
            row["signal_log_entry_id"] = log_entry_id
            _write_jsonl_rows(capture_file, rows)
            return True
    return False


def update_calibration_outcome(
    log_dir: Path,
    trade_record: dict[str, Any],
    *,
    min_resolved_samples: int = _CALIBRATION_MIN_SAMPLES,
) -> bool:
    cal_file = Path(log_dir) / "score_calibration.jsonl"
    if not cal_file.exists():
        return False

    rows = _load_jsonl_rows(cal_file)
    if not rows:
        return False

    match_index = _find_matching_calibration_entry(rows, trade_record)
    if match_index is None:
        return False

    rows[match_index]["outcome"] = trade_record.get("outcome")
    rows[match_index]["pnl_r"] = trade_record.get("pnl_r")
    rows[match_index]["pnl_usd"] = trade_record.get("pnl_usd")
    rows[match_index]["closed_at"] = datetime.utcnow().isoformat()
    _write_jsonl_rows(cal_file, rows)
    _write_calibration_report(Path(log_dir), rows, min_resolved_samples=min_resolved_samples)
    return True


def update_live_validation_outcome(log_dir: Path, trade_record: dict[str, Any]) -> bool:
    capture_file = Path(log_dir) / "live_validation_capture.jsonl"
    if not capture_file.exists():
        return False

    rows = _load_jsonl_rows(capture_file)
    if not rows:
        return False

    signal_log_entry_id = str(trade_record.get("signal_log_entry_id") or "").strip()
    signal_log_filename = str(trade_record.get("signal_log_filename") or "").strip()
    signal_timestamp = str(trade_record.get("signal_timestamp") or "").strip()
    pair = str(trade_record.get("pair") or "").strip()

    def matches(row: dict[str, Any]) -> bool:
        if signal_log_entry_id and signal_log_filename:
            return (
                str(row.get("signal_log_entry_id") or "").strip() == signal_log_entry_id
                and str(row.get("signal_log_filename") or "").strip() == signal_log_filename
            )
        if signal_timestamp:
            return str(row.get("signal_timestamp") or "").strip() == signal_timestamp
        return str(row.get("pair") or "").strip() == pair

    for index in range(len(rows) - 1, -1, -1):
        row = rows[index]
        if matches(row):
            row["outcome"] = trade_record.get("outcome")
            row["pnl_r"] = trade_record.get("pnl_r")
            row["pnl_usd"] = trade_record.get("pnl_usd")
            row["closed_at"] = datetime.utcnow().isoformat()
            _write_jsonl_rows(capture_file, rows)
            return True
    return False


def _load_jsonl_rows(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _write_jsonl_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def _is_unresolved_outcome(value: Any) -> bool:
    return value in (None, "", "UNKNOWN")


def _find_matching_calibration_entry(
    rows: list[dict[str, Any]],
    trade_record: dict[str, Any],
) -> int | None:
    signal_timestamp = str(trade_record.get("signal_timestamp") or "").strip()
    signal_log_filename = str(trade_record.get("signal_log_filename") or "").strip()
    signal_log_entry_id = str(trade_record.get("signal_log_entry_id") or "").strip()
    pair = str(trade_record.get("pair") or "").strip()
    session = str(trade_record.get("session") or "").strip()
    direction = str(trade_record.get("direction") or "").strip().upper()
    claude_score = trade_record.get("confluence_score")

    def matches_entry_id(row: dict[str, Any]) -> bool:
        if not signal_log_entry_id:
            return False
        return (
            str(row.get("signal_log_entry_id") or "").strip() == signal_log_entry_id
            and str(row.get("signal_log_filename") or "").strip() == signal_log_filename
        )

    def matches_signal_timestamp(row: dict[str, Any]) -> bool:
        if not signal_timestamp:
            return False
        return str(row.get("signal_timestamp") or "").strip() == signal_timestamp

    def matches_fallback_tuple(row: dict[str, Any]) -> bool:
        return (
            str(row.get("pair") or "").strip() == pair
            and str(row.get("session") or "").strip() == session
            and str(row.get("direction") or "").strip().upper() == direction
            and row.get("claude_score") == claude_score
        )

    for matcher in (matches_entry_id, matches_signal_timestamp, matches_fallback_tuple):
        for index in range(len(rows) - 1, -1, -1):
            row = rows[index]
            if _is_unresolved_outcome(row.get("outcome")) and matcher(row):
                return index
        for index in range(len(rows) - 1, -1, -1):
            row = rows[index]
            if matcher(row):
                return index

    return None


def _write_calibration_report(
    log_dir: Path,
    rows: list[dict[str, Any]],
    *,
    min_resolved_samples: int,
) -> None:
    resolved_rows = [row for row in rows if _outcome_win_flag(row.get("outcome")) is not None]
    report: dict[str, Any] = {
        "generated_at": datetime.utcnow().isoformat(),
        "resolved_samples": len(resolved_rows),
        "minimum_samples_required": min_resolved_samples,
        "status": "insufficient_samples",
    }

    if len(resolved_rows) >= min_resolved_samples:
        claude_rows = [row for row in resolved_rows if row.get("claude_score") is not None]
        wins = [_outcome_win_flag(row.get("outcome")) for row in claude_rows]
        claude_scores = [float(row.get("claude_score", 0) or 0) for row in claude_rows]
        report.update(
            {
                "status": "ready",
                "claude_vs_win_correlation": _pearson(claude_scores, wins),
                "claude_avg_win_score": _average_for_flag(claude_rows, "claude_score", 1.0),
                "claude_avg_non_win_score": _average_for_flag(claude_rows, "claude_score", 0.0),
                "claude_win_rate_by_bucket": _bucket_win_rates(claude_rows, "claude_score"),
            }
        )

    report_path = log_dir / "score_calibration_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)


def _outcome_win_flag(value: Any) -> float | None:
    outcome = str(value or "").strip().upper()
    if outcome in {"WIN", "PARTIAL_WIN"}:
        return 1.0
    if outcome in {"LOSS", "BREAKEVEN"}:
        return 0.0
    return None


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 2:
        return None

    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x <= 0 or var_y <= 0:
        return None

    return round(cov / ((var_x * var_y) ** 0.5), 4)


def _average_for_flag(
    rows: list[dict[str, Any]],
    field: str,
    flag: float,
) -> float | None:
    values = [
        float(row.get(field, 0) or 0)
        for row in rows
        if _outcome_win_flag(row.get("outcome")) == flag
    ]
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _bucket_win_rates(rows: list[dict[str, Any]], field: str) -> dict[str, dict[str, float | int]]:
    buckets = {
        "<65": lambda score: score < 65,
        "65-74": lambda score: 65 <= score <= 74,
        "75-84": lambda score: 75 <= score <= 84,
        "85+": lambda score: score >= 85,
    }
    report: dict[str, dict[str, float | int]] = {}
    for label, predicate in buckets.items():
        bucket_rows = [
            row for row in rows if predicate(float(row.get(field, 0) or 0))
        ]
        wins = [
            _outcome_win_flag(row.get("outcome"))
            for row in bucket_rows
            if _outcome_win_flag(row.get("outcome")) is not None
        ]
        sample_count = len(wins)
        win_rate = round((sum(wins) / sample_count) * 100, 2) if sample_count else None
        report[label] = {
            "samples": sample_count,
            "win_rate_pct": win_rate,
        }
    return report


def _safe_score(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _build_market_snapshot(market_data: dict[str, Any]) -> dict[str, Any]:
    return {
        "price": market_data.get("price"),
        "spread": market_data.get("spread"),
        "pair": market_data.get("pair"),
        "fetch_time": market_data.get("fetch_time"),
        "ohlcv": _make_json_safe(market_data.get("ohlcv", {})),
        "indicators": _make_json_safe(market_data.get("indicators", {})),
        "fundamental": _make_json_safe(market_data.get("fundamental", {})),
        "portfolio": _make_json_safe(market_data.get("portfolio", {})),
    }


def _make_json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _make_json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_make_json_safe(item) for item in value]
    return str(value)


def _sha256_text(value: str | None) -> str:
    text = (value or "").encode("utf-8")
    return hashlib.sha256(text).hexdigest()
