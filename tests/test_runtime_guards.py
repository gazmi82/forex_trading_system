from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from app.analysis.agent import ForexAnalystAgent
from app.cli.main import print_signal_runtime_issue
from app.execution.trade_executor import TradeExecutor
from app.fundamentals.fetcher import _classify_news_risk


class RuntimeGuardTests(unittest.TestCase):
    def test_print_signal_runtime_issue_handles_null_reason(self):
        with redirect_stdout(io.StringIO()) as output:
            print_signal_runtime_issue({"error": "upstream failed", "do_not_trade_reason": None})

        self.assertIn("Claude API failure", output.getvalue())

    def test_print_signal_runtime_issue_handles_missing_reason(self):
        with redirect_stdout(io.StringIO()) as output:
            print_signal_runtime_issue({"signal": {"direction": "NEUTRAL"}})

        self.assertEqual(output.getvalue(), "")

    def test_agent_runtime_issue_handles_null_reason(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agent = ForexAnalystAgent(
                rag_pipeline=None,
                anthropic_client=None,
                config={},
                log_dir=Path(tmpdir),
            )

        self.assertEqual(agent._get_runtime_issue({"do_not_trade_reason": None}), "")
        self.assertEqual(
            agent._get_runtime_issue({"do_not_trade_reason": "JSON parse error - bad payload"}),
            "JSON parse error - bad payload",
        )

    def test_classify_news_risk_handles_missing_event_name(self):
        self.assertEqual(_classify_news_risk(None, "25 minutes"), "HIGH")
        self.assertEqual(_classify_news_risk(None, None), "LOW")

    def test_validate_signal_preserves_claude_score_and_blocks_execution(self):
        agent = ForexAnalystAgent.__new__(ForexAnalystAgent)
        agent.config = {
            "min_confidence": 65,
            "max_risk_per_trade": 0.01,
            "max_daily_loss": 0.02,
            "max_portfolio_risk": 0.03,
        }
        agent._has_session_loss_streak = lambda session, limit=2: False

        signal = {
            "confluence_score": 80,
            "signal": {
                "direction": "BUY",
                "confidence": 80,
                "risk_reward": 2.5,
            },
        }
        market_data = {
            "demo_mode": True,
            "price": 1.0800,
            "ohlcv": {
                "weekly_trend": "BEARISH",
                "daily_trend": "BEARISH",
                "h4_trend": "BEARISH",
            },
            "indicators": {
                "bullish_ob": "None identified in last 50 candles",
                "bullish_fvg": "None identified",
                "recent_liquidity_sweep": "No recent sweep identified",
                "premium_discount_zone": "PREMIUM (72% of range)",
                "ote_zone": [],
                "rsi_4h": 55.0,
                "rsi_1h": 53.0,
                "adx_4h": 18.0,
                "ema20_4h": 1.0820,
                "ema50_4h": 1.0800,
            },
            "fundamental": {
                "active_session": "NY Kill Zone",
                "next_news_event": "",
                "time_to_event": "2 hours",
                "pair_rate": 2.0,
                "usd_rate": 4.5,
                "dxy_direction": "RISING",
                "cot_bias": "BEARISH",
                "news_risk": "HIGH",
            },
            "portfolio": {
                "daily_pnl_pct": 0.0,
                "open_risk_pct": 0.0,
            },
        }

        result = agent._validate_signal(signal, market_data)

        self.assertEqual(result["confluence_score"], 80)
        self.assertLess(result["mechanical_confluence_score"], 65)
        self.assertEqual(result["signal"]["direction"], "BUY")
        self.assertFalse(result["execution_allowed"])
        self.assertEqual(result["execution_direction"], "NEUTRAL")
        self.assertTrue(
            any("Mechanical confluence score too low" in item for item in result["validator_overrides"])
        )

    def test_executor_blocks_preserved_claude_signal_when_execution_not_allowed(self):
        class _DummyClient:
            def get_account_summary(self):
                raise AssertionError("execution gate should block before account fetch")

        with tempfile.TemporaryDirectory() as tmpdir:
            executor = TradeExecutor(_DummyClient(), {"demo_mode": True, "min_confidence": 65}, Path(tmpdir))
            signal = {
                "session": "NY Kill Zone",
                "execution_allowed": False,
                "validator_overrides": ["BLOCKED: Mechanical confluence score too low (10/100, minimum 65; Claude scored 80)"],
                "mechanical_confluence_score": 10,
                "confluence_score": 80,
                "signal": {
                    "direction": "BUY",
                    "confidence": 80,
                    "risk_reward": 2.5,
                },
            }

            result = executor.execute_signal(signal)

        self.assertFalse(result["executed"])
        self.assertIn("Mechanical confluence score too low", result["reason"])


if __name__ == "__main__":
    unittest.main()
