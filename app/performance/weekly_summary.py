from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from app.core.text_utils import slugify_text
from app.logs.closed_trade_stats import _parse_trade_date, iter_closed_trade_rows
from app.performance.edge_report import EdgeReportGenerator


@dataclass(frozen=True)
class WeeklySummaryResult:
    week_start: str
    week_end: str
    total_trades: int
    output_path: str


class WeeklySummaryGenerator:
    def __init__(self, *, output_root: Path | None = None, min_samples: int = 2):
        self.output_root = Path(output_root or "feedback")
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.min_samples = max(int(min_samples), 1)

    def generate(
        self,
        trades_path: Path,
        *,
        reference_date: date | datetime | None = None,
        output_path: Path | None = None,
        min_samples: int | None = None,
    ) -> WeeklySummaryResult:
        reference = _coerce_reference_date(reference_date)
        week_start, week_end = _iso_week_bounds(reference)
        week_rows = [
            row
            for row in iter_closed_trade_rows(Path(trades_path))
            if _in_week(row, week_start, week_end)
        ]

        markdown = self.build_markdown(
            week_rows,
            source_file=str(trades_path),
            week_start=week_start,
            week_end=week_end,
            min_samples=min_samples,
        )
        target = Path(output_path or self._default_output_path(reference))
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            handle.write(markdown)

        return WeeklySummaryResult(
            week_start=week_start.date().isoformat(),
            week_end=(week_end - timedelta(seconds=1)).date().isoformat(),
            total_trades=len(week_rows),
            output_path=str(target),
        )

    def build_markdown(
        self,
        trades: list[dict[str, Any]],
        *,
        source_file: str,
        week_start: datetime,
        week_end: datetime,
        min_samples: int | None = None,
    ) -> str:
        threshold = max(int(min_samples or self.min_samples), 1)
        edge_report = EdgeReportGenerator(min_samples=threshold).build_report(
            trades,
            source_file=source_file,
            min_samples=threshold,
        )
        overall = edge_report.get("overall") or {}
        wins = int(overall.get("wins", 0) or 0)
        losses = int(overall.get("losses", 0) or 0)
        total = int(edge_report.get("total_trades", 0) or 0)
        pnl_r = round(float(overall.get("gross_profit_r", 0) or 0) + float(overall.get("gross_loss_r", 0) or 0), 4)
        accuracy_hits = sum(1 for trade in trades if _safe_float(trade.get("pnl_r")) > 0)
        top_pattern = _top_pattern_text(trades, edge_report)
        action_items = _weekly_action_items(trades, edge_report)
        best_session = ((edge_report.get("key_findings") or {}).get("best_session_by_expectancy") or {}).get("name")
        best_session_expectancy = ((edge_report.get("key_findings") or {}).get("best_session_by_expectancy") or {}).get("expectancy_r")

        header = [
            "# Weekly Performance Summary",
            "",
            f"WEEK OF: {week_start.date().isoformat()} → {(week_end - timedelta(seconds=1)).date().isoformat()}",
            "",
            f"Trades taken: {total}  |  W/L: {wins}/{losses}  |  P&L: {_signed_r(pnl_r)}R",
            f"Mechanical score accuracy: {accuracy_hits}/{total} positive outcomes",
            f"Top pattern this week: {top_pattern}",
        ]
        if best_session:
            header.append(
                f"Best session this week: {best_session} ({best_session_expectancy}R expectancy)"
            )

        lines = header + ["", "## Action Items", ""]
        if action_items:
            for item in action_items:
                lines.append(f"- {item}")
        else:
            lines.append("- No urgent changes this week. Keep collecting samples.")

        lines.extend(
            [
                "",
                "## Evidence",
                "",
                f"- Minimum sample threshold for grouped insights: {threshold}",
                f"- Source file: `{source_file}`",
                f"- Sessions with negative expectancy: {', '.join(_negative_expectancy_sessions(edge_report)) or 'none'}",
            ]
        )
        return "\n".join(lines) + "\n"

    def _default_output_path(self, reference: date) -> Path:
        slug = reference.strftime("%Y%m%d")
        return self.output_root / f"weekly_{slug}.md"


def weekly_summary_to_dict(summary: WeeklySummaryResult) -> dict[str, Any]:
    return asdict(summary)


def _coerce_reference_date(value: date | datetime | None) -> date:
    if value is None:
        return datetime.now(timezone.utc).date()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.date()
        return value.astimezone(timezone.utc).date()
    return value


def _iso_week_bounds(value: date) -> tuple[datetime, datetime]:
    start = datetime.combine(value - timedelta(days=value.weekday()), time.min, tzinfo=timezone.utc)
    end = start + timedelta(days=7)
    return start, end


def _in_week(trade: dict[str, Any], week_start: datetime, week_end: datetime) -> bool:
    trade_date = _parse_trade_date(trade.get("date"))
    return trade_date is not None and week_start <= trade_date < week_end


def _top_pattern_text(trades: list[dict[str, Any]], edge_report: dict[str, Any]) -> str:
    pair_stats = edge_report.get("pattern_tag_pair_breakdown") or {}
    if pair_stats:
        label, stats = max(
            pair_stats.items(),
            key=lambda item: (float(item[1].get("expectancy_r", 0) or 0), int(item[1].get("trades", 0) or 0)),
        )
        return f"{label} — {stats['expectancy_r']}R expectancy over {stats['trades']} trades"

    tag_stats = edge_report.get("pattern_tag_breakdown") or {}
    if tag_stats:
        label, stats = max(
            tag_stats.items(),
            key=lambda item: (float(item[1].get("expectancy_r", 0) or 0), int(item[1].get("trades", 0) or 0)),
        )
        return f"{label} — {stats['expectancy_r']}R expectancy over {stats['trades']} trades"

    if not trades:
        return "No completed trades this week"

    best_trade = max(trades, key=lambda item: _safe_float(item.get("pnl_r")))
    tags = [str(tag).strip() for tag in (best_trade.get("pattern_tags") or []) if str(tag).strip()]
    tag_text = " ".join(tags[:3]) if tags else slugify_text(best_trade.get("session", "unknown"))
    return f"{tag_text} — {_signed_r(_safe_float(best_trade.get('pnl_r')))}R on the best single trade"


def _weekly_action_items(trades: list[dict[str, Any]], edge_report: dict[str, Any]) -> list[str]:
    items: list[str] = []
    sessions = edge_report.get("session_breakdown") or {}
    grades = edge_report.get("setup_grade_breakdown") or {}
    tag_pairs = edge_report.get("pattern_tag_pair_breakdown") or {}

    grade_c_rows = [trade for trade in trades if str(trade.get("setup_grade", "")).upper() == "C"]
    if grade_c_rows:
        wins = sum(1 for trade in grade_c_rows if _safe_float(trade.get("pnl_r")) > 0)
        losses = sum(1 for trade in grade_c_rows if _safe_float(trade.get("pnl_r")) < 0)
        if losses >= wins:
            items.append(
                f"Grade C setups {wins}W/{losses}L this week — consider raising the minimum accepted setup quality to B."
            )

    for session, stats in sorted(sessions.items(), key=lambda item: float(item[1].get("expectancy_r", 0) or 0)):
        if float(stats.get("expectancy_r", 0) or 0) < 0:
            items.append(
                f"{session} expectancy was negative ({stats['expectancy_r']}R over {stats['trades']} trades) — review or filter that session."
            )
            break

    for label, stats in sorted(tag_pairs.items(), key=lambda item: float(item[1].get("expectancy_r", 0) or 0)):
        if float(stats.get("expectancy_r", 0) or 0) < 0:
            items.append(
                f"Pattern pair '{label}' lost money this week ({stats['expectancy_r']}R over {stats['trades']} trades) — reduce trust in that combination."
            )
            break

    if not items:
        best_grade = grades.get("A") or grades.get("B")
        if best_grade:
            items.append(
                f"Best-quality setups held up this week ({best_grade['expectancy_r']}R expectancy) — keep prioritizing that bucket."
            )
        else:
            items.append("No strong filter change surfaced this week; keep collecting samples.")
    return items[:3]


def _negative_expectancy_sessions(edge_report: dict[str, Any]) -> list[str]:
    sessions = edge_report.get("session_breakdown") or {}
    return [
        session
        for session, stats in sessions.items()
        if float(stats.get("expectancy_r", 0) or 0) < 0
    ]


def _signed_r(value: float) -> str:
    rounded = round(float(value or 0.0), 2)
    return f"+{rounded}" if rounded > 0 else str(rounded)


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
