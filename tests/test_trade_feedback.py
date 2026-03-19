import json
import tempfile
import unittest
from pathlib import Path

from app.analysis.agent import ForexAnalystAgent
from app.analysis.trade_feedback import TradeFeedbackManager
from app.execution.trade_executor import TradeExecutor


class _DummyClient:
    pass


class _DummyRag:
    def __init__(self):
        self.feedback_calls = []

    def store_feedback(self, feedback_text: str, trade_date: str, pair: str):
        self.feedback_calls.append((feedback_text, trade_date, pair))
        return 1


class TradeFeedbackTests(unittest.TestCase):
    def test_track_trade_persists_signal_context(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            executor = TradeExecutor(_DummyClient(), {"demo_mode": True}, Path(tmpdir))
            signal = {
                "pair": "EUR/USD",
                "timestamp": "2026-03-19T13:00:00Z",
                "session": "London Close",
                "signal_strength": "MODERATE",
                "confluence_score": 75,
                "macro_bias": {
                    "weekly": "BULLISH",
                    "daily": "BEARISH",
                    "h4": "BEARISH",
                    "alignment": "MIXED",
                },
                "technical_analysis": {
                    "ema_bias": "BEARISH",
                    "adx_14": 27.2,
                    "market_regime": "TRENDING",
                    "rsi_14": 48.1,
                    "rsi_signal": "NEUTRAL",
                },
                "ict_analysis": {
                    "order_block": {"present": True, "type": "BEARISH", "level": 1.1537},
                    "fair_value_gap": {"present": False, "type": "NONE"},
                    "liquidity": {"recent_sweep": True, "direction": "BUY_SIDE", "swept_level": 1.1527},
                    "premium_discount": "PREMIUM",
                },
                "fundamental": {
                    "rate_differential": "+1.62% USD favor supports bearish EUR/USD bias",
                    "dxy_direction": "RISING",
                    "cot_bias": "BULLISH",
                    "next_news_event": "USD Unemployment Claims in 2h 45m",
                    "news_risk": "MEDIUM",
                },
                "reasoning": [
                    "Recent buy-side sweep suggests institutional selling interest",
                    "Rising DXY supports downside pressure",
                ],
                "key_risk": "Upcoming USD claims release could disrupt the move",
                "knowledge_sources_used": ["The Forex Trading Course"],
                "trade_management": {"tp1_action": "Close 50% at TP1"},
                "validator_overrides": [],
                "log_filename": "signal_20260319_130000.json",
                "signal": {
                    "direction": "SELL",
                    "confidence": 74,
                    "entry_zone": [1.1535, 1.1540],
                    "stop_loss": 1.1565,
                    "take_profit_1": 1.1480,
                    "take_profit_2": 1.1441,
                    "risk_reward": 2.4,
                    "order_type": "LIMIT",
                },
            }
            result = {
                "order_id": "100",
                "trade_id": "200",
                "units": 362318,
                "entry_price": 1.15374,
            }

            executor._track_trade(signal, result)
            tracked = executor._load_open_trades()
            trade = next(iter(tracked.values()))

            self.assertEqual(trade["signal_log_filename"], "signal_20260319_130000.json")
            self.assertEqual(trade["reasoning"][0], signal["reasoning"][0])
            self.assertEqual(
                trade["fundamental_context"]["next_news_event"],
                "USD Unemployment Claims in 2h 45m",
            )
            self.assertEqual(trade["knowledge_sources"][0], "The Forex Trading Course")
            self.assertGreater(trade["initial_risk_usd"], 0)
            self.assertTrue(trade["signal_timeline_file"])

            timeline_path = Path(tmpdir) / "trade_signal_timelines" / trade["signal_timeline_file"]
            timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
            self.assertEqual(timeline["timeline_status"], "OPEN")
            self.assertEqual(len(timeline["analysis_events"]), 1)
            self.assertEqual(
                timeline["analysis_events"][0]["event_type"],
                "ENTRY_SIGNAL",
            )

    def test_trade_timeline_collects_loop_signals_until_close(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            executor = TradeExecutor(_DummyClient(), {"demo_mode": True}, log_dir)
            entry_signal = {
                "pair": "EUR/USD",
                "timestamp": "2026-03-19T13:00:00Z",
                "session": "London Close",
                "signal_strength": "MODERATE",
                "confluence_score": 75,
                "reasoning": ["Entry thesis"],
                "fundamental": {"next_news_event": "USD Claims", "news_risk": "MEDIUM"},
                "signal": {
                    "direction": "SELL",
                    "confidence": 74,
                    "entry_zone": [1.1535, 1.1540],
                    "stop_loss": 1.1565,
                    "take_profit_1": 1.1480,
                    "take_profit_2": 1.1441,
                    "risk_reward": 2.4,
                    "order_type": "LIMIT",
                },
                "log_filename": "signal_20260319_130000.json",
            }
            result = {
                "order_id": "100",
                "trade_id": "200",
                "units": 362318,
                "entry_price": 1.15374,
            }
            executor._track_trade(entry_signal, result)

            loop_signal = {
                "pair": "EUR/USD",
                "timestamp": "2026-03-19T13:10:00Z",
                "session": "London Close",
                "confluence_score": 68,
                "signal_strength": "MODERATE",
                "key_risk": "News approaching",
                "signal": {"direction": "SELL", "confidence": 69},
                "log_filename": "signal_20260319_131000.json",
            }
            executor.record_signal_snapshot_for_open_trades(loop_signal)

            tracked = executor._load_open_trades()
            trade = next(iter(tracked.values()))
            executor._log_trade_close(trade, "TIME_STOP", -250.0)

            timeline_path = log_dir / "trade_signal_timelines" / trade["signal_timeline_file"]
            timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
            self.assertEqual(timeline["timeline_status"], "CLOSED")
            self.assertEqual(len(timeline["analysis_events"]), 2)
            self.assertEqual(
                timeline["analysis_events"][1]["signal_log_filename"],
                "signal_20260319_131000.json",
            )
            self.assertEqual(
                timeline["trade_management_events"][-1]["event_type"],
                "TRADE_CLOSED",
            )
            self.assertEqual(timeline["close_summary"]["close_reason"], "TIME_STOP")

    def test_closed_trade_feedback_marks_unknown_broker_exit_and_writes_detail(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir) / "logs"
            feedback_dir = Path(tmpdir) / "feedback"
            log_dir.mkdir(parents=True, exist_ok=True)
            feedback_dir.mkdir(parents=True, exist_ok=True)

            signal_log = log_dir / "signal_20260319_130000.json"
            with open(signal_log, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "timestamp": "2026-03-19T13:00:00Z",
                        "pair": "EUR/USD",
                        "session": "London Close",
                        "signal_strength": "MODERATE",
                        "macro_bias": {
                            "weekly": "BULLISH",
                            "daily": "BEARISH",
                            "h4": "BEARISH",
                            "alignment": "CONFLICTING",
                        },
                        "technical_analysis": {
                            "ema_bias": "BEARISH",
                            "adx_14": 24.07,
                            "market_regime": "RANGING",
                            "rsi_14": 47.92,
                            "rsi_signal": "NEUTRAL",
                        },
                        "ict_analysis": {
                            "order_block": {"present": True, "type": "BEARISH", "level": 1.15374},
                            "fair_value_gap": {"present": True, "type": "BULLISH", "lower": 1.14594, "upper": 1.14666},
                            "liquidity": {"recent_sweep": True, "direction": "BUY_SIDE", "swept_level": 1.15274},
                            "premium_discount": "EQUILIBRIUM",
                        },
                        "fundamental": {
                            "rate_differential": "+1.62% USD favor supports bearish EUR/USD bias",
                            "dxy_direction": "RISING",
                            "cot_bias": "BULLISH",
                            "next_news_event": "USD Unemployment Claims in 2h 45m",
                            "news_risk": "MEDIUM",
                        },
                        "reasoning": [
                            "Recent buy-side liquidity sweep suggests institutional selling interest",
                            "Rate differential and rising DXY supported the short bias",
                        ],
                        "key_risk": "Upcoming USD news could increase volatility",
                        "knowledge_sources_used": [
                            "The Forex Trading Course - Post-news retracement concepts",
                        ],
                    },
                    f,
                    indent=2,
                )

            executor = TradeExecutor(_DummyClient(), {"demo_mode": True}, log_dir)
            trade = {
                "instrument": "EUR_USD",
                "pair": "EUR/USD",
                "direction": "SELL",
                "units": 362318,
                "entry_price": 1.15374,
                "stop_loss": 1.15650,
                "tp1": 1.14800,
                "tp2": 1.14413,
                "risk_reward": 2.4,
                "tp1_hit": True,
                "tp1_fill_price": 1.14800,
                "tp1_closed_units": 181159,
                "stop_moved_to_entry": True,
                "partial_realized_pnl_usd": 1031.61,
                "partial_close_events": [],
                "open_time": "2026-03-19T13:00:00+00:00",
                "session": "London Close",
                "confluence": 75,
                "signal_log_filename": signal_log.name,
            }

            executor._log_trade_close(trade, "CLOSED_BY_OANDA", None)
            closed_trade = executor.drain_closed_trades()[0]

            self.assertEqual(closed_trade["outcome"], "PARTIAL_WIN")
            self.assertTrue(closed_trade["pnl_is_partial_estimate"])
            self.assertIn("Final broker-managed exit", closed_trade["pnl_missing_reason"])

            agent = ForexAnalystAgent.__new__(ForexAnalystAgent)
            agent.rag = _DummyRag()
            agent.client = None
            agent.config = {"feedback_memory_limit": 15}
            agent.log_dir = log_dir
            agent.feedback = TradeFeedbackManager(
                rag_pipeline=agent.rag,
                config=agent.config,
                log_dir=log_dir,
                feedback_dir=feedback_dir,
            )
            agent.feedback_dir = feedback_dir
            agent.feedback_memory = agent.feedback.feedback_memory

            agent.record_trade_outcome(closed_trade)

            feedback_files = list(feedback_dir.glob("feedback_*.md"))
            self.assertEqual(len(feedback_files), 1)
            content = feedback_files[0].read_text(encoding="utf-8")

            self.assertIn("Saved entry thesis:", content)
            self.assertIn("The saved entry snapshot explicitly recorded the next calendar event", content)
            self.assertIn("did not completely miss the calendar", content)
            self.assertIn("DATA GAPS / WHY SOME DETAILS ARE MISSING:", content)
            self.assertIn("Final broker-managed exit was not fetched", content)


if __name__ == "__main__":
    unittest.main()
