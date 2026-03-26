from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from app.backtesting import BacktestReportGenerator, HistoricalDataLoader, OutcomeSimulator


def _frame_from_rows(start: str, rows: list[dict[str, float]]) -> pd.DataFrame:
    index = pd.date_range(start=start, periods=len(rows), freq="1min", tz="UTC")
    return pd.DataFrame(rows, index=index)


def _write_m1_dataset(base: Path, frame: pd.DataFrame):
    dataset_dir = base / "raw" / "EUR_USD"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    start_slug = frame.index[0].strftime("%Y%m%dT%H%M%SZ")
    end_slug = (frame.index[-1] + pd.Timedelta(minutes=1)).strftime("%Y%m%dT%H%M%SZ")
    path = dataset_dir / f"EUR_USD_M1_M_{start_slug}_{end_slug}.csv"
    frame.to_csv(path, index_label="time")


def _write_signal_file(base: Path, payloads: list[dict]) -> Path:
    path = base / "signals.jsonl"
    with open(path, "w", encoding="utf-8") as handle:
        for row in payloads:
            handle.write(json.dumps(row) + "\n")
    return path


class OutcomeSimulatorTests(unittest.TestCase):
    def test_simulator_handles_tp1_then_tp2(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            frame = _frame_from_rows(
                "2026-03-25T12:00:00Z",
                [
                    {"open": 1.1000, "high": 1.1004, "low": 1.0998, "close": 1.1002, "volume": 100},
                    {"open": 1.1002, "high": 1.1013, "low": 1.1001, "close": 1.1010, "volume": 110},
                    {"open": 1.1010, "high": 1.1025, "low": 1.1009, "close": 1.1020, "volume": 120},
                ],
            )
            _write_m1_dataset(base, frame)
            signal = {
                "pair": "EUR/USD",
                "timestamp": "2026-03-25T12:00:00Z",
                "session": "NY Kill Zone",
                "confluence_score": 80,
                "signal_strength": "MODERATE",
                "execution_allowed": True,
                "validator_overrides": [],
                "signal": {
                    "direction": "BUY",
                    "confidence": 80,
                    "entry_zone": [1.1000, 1.1000],
                    "stop_loss": 1.0990,
                    "take_profit_1": 1.1010,
                    "take_profit_2": 1.1020,
                    "risk_reward": 2.0,
                },
            }
            signals_path = _write_signal_file(base, [signal])
            loader = HistoricalDataLoader(None, base)
            simulator = OutcomeSimulator(loader, output_root=base / "results")

            summary = simulator.simulate(signals_path, local_only=True)
            rows = [
                json.loads(line)
                for line in Path(summary.output_path).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertEqual(summary.filled_trades, 1)
        self.assertEqual(summary.no_fill_signals, 0)
        self.assertEqual(rows[0]["close_reason"], "TAKE_PROFIT_2")
        self.assertTrue(rows[0]["tp1_hit"])
        self.assertAlmostEqual(rows[0]["pnl_r"], 1.5, places=4)

    def test_simulator_applies_time_stop(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            rows = []
            for _ in range(121):
                rows.append(
                    {
                        "open": 1.1000,
                        "high": 1.1004,
                        "low": 1.0988,
                        "close": 1.0989,
                        "volume": 100,
                    }
                )
            frame = _frame_from_rows("2026-03-25T12:00:00Z", rows)
            _write_m1_dataset(base, frame)
            signal = {
                "pair": "EUR/USD",
                "timestamp": "2026-03-25T12:00:00Z",
                "session": "London Close",
                "confluence_score": 72,
                "signal_strength": "MODERATE",
                "execution_allowed": True,
                "validator_overrides": [],
                "signal": {
                    "direction": "BUY",
                    "confidence": 72,
                    "entry_zone": [1.1000, 1.1000],
                    "stop_loss": 1.0980,
                    "take_profit_1": 1.1020,
                    "take_profit_2": 1.1040,
                    "risk_reward": 2.0,
                },
            }
            signals_path = _write_signal_file(base, [signal])
            loader = HistoricalDataLoader(None, base)
            simulator = OutcomeSimulator(
                loader,
                output_root=base / "results",
                trading_config={"time_stop_hours": {"London Close": 1, "default": 8}},
            )

            summary = simulator.simulate(signals_path, local_only=True)
            rows = [
                json.loads(line)
                for line in Path(summary.output_path).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertEqual(rows[0]["close_reason"], "TIME_STOP")
        self.assertLess(rows[0]["pnl_r"], 0)

    def test_simulator_trails_runner_in_same_candle_as_tp1(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            frame = _frame_from_rows(
                "2026-03-25T12:00:00Z",
                [
                    {"open": 1.1000, "high": 1.1002, "low": 1.0998, "close": 1.1000, "volume": 100},
                    {"open": 1.1000, "high": 1.1030, "low": 1.1002, "close": 1.1025, "volume": 120},
                    {"open": 1.1025, "high": 1.1026, "low": 1.1018, "close": 1.1020, "volume": 110},
                ],
            )
            _write_m1_dataset(base, frame)
            signal = {
                "pair": "EUR/USD",
                "timestamp": "2026-03-25T12:00:00Z",
                "session": "NY Kill Zone",
                "confluence_score": 78,
                "signal_strength": "MODERATE",
                "execution_allowed": True,
                "validator_overrides": [],
                "signal": {
                    "direction": "BUY",
                    "confidence": 78,
                    "entry_zone": [1.1000, 1.1000],
                    "stop_loss": 1.0990,
                    "take_profit_1": 1.1010,
                    "take_profit_2": 1.1040,
                    "risk_reward": 2.0,
                },
            }
            signals_path = _write_signal_file(base, [signal])
            loader = HistoricalDataLoader(None, base)
            simulator = OutcomeSimulator(loader, output_root=base / "results")

            summary = simulator.simulate(signals_path, local_only=True)
            rows = [
                json.loads(line)
                for line in Path(summary.output_path).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertEqual(summary.filled_trades, 1)
        self.assertTrue(rows[0]["tp1_hit"])
        self.assertEqual(rows[0]["close_reason"], "STOP_LOSS")
        self.assertAlmostEqual(rows[0]["pnl_r"], 1.5, places=4)

    def test_simulator_uses_atr_based_trailing_when_signal_has_entry_atr(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            frame = _frame_from_rows(
                "2026-03-25T12:00:00Z",
                [
                    {"open": 1.1000, "high": 1.1002, "low": 1.0998, "close": 1.1000, "volume": 100},
                    {"open": 1.1000, "high": 1.1026, "low": 1.1002, "close": 1.1024, "volume": 120},
                    {"open": 1.1024, "high": 1.1025, "low": 1.1015, "close": 1.1016, "volume": 110},
                ],
            )
            _write_m1_dataset(base, frame)
            signal = {
                "pair": "EUR/USD",
                "timestamp": "2026-03-25T12:00:00Z",
                "session": "NY Kill Zone",
                "confluence_score": 78,
                "signal_strength": "MODERATE",
                "execution_allowed": True,
                "validator_overrides": [],
                "technical_analysis": {"atr_1h": 0.0006},
                "signal": {
                    "direction": "BUY",
                    "confidence": 78,
                    "entry_zone": [1.1000, 1.1000],
                    "stop_loss": 1.0990,
                    "take_profit_1": 1.1010,
                    "take_profit_2": 1.1040,
                    "risk_reward": 2.0,
                },
            }
            signals_path = _write_signal_file(base, [signal])
            loader = HistoricalDataLoader(None, base)
            simulator = OutcomeSimulator(loader, output_root=base / "results")

            summary = simulator.simulate(signals_path, local_only=True)
            rows = [
                json.loads(line)
                for line in Path(summary.output_path).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        self.assertEqual(summary.filled_trades, 1)
        self.assertTrue(rows[0]["tp1_hit"])
        self.assertEqual(rows[0]["close_reason"], "STOP_LOSS")
        self.assertAlmostEqual(rows[0]["pnl_r"], 1.5, places=4)

    def test_backtest_report_summarizes_results(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            trades_path = base / "closed_trades.jsonl"
            trades = [
                {"session": "NY Kill Zone", "confluence_score": 80, "pnl_r": 1.5},
                {"session": "NY Kill Zone", "confluence_score": 72, "pnl_r": -1.0},
                {"session": "London Close", "confluence_score": 88, "pnl_r": 0.5},
            ]
            with open(trades_path, "w", encoding="utf-8") as handle:
                for trade in trades:
                    handle.write(json.dumps(trade) + "\n")

            reporter = BacktestReportGenerator(output_root=base / "results")
            summary = reporter.generate(trades_path)
            report = json.loads(Path(summary.output_path).read_text(encoding="utf-8"))

        self.assertEqual(report["total_trades"], 3)
        self.assertAlmostEqual(report["expectancy_r"], 0.3333, places=4)
        self.assertAlmostEqual(report["profit_factor"], 2.0, places=4)
        self.assertIn("NY Kill Zone", report["session_breakdown"])
        self.assertIn("85+", report["score_bucket_breakdown"])


if __name__ == "__main__":
    unittest.main()
