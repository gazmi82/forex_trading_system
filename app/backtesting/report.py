from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BacktestReportSummary:
    total_trades: int
    expectancy_r: float
    profit_factor: float | None
    win_rate: float
    output_path: str


class BacktestReportGenerator:
    def __init__(self, *, output_root: Path | None = None):
        self.output_root = Path(output_root or "backtest_results")
        self.output_root.mkdir(parents=True, exist_ok=True)

    def generate(
        self,
        trades_path: Path,
        *,
        output_path: Path | None = None,
    ) -> BacktestReportSummary:
        trades = _read_jsonl(trades_path)
        report = self.build_report(trades, source_file=str(trades_path))
        target = Path(output_path or (self.output_root / "report.json"))
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)

        return BacktestReportSummary(
            total_trades=report["total_trades"],
            expectancy_r=report["expectancy_r"],
            profit_factor=report["profit_factor"],
            win_rate=report["win_rate"],
            output_path=str(target),
        )

    def build_report(self, trades: list[dict[str, Any]], *, source_file: str) -> dict[str, Any]:
        pnls = [float(item.get("pnl_r", 0) or 0) for item in trades]
        total = len(trades)
        wins = sum(1 for value in pnls if value > 0)
        losses = sum(1 for value in pnls if value < 0)
        breakeven = sum(1 for value in pnls if value == 0)
        gross_profit = round(sum(value for value in pnls if value > 0), 4)
        gross_loss = round(sum(value for value in pnls if value < 0), 4)
        profit_factor = None if gross_loss == 0 else round(gross_profit / abs(gross_loss), 4)
        expectancy = round(sum(pnls) / total, 4) if total else 0.0
        win_rate = round(wins / total, 4) if total else 0.0

        session_breakdown = _group_stats(trades, key="session")
        score_breakdown = _bucketed_score_stats(trades)
        negative_sessions = [
            session
            for session, stats in session_breakdown.items()
            if stats["expectancy_r"] < 0
        ]

        report = {
            "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "source_file": source_file,
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "breakeven": breakeven,
            "win_rate": win_rate,
            "avg_r": expectancy,
            "expectancy_r": expectancy,
            "gross_profit_r": gross_profit,
            "gross_loss_r": gross_loss,
            "profit_factor": profit_factor,
            "max_consecutive_losses": _max_consecutive_losses(pnls),
            "max_drawdown_r": _max_drawdown(pnls),
            "session_breakdown": session_breakdown,
            "score_bucket_breakdown": score_breakdown,
            "minimum_thresholds": {
                "expectancy_positive": expectancy > 0,
                "profit_factor_above_1_3": profit_factor is not None and profit_factor > 1.3,
                "negative_expectancy_sessions": negative_sessions,
            },
            "status": _report_status(expectancy, profit_factor, negative_sessions),
        }
        return report


def report_summary_to_dict(summary: BacktestReportSummary) -> dict[str, Any]:
    return asdict(summary)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _group_stats(trades: list[dict[str, Any]], *, key: str) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        label = str(trade.get(key, "UNKNOWN") or "UNKNOWN")
        groups.setdefault(label, []).append(trade)

    stats: dict[str, dict[str, Any]] = {}
    for label, rows in groups.items():
        pnls = [float(item.get("pnl_r", 0) or 0) for item in rows]
        gross_profit = sum(value for value in pnls if value > 0)
        gross_loss = sum(value for value in pnls if value < 0)
        stats[label] = {
            "trades": len(rows),
            "win_rate": round(sum(1 for value in pnls if value > 0) / len(rows), 4) if rows else 0.0,
            "avg_r": round(sum(pnls) / len(rows), 4) if rows else 0.0,
            "expectancy_r": round(sum(pnls) / len(rows), 4) if rows else 0.0,
            "profit_factor": None if gross_loss == 0 else round(gross_profit / abs(gross_loss), 4),
        }
    return stats


def _bucketed_score_stats(trades: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    buckets = {
        "0-64": [],
        "65-74": [],
        "75-84": [],
        "85+": [],
    }
    for trade in trades:
        score = int(trade.get("confluence_score", 0) or 0)
        if score >= 85:
            buckets["85+"].append(trade)
        elif score >= 75:
            buckets["75-84"].append(trade)
        elif score >= 65:
            buckets["65-74"].append(trade)
        else:
            buckets["0-64"].append(trade)

    return _group_stats(
        [
            {**trade, "__bucket__": bucket}
            for bucket, rows in buckets.items()
            for trade in rows
        ],
        key="__bucket__",
    )


def _max_consecutive_losses(pnls: list[float]) -> int:
    longest = 0
    current = 0
    for value in pnls:
        if value < 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest


def _max_drawdown(pnls: list[float]) -> float:
    peak = 0.0
    running = 0.0
    max_drawdown = 0.0
    for value in pnls:
        running += value
        peak = max(peak, running)
        max_drawdown = max(max_drawdown, peak - running)
    return round(max_drawdown, 4)


def _report_status(expectancy: float, profit_factor: float | None, negative_sessions: list[str]) -> str:
    if expectancy > 0 and profit_factor is not None and profit_factor > 1.3 and not negative_sessions:
        return "ready"
    if expectancy > 0 and (profit_factor is None or profit_factor > 1.0):
        return "mixed"
    return "not_ready"
