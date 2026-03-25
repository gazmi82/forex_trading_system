from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.brokers.oanda import MarketDataBuilder
from app.core.runtime_logging import record_runtime_event
from app.execution.trade_executor import TradeExecutor


class _FailingBuilderClient:
    def get_candles(self, *args, **kwargs):
        raise RuntimeError("candles unavailable")

    def get_current_price(self, *args, **kwargs):
        raise RuntimeError("price unavailable")

    def get_account_summary(self):
        return {"balance": 100_000.0, "equity": 100_000.0, "margin_used": 0.0}

    def get_open_trades(self):
        return []


class _OrderFailClient:
    base_url = "https://example.test"
    account_id = "acct"
    headers = {}

    def get_account_summary(self):
        return {
            "balance": 100_000.0,
            "equity": 100_000.0,
            "open_trade_count": 0,
        }

    def get_open_trades(self):
        return []


class _ExplodingExecutor(TradeExecutor):
    def _place_order(self, signal: dict, units: int, execution_direction: str) -> dict:
        raise RuntimeError("broker rejected order")


class RuntimeLoggingTests(unittest.TestCase):
    def test_record_runtime_event_writes_jsonl(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = record_runtime_event(
                component="tests.runtime",
                action="direct_write",
                message="sample runtime failure",
                context={"foo": "bar"},
                log_dir=Path(tmpdir),
            )

            rows = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["component"], "tests.runtime")
        self.assertEqual(rows[0]["action"], "direct_write")
        self.assertEqual(rows[0]["context"]["foo"], "bar")

    def test_market_data_builder_failure_writes_runtime_event(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            builder = MarketDataBuilder(_FailingBuilderClient(), log_dir=Path(tmpdir))
            with self.assertRaises(RuntimeError):
                builder.build_market_data("EUR_USD")

            log_file = Path(tmpdir) / "runtime_events.jsonl"
            rows = [
                json.loads(line)
                for line in log_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertTrue(any(row["action"] == "build_market_data" for row in rows))

    def test_trade_executor_order_failure_writes_runtime_event(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            executor = _ExplodingExecutor(
                _OrderFailClient(),
                {
                    "demo_mode": True,
                    "min_confidence": 65,
                    "max_risk_per_trade": 0.01,
                    "max_weekly_loss": 0.05,
                },
                Path(tmpdir),
            )
            signal = {
                "session": "NY Kill Zone",
                "execution_allowed": True,
                "execution_direction": "BUY",
                "mechanical_confluence_score": 80,
                "confluence_score": 82,
                "signal": {
                    "direction": "BUY",
                    "confidence": 78,
                    "risk_reward": 2.8,
                    "entry_zone": [1.0800, 1.0810],
                    "stop_loss": 1.0780,
                    "take_profit_1": 1.0840,
                    "take_profit_2": 1.0880,
                },
            }

            result = executor.execute_signal(signal)
            log_file = Path(tmpdir) / "runtime_events.jsonl"
            rows = [
                json.loads(line)
                for line in log_file.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertFalse(result["executed"])
        self.assertIn("Order placement failed", result["reason"])
        self.assertTrue(any(row["action"] == "execute_signal" for row in rows))


if __name__ == "__main__":
    unittest.main()
