from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def _parse_trade_date(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None

    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    for parser in (
        lambda item: datetime.fromisoformat(item),
        lambda item: datetime.strptime(item, "%Y-%m-%d"),
    ):
        try:
            parsed = parser(text)
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def iter_closed_trade_rows(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return

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
                yield row


def weekly_realized_pnl_usd(
    path: Path,
    *,
    now: datetime | None = None,
) -> float:
    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    target_week = reference.isocalendar()[:2]
    total = 0.0

    for row in iter_closed_trade_rows(path):
        trade_date = _parse_trade_date(row.get("date"))
        if trade_date is None or trade_date.isocalendar()[:2] != target_week:
            continue
        try:
            total += float(row.get("pnl_usd", 0) or 0)
        except (TypeError, ValueError):
            continue

    return round(total, 2)


def weekly_pnl_pct_from_closed_trades(
    path: Path,
    current_balance: float,
    *,
    now: datetime | None = None,
) -> float:
    try:
        balance = float(current_balance)
    except (TypeError, ValueError):
        return 0.0

    if balance <= 0 or not path.exists():
        return 0.0

    weekly_pnl_usd = weekly_realized_pnl_usd(path, now=now)
    start_balance = balance - weekly_pnl_usd
    if start_balance <= 0:
        start_balance = balance
    if start_balance <= 0:
        return 0.0

    return round((weekly_pnl_usd / start_balance) * 100, 2)
