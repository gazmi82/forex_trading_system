from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.analysis.decision_logging import (
    attach_live_validation_log_reference,
    log_analysis,
    update_live_validation_outcome,
)


class LiveValidationCaptureTests(unittest.TestCase):
    def _read_rows(self, path: Path) -> list[dict]:
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_log_analysis_writes_live_validation_capture_row(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            log_analysis(
                log_dir,
                pair="EUR/USD",
                market_data={
                    "pair": "EUR/USD",
                    "price": 1.1542,
                    "spread": 1.7,
                    "ohlcv": {"weekly_trend": "BULLISH"},
                    "indicators": {"adx_4h": 24.5},
                    "fundamental": {"active_session": "NY Kill Zone"},
                    "portfolio": {"daily_pnl_pct": 0.0},
                },
                signal={
                    "timestamp": "2026-03-26T13:55:20Z",
                    "session": "NY Kill Zone",
                    "confluence_score": 72,
                    "execution_allowed": True,
                    "execution_direction": "BUY",
                    "signal": {"direction": "BUY", "confidence": 75},
                },
                retrieved_chunks={"pair_knowledge": [{"source": "book"}]},
                raw_response='{"signal":{"direction":"BUY","confidence":75}}',
                user_message="USER MESSAGE",
                model="claude-sonnet",
                system_prompt="SYSTEM PROMPT",
            )

            rows = self._read_rows(log_dir / "live_validation_capture.jsonl")

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["analysis_source"], "CLAUDE_LIVE")
        self.assertEqual(row["pair"], "EUR/USD")
        self.assertEqual(row["signal_summary"]["direction"], "BUY")
        self.assertEqual(row["market_snapshot"]["price"], 1.1542)
        self.assertEqual(row["rag_summary"]["chunks_used"], 1)
        self.assertEqual(row["user_message"], "USER MESSAGE")
        self.assertEqual(len(row["system_prompt_sha256"]), 64)
        self.assertEqual(len(row["user_message_sha256"]), 64)

    def test_attach_live_validation_log_reference_updates_matching_row(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            log_analysis(
                log_dir,
                pair="EUR/USD",
                market_data={"fundamental": {"active_session": "NY Kill Zone"}},
                signal={
                    "timestamp": "2026-03-26T13:55:20Z",
                    "pair": "EUR/USD",
                    "session": "NY Kill Zone",
                    "confluence_score": 60,
                    "signal": {"direction": "NEUTRAL", "confidence": 55},
                },
                retrieved_chunks={},
                raw_response="{}",
                user_message="msg",
                model="claude-sonnet",
                system_prompt="prompt",
            )

            updated = attach_live_validation_log_reference(
                log_dir,
                {
                    "timestamp": "2026-03-26T13:55:20Z",
                    "pair": "EUR/USD",
                    "log_filename": "signal_20260326.json",
                    "log_entry_id": "entry_1",
                },
            )
            rows = self._read_rows(log_dir / "live_validation_capture.jsonl")

        self.assertTrue(updated)
        self.assertEqual(rows[0]["signal_log_filename"], "signal_20260326.json")
        self.assertEqual(rows[0]["signal_log_entry_id"], "entry_1")

    def test_update_live_validation_outcome_uses_signal_log_reference(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            log_analysis(
                log_dir,
                pair="EUR/USD",
                market_data={"fundamental": {"active_session": "London Close"}},
                signal={
                    "timestamp": "2026-03-26T14:05:20Z",
                    "pair": "EUR/USD",
                    "session": "London Close",
                    "confluence_score": 68,
                    "signal": {"direction": "SELL", "confidence": 70},
                    "log_filename": "signal_20260326.json",
                    "log_entry_id": "entry_2",
                },
                retrieved_chunks={},
                raw_response="{}",
                user_message="msg",
                model="claude-sonnet",
                system_prompt="prompt",
            )

            updated = update_live_validation_outcome(
                log_dir,
                {
                    "pair": "EUR/USD",
                    "signal_timestamp": "2026-03-26T14:05:20Z",
                    "signal_log_filename": "signal_20260326.json",
                    "signal_log_entry_id": "entry_2",
                    "outcome": "WIN",
                    "pnl_r": 2.1,
                    "pnl_usd": 2100.0,
                },
            )
            rows = self._read_rows(log_dir / "live_validation_capture.jsonl")

        self.assertTrue(updated)
        self.assertEqual(rows[0]["outcome"], "WIN")
        self.assertEqual(rows[0]["pnl_r"], 2.1)
        self.assertEqual(rows[0]["pnl_usd"], 2100.0)


if __name__ == "__main__":
    unittest.main()
