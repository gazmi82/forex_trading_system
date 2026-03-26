from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from app.performance import EdgeReportGenerator, WeeklySummaryGenerator


def _write_trades(path: Path, rows: list[dict]) -> Path:
    target = path / "closed_trades.jsonl"
    with open(target, "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    return target


class PerformanceReportTests(unittest.TestCase):
    @staticmethod
    def _sample_trades() -> list[dict]:
        return [
            {
                "date": "2026-03-23",
                "session": "NY Kill Zone",
                "setup_grade": "A",
                "root_cause": "CORRECT_PROCESS_CORRECT_OUTCOME",
                "pattern_tags": ["ny_kill_zone", "ob_entry", "post_sweep", "grade_a"],
                "confluence_score": 70,
                "pnl_r": 1.5,
                "outcome": "WIN",
            },
            {
                "date": "2026-03-24",
                "session": "NY Kill Zone",
                "setup_grade": "A",
                "root_cause": "CORRECT_PROCESS_CORRECT_OUTCOME",
                "pattern_tags": ["ny_kill_zone", "ob_entry", "post_sweep", "grade_a"],
                "confluence_score": 95,
                "pnl_r": 1.0,
                "outcome": "WIN",
            },
            {
                "date": "2026-03-25",
                "session": "NY Kill Zone",
                "setup_grade": "B",
                "root_cause": "CORRECT_PROCESS_ADVERSE_OUTCOME",
                "pattern_tags": ["ny_kill_zone", "ob_entry", "discount_zone", "grade_b"],
                "confluence_score": 89,
                "pnl_r": 0.0,
                "outcome": "BREAKEVEN",
            },
            {
                "date": "2026-03-26",
                "session": "London Close",
                "setup_grade": "C",
                "root_cause": "MARGINAL_SETUP_POOR_OUTCOME",
                "pattern_tags": ["london_close", "fvg_confluence", "news_day", "grade_c"],
                "confluence_score": 92,
                "pnl_r": -1.0,
                "outcome": "LOSS",
            },
            {
                "date": "2026-03-27",
                "session": "London Close",
                "setup_grade": "C",
                "root_cause": "MARGINAL_SETUP_POOR_OUTCOME",
                "pattern_tags": ["london_close", "fvg_confluence", "news_day", "grade_c"],
                "confluence_score": 94,
                "pnl_r": -0.5,
                "outcome": "LOSS",
            },
            {
                "date": "2026-03-27",
                "session": "London Close",
                "setup_grade": "B",
                "root_cause": "CORRECT_PROCESS_ADVERSE_OUTCOME",
                "pattern_tags": ["london_close", "ob_entry", "grade_b"],
                "confluence_score": 90,
                "pnl_r": -0.25,
                "outcome": "LOSS",
            },
            {
                "date": "2026-03-18",
                "session": "London Kill Zone",
                "setup_grade": "B",
                "root_cause": "CORRECT_PROCESS_CORRECT_OUTCOME",
                "pattern_tags": ["london_kill_zone", "ob_entry", "grade_b"],
                "confluence_score": 82,
                "pnl_r": 0.75,
                "outcome": "WIN",
            },
        ]

    def test_edge_report_aggregates_sessions_tags_and_confluence_buckets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            trades_path = _write_trades(base, self._sample_trades())
            generator = EdgeReportGenerator(output_root=base / "feedback", min_samples=2)

            summary = generator.generate(trades_path)
            report = json.loads(Path(summary.output_path).read_text(encoding="utf-8"))

        self.assertEqual(report["total_trades"], 7)
        self.assertIn("NY Kill Zone", report["session_breakdown"])
        self.assertIn("London Close", report["session_breakdown"])
        self.assertIn("C", report["setup_grade_breakdown"])
        self.assertIn("CORRECT_PROCESS_ADVERSE_OUTCOME", report["root_cause_breakdown"])
        self.assertIn("ob_entry", report["pattern_tag_breakdown"])
        self.assertIn("ny_kill_zone + ob_entry", report["pattern_tag_pair_breakdown"])
        self.assertIn("90+", report["confluence_score_bucket_breakdown"])
        self.assertEqual(
            report["key_findings"]["best_session_by_expectancy"]["name"],
            "NY Kill Zone",
        )
        self.assertIn("pnl_correlation", report["score_predictiveness"])
        self.assertTrue(
            any("London Close" in item for item in report["key_findings"]["suggested_filters"])
        )

    def test_weekly_summary_writes_markdown_with_action_items(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            trades_path = _write_trades(base, self._sample_trades())
            generator = WeeklySummaryGenerator(output_root=base / "feedback", min_samples=2)

            summary = generator.generate(
                trades_path,
                reference_date=date(2026, 3, 27),
            )
            markdown = Path(summary.output_path).read_text(encoding="utf-8")

        self.assertEqual(summary.total_trades, 6)
        self.assertIn("Trades taken: 6", markdown)
        self.assertIn("Positive outcomes this week: 2/6", markdown)
        self.assertIn("Top pattern this week:", markdown)
        self.assertIn("## Action Items", markdown)
        self.assertIn("Grade C setups 0W/2L this week", markdown)
        self.assertIn("London Close expectancy was negative", markdown)


if __name__ == "__main__":
    unittest.main()
