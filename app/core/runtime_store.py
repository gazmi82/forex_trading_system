from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def runtime_store_path(path: Path | None = None) -> Path:
    if path is not None:
        return Path(path)
    configured = os.getenv("RUNTIME_STORE_PATH", "").strip()
    if configured:
        return Path(configured)
    return Path("runtime_store") / "runtime_store.sqlite3"


def upsert_signal(payload: dict[str, Any], *, kind: str, db_path: Path | None = None) -> None:
    store_path = runtime_store_path(db_path)
    recorded_at = _recorded_at(payload)
    signal_id = str(payload.get("log_entry_id") or "").strip() or f"{kind}:{recorded_at}"
    with _connect(store_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO signals (
                signal_id, kind, recorded_at, log_filename, payload_json
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                signal_id,
                kind,
                recorded_at,
                str(payload.get("log_filename") or ""),
                json.dumps(_json_safe(payload), ensure_ascii=True),
            ),
        )
        conn.commit()


def latest_signal(*, kind: str, db_path: Path | None = None) -> dict[str, Any] | None:
    store_path = runtime_store_path(db_path)
    with _connect(store_path) as conn:
        row = conn.execute(
            """
            SELECT payload_json
            FROM signals
            WHERE kind = ?
            ORDER BY recorded_at DESC, rowid DESC
            LIMIT 1
            """,
            (kind,),
        ).fetchone()
    if row is None:
        return None
    return json.loads(row[0])


def append_decision(payload: dict[str, Any], *, db_path: Path | None = None) -> None:
    store_path = runtime_store_path(db_path)
    with _connect(store_path) as conn:
        conn.execute(
            """
            INSERT INTO decisions (recorded_at, payload_json)
            VALUES (?, ?)
            """,
            (
                _recorded_at(payload),
                json.dumps(_json_safe(payload), ensure_ascii=True),
            ),
        )
        conn.commit()


def replace_decisions(rows: list[dict[str, Any]], *, db_path: Path | None = None) -> None:
    _replace_payload_rows("decisions", rows, db_path=db_path)


def latest_decisions(*, limit: int, db_path: Path | None = None) -> list[dict[str, Any]]:
    store_path = runtime_store_path(db_path)
    with _connect(store_path) as conn:
        rows = conn.execute(
            """
            SELECT payload_json
            FROM decisions
            ORDER BY recorded_at DESC, id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [json.loads(row[0]) for row in rows]


def replace_open_trades_snapshot(trades: dict[str, Any], *, db_path: Path | None = None) -> None:
    store_path = runtime_store_path(db_path)
    with _connect(store_path) as conn:
        conn.execute("DELETE FROM open_trades")
        now = _iso_z(_utc_now())
        for key, payload in trades.items():
            conn.execute(
                """
                INSERT INTO open_trades (trade_key, updated_at, payload_json)
                VALUES (?, ?, ?)
                """,
                (
                    str(key),
                    now,
                    json.dumps(_json_safe(payload), ensure_ascii=True),
                ),
            )
        conn.commit()


def current_open_trades(*, db_path: Path | None = None) -> list[dict[str, Any]]:
    store_path = runtime_store_path(db_path)
    with _connect(store_path) as conn:
        rows = conn.execute(
            """
            SELECT payload_json
            FROM open_trades
            ORDER BY updated_at DESC, trade_key ASC
            """
        ).fetchall()
    return [json.loads(row[0]) for row in rows]


def append_closed_trade(payload: dict[str, Any], *, db_path: Path | None = None) -> None:
    _append_payload_row("closed_trades", payload, db_path=db_path)


def latest_closed_trades(*, limit: int, db_path: Path | None = None) -> list[dict[str, Any]]:
    return _latest_payload_rows("closed_trades", limit=limit, db_path=db_path)


def replace_closed_trades(rows: list[dict[str, Any]], *, db_path: Path | None = None) -> None:
    _replace_payload_rows("closed_trades", rows, db_path=db_path)


def append_trade_history(payload: dict[str, Any], *, db_path: Path | None = None) -> None:
    _append_payload_row("trade_history", payload, db_path=db_path)


def latest_trade_history(*, limit: int, db_path: Path | None = None) -> list[dict[str, Any]]:
    return _latest_payload_rows("trade_history", limit=limit, db_path=db_path)


def replace_trade_history(rows: list[dict[str, Any]], *, db_path: Path | None = None) -> None:
    _replace_payload_rows("trade_history", rows, db_path=db_path)


def _append_payload_row(table: str, payload: dict[str, Any], *, db_path: Path | None = None) -> None:
    store_path = runtime_store_path(db_path)
    with _connect(store_path) as conn:
        conn.execute(
            f"""
            INSERT INTO {table} (recorded_at, payload_json)
            VALUES (?, ?)
            """,
            (
                _recorded_at(payload),
                json.dumps(_json_safe(payload), ensure_ascii=True),
            ),
        )
        conn.commit()


def _latest_payload_rows(table: str, *, limit: int, db_path: Path | None = None) -> list[dict[str, Any]]:
    store_path = runtime_store_path(db_path)
    with _connect(store_path) as conn:
        rows = conn.execute(
            f"""
            SELECT payload_json
            FROM {table}
            ORDER BY recorded_at DESC, id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    return [json.loads(row[0]) for row in rows]


def _replace_payload_rows(table: str, rows: list[dict[str, Any]], *, db_path: Path | None = None) -> None:
    store_path = runtime_store_path(db_path)
    with _connect(store_path) as conn:
        conn.execute(f"DELETE FROM {table}")
        for payload in rows:
            conn.execute(
                f"""
                INSERT INTO {table} (recorded_at, payload_json)
                VALUES (?, ?)
                """,
                (
                    _recorded_at(payload),
                    json.dumps(_json_safe(payload), ensure_ascii=True),
                ),
            )
        conn.commit()


def _connect(path: Path) -> sqlite3.Connection:
    store_path = Path(path)
    store_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(store_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS signals (
            signal_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            recorded_at TEXT NOT NULL,
            log_filename TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_signals_kind_recorded_at
        ON signals(kind, recorded_at DESC)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS open_trades (
            trade_key TEXT PRIMARY KEY,
            updated_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS closed_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trade_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )


def _recorded_at(payload: dict[str, Any]) -> str:
    for key in ("logged_at_utc", "timestamp", "captured_at", "date"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return _iso_z(_utc_now())


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
