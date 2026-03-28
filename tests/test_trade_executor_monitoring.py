from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from app.execution.trade_executor import TradeExecutor


class _MonitorClient:
    def __init__(self, *, current_price: dict, open_trades: list[dict], candles: pd.DataFrame):
        self._current_price = current_price
        self._open_trades = open_trades
        self._candles = candles

    def get_current_price(self, instrument: str):
        return dict(self._current_price)

    def get_open_trades(self):
        return list(self._open_trades)

    def get_candles(self, instrument: str, granularity: str, count: int = 200):
        return self._candles.copy()

    def get_candles_range(self, instrument: str, granularity: str, *, start, end):
        return self._candles.copy()

    def get_account_summary(self):
        return {"equity": 100000.0}


class _RecordingTradeExecutor(TradeExecutor):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.partial_calls: list[tuple[str, int]] = []
        self.stop_moves: list[tuple[str, float]] = []
        self.close_calls: list[str] = []

    def _close_partial(self, trade_id: str, units: int):
        self.partial_calls.append((trade_id, units))

    def _move_sl_to_entry(self, trade_id: str, entry_price: float):
        self.stop_moves.append((trade_id, round(entry_price, 5)))

    def _close_trade(self, trade_id: str):
        self.close_calls.append(trade_id)


class TradeExecutorMonitoringTests(unittest.TestCase):
    @staticmethod
    def _candles(*, highs: list[float], lows: list[float]) -> pd.DataFrame:
        return pd.DataFrame({"high": highs, "low": lows})

    @staticmethod
    def _tracked_trade(**overrides) -> dict:
        trade = {
            "trade_id": "200",
            "order_id": "100",
            "instrument": "EUR_USD",
            "pair": "EUR/USD",
            "direction": "SELL",
            "units": 100000,
            "entry_price": 1.15374,
            "stop_loss": 1.15650,
            "tp1": 1.14800,
            "tp2": 1.14413,
            "risk_reward": 2.4,
            "tp1_hit": False,
            "open_time": datetime.now(timezone.utc).isoformat(),
            "confluence": 75,
            "confidence": 74,
            "session": "London Close",
            "partial_realized_pnl_usd": 0.0,
            "partial_close_events": [],
        }
        trade.update(overrides)
        return trade

    def test_monitor_hits_tp1_when_recent_candle_touched_level(self):
        client = _MonitorClient(
            current_price={
                "bid": 1.14900,
                "ask": 1.14920,
                "mid": 1.14910,
            },
            open_trades=[
                {
                    "id": "200",
                    "instrument": "EUR_USD",
                    "units": 100000.0,
                    "open_price": 1.15374,
                    "unrealized_pl": 454.0,
                    "open_time": datetime.now(timezone.utc).isoformat(),
                }
            ],
            candles=self._candles(
                highs=[1.15100, 1.15020, 1.14980],
                lows=[1.14780, 1.14840, 1.14890],
            ),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            executor = _RecordingTradeExecutor(
                client,
                {"demo_mode": True, "tp2_trail": False, "tp1_close_percent": 0.50},
                Path(tmpdir),
            )
            executor.journal.save_open_trades({"trade_1": self._tracked_trade()})

            actions = executor.monitor_open_trades()
            tracked = executor.journal.load_open_trades()
            trade = tracked["trade_1"]

        self.assertEqual(executor.partial_calls, [("200", 50000)])
        self.assertEqual(executor.stop_moves, [("200", 1.15374)])
        self.assertTrue(any("TP1 handled" in item for item in actions))
        self.assertTrue(trade["tp1_hit"])
        self.assertEqual(trade["stop_loss"], 1.15374)
        self.assertEqual(trade["tp1_closed_units"], 50000)

    def test_monitor_trails_using_recent_favorable_extreme(self):
        client = _MonitorClient(
            current_price={
                "bid": 1.15130,
                "ask": 1.15150,
                "mid": 1.15140,
            },
            open_trades=[
                {
                    "id": "200",
                    "instrument": "EUR_USD",
                    "units": 50000.0,
                    "open_price": 1.15374,
                    "unrealized_pl": 112.0,
                    "open_time": datetime.now(timezone.utc).isoformat(),
                }
            ],
            candles=self._candles(
                highs=[1.15190, 1.15170, 1.15160],
                lows=[1.14700, 1.14730, 1.14760],
            ),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            executor = _RecordingTradeExecutor(
                client,
                {"demo_mode": True, "tp2_trail": True},
                Path(tmpdir),
            )
            executor.journal.save_open_trades(
                {
                    "trade_1": self._tracked_trade(
                        tp1_hit=True,
                        stop_loss=1.15374,
                        units=50000,
                    )
                }
            )

            actions = executor.monitor_open_trades()
            tracked = executor.journal.load_open_trades()
            trade = tracked["trade_1"]

        self.assertEqual(executor.partial_calls, [])
        self.assertEqual(executor.stop_moves, [("200", 1.15274)])
        self.assertTrue(any("Trailing stop" in item for item in actions))
        self.assertEqual(trade["stop_loss"], 1.15274)

    def test_monitor_uses_atr_based_trailing_when_entry_atr_is_saved(self):
        client = _MonitorClient(
            current_price={
                "bid": 1.15130,
                "ask": 1.15150,
                "mid": 1.15140,
            },
            open_trades=[
                {
                    "id": "200",
                    "instrument": "EUR_USD",
                    "units": 50000.0,
                    "open_price": 1.15374,
                    "unrealized_pl": 112.0,
                    "open_time": datetime.now(timezone.utc).isoformat(),
                }
            ],
            candles=self._candles(
                highs=[1.15190, 1.15170, 1.15160],
                lows=[1.14980, 1.14990, 1.15000],
            ),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            executor = _RecordingTradeExecutor(
                client,
                {"demo_mode": True, "tp2_trail": True, "trail_atr_multiplier": 1.0},
                Path(tmpdir),
            )
            executor.journal.save_open_trades(
                {
                    "trade_1": self._tracked_trade(
                        tp1_hit=True,
                        stop_loss=1.15374,
                        units=50000,
                        atr_1h_at_entry=0.0020,
                    )
                }
            )

            actions = executor.monitor_open_trades()
            tracked = executor.journal.load_open_trades()
            trade = tracked["trade_1"]

        self.assertEqual(executor.partial_calls, [])
        self.assertEqual(executor.stop_moves, [("200", 1.1518)])
        self.assertTrue(any("Trailing stop" in item for item in actions))
        self.assertEqual(trade["stop_loss"], 1.1518)

    def test_monitor_applies_session_specific_time_stop(self):
        client = _MonitorClient(
            current_price={
                "bid": 1.15000,
                "ask": 1.15020,
                "mid": 1.15010,
            },
            open_trades=[
                {
                    "id": "200",
                    "instrument": "EUR_USD",
                    "units": 100000.0,
                    "open_price": 1.15374,
                    "unrealized_pl": -600.0,
                    "open_time": (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat(),
                }
            ],
            candles=self._candles(
                highs=[1.15100, 1.15080, 1.15060],
                lows=[1.14980, 1.14970, 1.14960],
            ),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            executor = _RecordingTradeExecutor(
                client,
                {
                    "demo_mode": True,
                    "early_momentum_exit": False,
                    "time_stop_hours": {
                        "London Close": 3,
                        "default": 8,
                    },
                },
                Path(tmpdir),
            )
            executor.journal.save_open_trades(
                {
                    "trade_1": self._tracked_trade(
                        session="London Close",
                        open_time=(datetime.now(timezone.utc) - timedelta(hours=4)).isoformat(),
                    )
                }
            )

            actions = executor.monitor_open_trades()
            tracked = executor.journal.load_open_trades()

        self.assertTrue(any("Time stop" in item for item in actions))
        self.assertEqual(executor.close_calls, ["200"])
        self.assertEqual(tracked, {})

    def test_monitor_waits_when_adaptive_time_stop_extends_the_window(self):
        client = _MonitorClient(
            current_price={
                "bid": 1.15000,
                "ask": 1.15020,
                "mid": 1.15010,
            },
            open_trades=[
                {
                    "id": "200",
                    "instrument": "EUR_USD",
                    "units": 100000.0,
                    "open_price": 1.15374,
                    "unrealized_pl": -600.0,
                    "open_time": (datetime.now(timezone.utc) - timedelta(hours=4)).isoformat(),
                }
            ],
            candles=self._candles(
                highs=[1.15100, 1.15080, 1.15060],
                lows=[1.14980, 1.14970, 1.14960],
            ),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            executor = _RecordingTradeExecutor(
                client,
                {
                    "demo_mode": True,
                    "early_momentum_exit": False,
                    "time_stop_hours": {
                        "London Close": 3,
                        "default": 8,
                    },
                    "adaptive_time_stop": True,
                    "adaptive_time_stop_extensions": {
                        "trend_aligned_hours": 1.0,
                        "macro_aligned_hours": 0.5,
                        "high_volatility_hours": 1.0,
                        "strong_signal_hours": 0.5,
                        "strong_signal_threshold": 85,
                        "max_total_hours": 2.0,
                    },
                },
                Path(tmpdir),
            )
            executor.journal.save_open_trades(
                {
                    "trade_1": self._tracked_trade(
                        session="London Close",
                        open_time=(datetime.now(timezone.utc) - timedelta(hours=4)).isoformat(),
                        technical_analysis={
                            "ema_bias": "BEARISH",
                            "market_regime": "HIGH_VOLATILITY",
                        },
                        macro_bias={"alignment": "ALIGNED"},
                        confluence=90,
                    )
                }
            )

            actions = executor.monitor_open_trades()
            tracked = executor.journal.load_open_trades()

        self.assertFalse(any("Time stop" in item for item in actions))
        self.assertEqual(executor.close_calls, [])
        self.assertIn("trade_1", tracked)

    def test_monitor_applies_early_momentum_exit_when_trade_stalls(self):
        candles = pd.DataFrame(
            {
                "high": [1.15100, 1.15080, 1.15060],
                "low": [1.14980, 1.14970, 1.14960],
                "close": [1.14990, 1.14985, 1.14980],
            }
        )
        client = _MonitorClient(
            current_price={
                "bid": 1.14970,
                "ask": 1.14990,
                "mid": 1.14980,
            },
            open_trades=[
                {
                    "id": "200",
                    "instrument": "EUR_USD",
                    "units": 100000.0,
                    "open_price": 1.15374,
                    "unrealized_pl": -384.0,
                    "open_time": (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
                }
            ],
            candles=candles,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            executor = _RecordingTradeExecutor(
                client,
                {
                    "demo_mode": True,
                    "early_momentum_exit": True,
                    "early_momentum_minutes": 60,
                    "early_momentum_max_gap_pips": 15.0,
                },
                Path(tmpdir),
            )
            executor.journal.save_open_trades(
                {
                    "trade_1": self._tracked_trade(
                        open_time=(datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(),
                        stop_loss=1.15650,
                        tp1=1.14800,
                        tp2=1.14413,
                    )
                }
            )

            actions = executor.monitor_open_trades()
            tracked = executor.journal.load_open_trades()

        self.assertTrue(any("Closed trade 200 after 60m" in item for item in actions))
        self.assertEqual(executor.close_calls, ["200"])
        self.assertEqual(tracked, {})

if __name__ == "__main__":
    unittest.main()
