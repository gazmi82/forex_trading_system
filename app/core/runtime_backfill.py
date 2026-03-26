from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from app.analysis.decision_logging import _load_jsonl_rows
from app.core.runtime_store import (
    replace_closed_trades,
    replace_decisions,
    replace_open_trades_snapshot,
    replace_trade_history,
    upsert_signal,
)
from app.core.runtime_sync import (
    sync_closed_trade,
    sync_decision,
    sync_open_trades,
    sync_signal,
    sync_trade_history,
)
from app.logs.signal_logs import read_signal_log_entries


def backfill_runtime_store_from_logs(
    *,
    log_dir: Path,
    sync_remote: bool = False,
) -> dict[str, int]:
    output_dir = Path(log_dir)
    counts = {
        "signals": 0,
        "test_signals": 0,
        "decisions": 0,
        "open_trades": 0,
        "closed_trades": 0,
        "trade_history": 0,
    }

    for kind in ("signal", "test_signal"):
        for path in sorted(output_dir.glob(f"{kind}_*.json")):
            for entry in read_signal_log_entries(path):
                upsert_signal(entry, kind=kind)
                counts["signals" if kind == "signal" else "test_signals"] += 1
                if sync_remote:
                    sync_signal(entry, kind=kind, log_dir=output_dir)

    decision_rows = _load_jsonl_rows(output_dir / "agent_decisions.jsonl")
    replace_decisions(decision_rows)
    counts["decisions"] = len(decision_rows)
    if sync_remote:
        for row in decision_rows:
            sync_decision(row, log_dir=output_dir)

    open_trades = _load_open_trades(output_dir / "open_trades.json")
    replace_open_trades_snapshot(open_trades)
    counts["open_trades"] = len(open_trades)
    if sync_remote:
        sync_open_trades(open_trades, log_dir=output_dir)

    closed_rows = _load_jsonl_rows(output_dir / "closed_trades.jsonl")
    replace_closed_trades(closed_rows)
    counts["closed_trades"] = len(closed_rows)
    if sync_remote:
        for row in closed_rows:
            sync_closed_trade(row, log_dir=output_dir)

    trade_history_rows = _load_trade_history_rows(output_dir / "trades.csv")
    replace_trade_history(trade_history_rows)
    counts["trade_history"] = len(trade_history_rows)
    if sync_remote:
        for row in trade_history_rows:
            sync_trade_history(row, log_dir=output_dir)

    return counts


def _load_open_trades(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _load_trade_history_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return [
            {str(key): value for key, value in row.items() if key is not None}
            for row in reader
        ]
