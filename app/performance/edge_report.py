from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import combinations
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.logs.closed_trade_stats import iter_closed_trade_rows


@dataclass(frozen=True)
class EdgeReportSummary:
    total_trades: int
    best_session: str
    best_session_expectancy_r: float
    better_predictor: str
    output_path: str


class EdgeReportGenerator:
    """
    Aggregate closed trades into evidence about where the edge actually lives.

    The report is intentionally schema-compatible with both live
    `closed_trades.jsonl` rows and backtest simulated closed-trade rows.
    """

    def __init__(self, *, output_root: Path | None = None, min_samples: int = 10):
        self.output_root = Path(output_root or "feedback")
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.min_samples = max(int(min_samples), 1)

    def generate(
        self,
        trades_path: Path,
        *,
        output_path: Path | None = None,
        min_samples: int | None = None,
    ) -> EdgeReportSummary:
        rows = list(iter_closed_trade_rows(Path(trades_path)))
        report = self.build_report(
            rows,
            source_file=str(trades_path),
            min_samples=min_samples,
        )
        target = Path(output_path or (self.output_root / "edge_report.json"))
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)

        best_session = (
            ((report.get("key_findings") or {}).get("best_session_by_expectancy") or {}).get("name")
            or ""
        )
        best_session_expectancy = float(
            (((report.get("key_findings") or {}).get("best_session_by_expectancy") or {}).get("expectancy_r") or 0.0)
        )
        better_predictor = str(
            ((report.get("score_predictiveness") or {}).get("better_predictor") or "insufficient_data")
        )
        return EdgeReportSummary(
            total_trades=int(report.get("total_trades", 0) or 0),
            best_session=best_session,
            best_session_expectancy_r=round(best_session_expectancy, 4),
            better_predictor=better_predictor,
            output_path=str(target),
        )

    def build_report(
        self,
        trades: list[dict[str, Any]],
        *,
        source_file: str,
        min_samples: int | None = None,
    ) -> dict[str, Any]:
        threshold = max(int(min_samples or self.min_samples), 1)
        cleaned = [row for row in trades if isinstance(row, dict)]

        session_stats = _group_stats_by_value(cleaned, "session", min_samples=threshold)
        grade_stats = _group_stats_by_value(cleaned, "setup_grade", min_samples=threshold)
        root_cause_stats = _group_stats_by_value(cleaned, "root_cause", min_samples=threshold)
        tag_stats = _group_stats_by_tags(cleaned, min_samples=threshold)
        tag_pair_stats = _group_stats_by_tag_pairs(cleaned, min_samples=threshold)
        mechanical_buckets = _group_stats_by_bucket(
            cleaned,
            score_getter=lambda row: _safe_int(row.get("mechanical_confluence_score")),
            min_samples=threshold,
        )

        mechanical_predictiveness = _score_predictiveness(cleaned, "mechanical_confluence_score")
        claude_predictiveness = _score_predictiveness(cleaned, "confluence_score")
        better_predictor = _better_predictor(mechanical_predictiveness, claude_predictiveness)

        report = {
            "generated_at_utc": _utc_now(),
            "source_file": source_file,
            "minimum_sample_size": threshold,
            "total_trades": len(cleaned),
            "overall": _stats_for_rows(cleaned),
            "session_breakdown": session_stats,
            "setup_grade_breakdown": grade_stats,
            "root_cause_breakdown": root_cause_stats,
            "pattern_tag_breakdown": tag_stats,
            "pattern_tag_pair_breakdown": tag_pair_stats,
            "mechanical_score_bucket_breakdown": mechanical_buckets,
            "score_predictiveness": {
                "mechanical": mechanical_predictiveness,
                "claude": claude_predictiveness,
                "better_predictor": better_predictor,
            },
        }
        report["key_findings"] = self._build_key_findings(
            report,
            cleaned,
            min_samples=threshold,
        )
        return report

    def _build_key_findings(
        self,
        report: dict[str, Any],
        trades: list[dict[str, Any]],
        *,
        min_samples: int,
    ) -> dict[str, Any]:
        session_stats = report.get("session_breakdown") or {}
        grade_stats = report.get("setup_grade_breakdown") or {}
        tag_pair_stats = report.get("pattern_tag_pair_breakdown") or {}
        root_cause_stats = report.get("root_cause_breakdown") or {}

        cpao_rows = [
            row
            for row in trades
            if str(row.get("root_cause", "")).upper() == "CORRECT_PROCESS_ADVERSE_OUTCOME"
        ]
        non_loss_recoveries = sum(
            1
            for row in cpao_rows
            if str(row.get("outcome", "")).upper() in {"BREAKEVEN", "PARTIAL_WIN", "WIN"}
        )
        cpao_recovery = {
            "sample_size": len(cpao_rows),
            "non_loss_rate": round(non_loss_recoveries / len(cpao_rows), 4) if cpao_rows else None,
            "avg_r": round(
                sum(_safe_float(row.get("pnl_r")) for row in cpao_rows) / len(cpao_rows),
                4,
            )
            if cpao_rows
            else None,
        }

        suggested_filters = _suggested_filters(
            session_stats,
            grade_stats,
            report.get("pattern_tag_breakdown") or {},
            tag_pair_stats,
        )
        focus_areas = _focus_areas(session_stats, grade_stats, tag_pair_stats)

        return {
            "best_session_by_expectancy": _best_group(session_stats),
            "best_setup_grade_by_expectancy": _best_group(grade_stats),
            "best_pattern_tag_pair_by_expectancy": _best_group(tag_pair_stats),
            "dominant_root_causes": _top_groups(root_cause_stats, limit=3, sort_key="trades"),
            "correct_process_adverse_outcome_recovery": cpao_recovery,
            "suggested_filters": suggested_filters,
            "focus_areas": focus_areas,
            "evidence_notes": _evidence_notes(
                best_session=_best_group(session_stats),
                best_grade=_best_group(grade_stats),
                better_predictor=str((report.get("score_predictiveness") or {}).get("better_predictor") or ""),
                min_samples=min_samples,
            ),
        }


def generate_edge_report(
    closed_trades_path: Path,
    *,
    output_path: Path | None = None,
    min_samples: int = 10,
) -> dict[str, Any]:
    generator = EdgeReportGenerator(
        output_root=(Path(output_path).parent if output_path else Path("feedback")),
        min_samples=min_samples,
    )
    rows = list(iter_closed_trade_rows(Path(closed_trades_path)))
    report = generator.build_report(
        rows,
        source_file=str(closed_trades_path),
        min_samples=min_samples,
    )
    if output_path is not None:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
    return report


def edge_report_summary_to_dict(summary: EdgeReportSummary) -> dict[str, Any]:
    return asdict(summary)


def _group_stats_by_value(
    trades: list[dict[str, Any]],
    key: str,
    *,
    min_samples: int,
) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        label = str(trade.get(key) or "UNKNOWN").strip() or "UNKNOWN"
        groups.setdefault(label, []).append(trade)
    return {
        label: _stats_for_rows(rows)
        for label, rows in groups.items()
        if len(rows) >= min_samples
    }


def _group_stats_by_tags(
    trades: list[dict[str, Any]],
    *,
    min_samples: int,
) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        tags = _normalised_tags(trade.get("pattern_tags"))
        for tag in tags:
            groups.setdefault(tag, []).append(trade)
    return {
        tag: _stats_for_rows(rows)
        for tag, rows in groups.items()
        if len(rows) >= min_samples
    }


def _group_stats_by_tag_pairs(
    trades: list[dict[str, Any]],
    *,
    min_samples: int,
) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        tags = _normalised_tags(trade.get("pattern_tags"))
        for left, right in combinations(tags, 2):
            label = f"{left} + {right}"
            groups.setdefault(label, []).append(trade)
    return {
        label: _stats_for_rows(rows)
        for label, rows in groups.items()
        if len(rows) >= min_samples
    }


def _group_stats_by_bucket(
    trades: list[dict[str, Any]],
    *,
    score_getter,
    min_samples: int,
) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {
        "<70": [],
        "70-79": [],
        "80-89": [],
        "90+": [],
    }
    for trade in trades:
        score = score_getter(trade)
        if score is None:
            continue
        groups[_score_bucket(score)].append(trade)
    return {
        label: _stats_for_rows(rows)
        for label, rows in groups.items()
        if len(rows) >= min_samples
    }


def _score_predictiveness(trades: list[dict[str, Any]], score_key: str) -> dict[str, Any]:
    scored_rows = []
    for trade in trades:
        score = _safe_int(trade.get(score_key))
        pnl_r = _safe_float(trade.get("pnl_r"))
        if score is None:
            continue
        scored_rows.append(
            {
                "score": score,
                "pnl_r": pnl_r,
                "won": 1 if pnl_r > 0 else 0,
            }
        )

    scores = [row["score"] for row in scored_rows]
    pnls = [row["pnl_r"] for row in scored_rows]
    wins = [row["won"] for row in scored_rows]
    return {
        "sample_size": len(scored_rows),
        "avg_score": round(sum(scores) / len(scores), 4) if scores else None,
        "pnl_correlation": _pearson(scores, pnls),
        "positive_outcome_correlation": _pearson(scores, wins),
        "bucket_breakdown": _group_stats_by_bucket(
            [
                {
                    "mechanical_confluence_score": row["score"],
                    "pnl_r": row["pnl_r"],
                }
                for row in scored_rows
            ],
            score_getter=lambda row: _safe_int(row.get("mechanical_confluence_score")),
            min_samples=1,
        ),
    }


def _stats_for_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [_safe_float(row.get("pnl_r")) for row in rows]
    wins = sum(1 for pnl in pnls if pnl > 0)
    losses = sum(1 for pnl in pnls if pnl < 0)
    breakeven = len(rows) - wins - losses
    gross_profit = sum(pnl for pnl in pnls if pnl > 0)
    gross_loss = sum(pnl for pnl in pnls if pnl < 0)
    mechanical_scores = [
        score
        for score in (_safe_int(row.get("mechanical_confluence_score")) for row in rows)
        if score is not None
    ]
    claude_scores = [
        score
        for score in (_safe_int(row.get("confluence_score")) for row in rows)
        if score is not None
    ]
    return {
        "trades": len(rows),
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate": round(wins / len(rows), 4) if rows else 0.0,
        "avg_r": round(sum(pnls) / len(rows), 4) if rows else 0.0,
        "expectancy_r": round(sum(pnls) / len(rows), 4) if rows else 0.0,
        "gross_profit_r": round(gross_profit, 4),
        "gross_loss_r": round(gross_loss, 4),
        "profit_factor": None if gross_loss == 0 else round(gross_profit / abs(gross_loss), 4),
        "avg_mechanical_score": round(sum(mechanical_scores) / len(mechanical_scores), 4)
        if mechanical_scores
        else None,
        "avg_claude_score": round(sum(claude_scores) / len(claude_scores), 4)
        if claude_scores
        else None,
    }


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _normalised_tags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    tags = sorted({str(item).strip() for item in value if str(item).strip()})
    return tags


def _score_bucket(score: int) -> str:
    if score >= 90:
        return "90+"
    if score >= 80:
        return "80-89"
    if score >= 70:
        return "70-79"
    return "<70"


def _pearson(xs: list[int | float], ys: list[int | float]) -> float | None:
    if len(xs) < 2 or len(ys) < 2 or len(xs) != len(ys):
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    diff_x = [x - mean_x for x in xs]
    diff_y = [y - mean_y for y in ys]
    denom_x = sum(value * value for value in diff_x) ** 0.5
    denom_y = sum(value * value for value in diff_y) ** 0.5
    if denom_x == 0 or denom_y == 0:
        return None
    numer = sum(x * y for x, y in zip(diff_x, diff_y))
    return round(numer / (denom_x * denom_y), 4)


def _better_predictor(mechanical: dict[str, Any], claude: dict[str, Any]) -> str:
    mech_corr = mechanical.get("pnl_correlation")
    claude_corr = claude.get("pnl_correlation")
    if mech_corr is None and claude_corr is None:
        return "insufficient_data"
    if claude_corr is None:
        return "mechanical"
    if mech_corr is None:
        return "claude"
    if abs(mech_corr - claude_corr) < 0.05:
        return "tie"
    return "mechanical" if mech_corr > claude_corr else "claude"


def _best_group(groups: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if not groups:
        return None
    name, stats = max(
        groups.items(),
        key=lambda item: (float(item[1].get("expectancy_r", 0) or 0), int(item[1].get("trades", 0) or 0)),
    )
    return {"name": name, **stats}


def _top_groups(
    groups: dict[str, dict[str, Any]],
    *,
    limit: int,
    sort_key: str,
) -> list[dict[str, Any]]:
    ranked = sorted(
        groups.items(),
        key=lambda item: (float(item[1].get(sort_key, 0) or 0), float(item[1].get("expectancy_r", 0) or 0)),
        reverse=True,
    )
    return [{"name": name, **stats} for name, stats in ranked[:limit]]


def _suggested_filters(
    sessions: dict[str, dict[str, Any]],
    grades: dict[str, dict[str, Any]],
    tags: dict[str, dict[str, Any]],
    tag_pairs: dict[str, dict[str, Any]],
) -> list[str]:
    items: list[str] = []
    for session, stats in sorted(sessions.items()):
        expectancy = float(stats.get("expectancy_r", 0) or 0)
        if expectancy < 0:
            items.append(
                f"Session '{session}' showed negative expectancy ({expectancy}R over {stats['trades']} trades) — consider reducing or filtering it."
            )
    for grade in ("C", "F"):
        stats = grades.get(grade)
        if stats and float(stats.get("expectancy_r", 0) or 0) < 0:
            items.append(
                f"Setup grade {grade} underperformed ({stats['expectancy_r']}R over {stats['trades']} trades) — tighten the minimum accepted quality."
            )
    for label, stats in sorted(tag_pairs.items(), key=lambda item: float(item[1].get("expectancy_r", 0) or 0)):
        expectancy = float(stats.get("expectancy_r", 0) or 0)
        if expectancy < 0:
            items.append(
                f"Tag pair '{label}' has negative expectancy ({expectancy}R over {stats['trades']} trades) — treat it as a candidate filter."
            )
            break
    if not items:
        for label, stats in sorted(tags.items(), key=lambda item: float(item[1].get("expectancy_r", 0) or 0)):
            expectancy = float(stats.get("expectancy_r", 0) or 0)
            if expectancy < 0:
                items.append(
                    f"Tag '{label}' has negative expectancy ({expectancy}R over {stats['trades']} trades) — review whether it should score lower."
                )
                break
    if not items:
        items.append("No strong removal candidates crossed the minimum sample threshold yet.")
    return items[:3]


def _focus_areas(
    sessions: dict[str, dict[str, Any]],
    grades: dict[str, dict[str, Any]],
    tag_pairs: dict[str, dict[str, Any]],
) -> list[str]:
    items: list[str] = []
    best_session = _best_group(sessions)
    if best_session:
        items.append(
            f"Best session so far is '{best_session['name']}' with {best_session['expectancy_r']}R expectancy over {best_session['trades']} trades."
        )
    best_grade = _best_group(grades)
    if best_grade:
        items.append(
            f"Best setup grade is {best_grade['name']} with {best_grade['expectancy_r']}R expectancy over {best_grade['trades']} trades."
        )
    best_pair = _best_group(tag_pairs)
    if best_pair:
        items.append(
            f"Strongest tag pair is '{best_pair['name']}' with {best_pair['expectancy_r']}R expectancy over {best_pair['trades']} trades."
        )
    if not items:
        items.append("Need more qualifying samples before declaring any strong focus area.")
    return items[:3]


def _evidence_notes(
    *,
    best_session: dict[str, Any] | None,
    best_grade: dict[str, Any] | None,
    better_predictor: str,
    min_samples: int,
) -> list[str]:
    notes = [
        f"Only groups with at least {min_samples} trades are promoted into actionable report sections."
    ]
    if best_session:
        notes.append(
            f"Highest expectancy session is {best_session['name']} ({best_session['expectancy_r']}R)."
        )
    if best_grade:
        notes.append(
            f"Highest expectancy setup grade is {best_grade['name']} ({best_grade['expectancy_r']}R)."
        )
    if better_predictor and better_predictor != "insufficient_data":
        notes.append(f"Current score predictor winner: {better_predictor}.")
    return notes


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
