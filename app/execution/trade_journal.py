from __future__ import annotations

import json
import logging
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.text_utils import normalize_pair, slugify_text

logger = logging.getLogger(__name__)


class TradeJournal:
    def __init__(self, log_dir: Path):
        self.log_dir = Path(log_dir)
        self.open_trades_file = self.log_dir / "open_trades.json"
        self.trades_csv = self.log_dir / "trades.csv"
        self.daily_state_file = self.log_dir / "daily_state.json"
        self.closed_trades_file = self.log_dir / "closed_trades.jsonl"
        self.trade_timelines_dir = self.log_dir / "trade_signal_timelines"
        self._pending_feedback: list[dict[str, Any]] = []
        self._init_logs()

    def _init_logs(self):
        self.trade_timelines_dir.mkdir(parents=True, exist_ok=True)
        if not self.trades_csv.exists():
            with open(self.trades_csv, "w") as f:
                f.write(
                    "timestamp,order_id,trade_id,instrument,direction,"
                    "units,entry_price,stop_loss,tp1,tp2,status,pnl,notes\n"
                )

    @staticmethod
    def _copy_json_value(value):
        return deepcopy(value)

    def load_open_trades(self) -> dict:
        if self.open_trades_file.exists():
            with open(self.open_trades_file) as f:
                return json.load(f)
        return {}

    def save_open_trades(self, trades: dict):
        with open(self.open_trades_file, "w") as f:
            json.dump(trades, f, indent=2)

    def _timeline_path(self, filename: str | None) -> Path | None:
        if not filename:
            return None
        return self.trade_timelines_dir / filename

    def _read_timeline(self, filename: str) -> dict:
        path = self._timeline_path(filename)
        if path is None or not path.exists():
            return {}
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def _write_timeline(self, filename: str, payload: dict):
        path = self._timeline_path(filename)
        if path is None:
            return
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def _build_signal_event(self, signal: dict, event_type: str) -> dict:
        return {
            "event_type": event_type,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            "signal_timestamp": signal.get("timestamp", ""),
            "signal_log_filename": signal.get("log_filename", ""),
            "session": signal.get("session", ""),
            "direction": signal.get("signal", {}).get("direction", ""),
            "confidence": signal.get("signal", {}).get("confidence", 0),
            "confluence_score": signal.get("confluence_score", 0),
            "signal_strength": signal.get("signal_strength", ""),
            "key_risk": signal.get("key_risk", ""),
            "signal_snapshot": self._copy_json_value(signal),
        }

    def _append_event_if_new(self, timeline: dict, section: str, event: dict) -> bool:
        events = timeline.setdefault(section, [])
        new_filename = event.get("signal_log_filename")
        new_type = event.get("event_type")
        new_timestamp = event.get("signal_timestamp")

        if new_filename:
            for existing in events:
                if (
                    existing.get("event_type") == new_type
                    and existing.get("signal_log_filename") == new_filename
                ):
                    return False
        elif new_timestamp:
            for existing in events:
                if (
                    existing.get("event_type") == new_type
                    and existing.get("signal_timestamp") == new_timestamp
                ):
                    return False
        else:
            for existing in events:
                if existing == event:
                    return False

        events.append(event)
        return True

    def _build_entry_context(self, signal: dict, result: dict) -> dict:
        sig = signal.get("signal", {})
        entry_price = float(result.get("entry_price", 0) or 0)
        stop_loss = float(sig.get("stop_loss", 0) or 0)
        units = abs(float(result.get("units", 0) or 0))
        stop_distance = abs(entry_price - stop_loss)
        initial_risk_usd = round(stop_distance * units, 2) if stop_distance and units else 0.0

        return {
            "pair": signal.get("pair", "EUR/USD"),
            "signal_timestamp": signal.get("timestamp", ""),
            "signal_log_filename": signal.get("log_filename", ""),
            "signal_strength": signal.get("signal_strength", ""),
            "macro_bias": self._copy_json_value(signal.get("macro_bias", {})),
            "technical_analysis": self._copy_json_value(signal.get("technical_analysis", {})),
            "ict_analysis": self._copy_json_value(signal.get("ict_analysis", {})),
            "fundamental_context": self._copy_json_value(signal.get("fundamental", {})),
            "reasoning": self._copy_json_value(signal.get("reasoning", [])),
            "key_risk": signal.get("key_risk", ""),
            "knowledge_sources": self._copy_json_value(signal.get("knowledge_sources_used", [])),
            "trade_management_plan": self._copy_json_value(signal.get("trade_management", {})),
            "validator_overrides": self._copy_json_value(signal.get("validator_overrides", [])),
            "order_type": sig.get("order_type", ""),
            "confidence": sig.get("confidence", 0),
            "risk_reward": sig.get("risk_reward", 0),
            "initial_risk_usd": initial_risk_usd,
            "entry_signal_snapshot": self._copy_json_value(signal),
        }

    def _ensure_trade_signal_timeline(self, trade: dict) -> str | None:
        if trade.get("signal_timeline_file"):
            filename = trade["signal_timeline_file"]
            path = self._timeline_path(filename)
            if path and path.exists():
                return filename

        if not trade.get("trade_id"):
            return None

        opened_at = trade.get("open_time") or datetime.now(timezone.utc).isoformat()
        timestamp_slug = (
            opened_at.replace(":", "").replace("-", "").replace("+", "_").replace(".", "_")
        )
        pair_slug = slugify_text(trade.get("pair") or trade.get("instrument", "eur-usd"))
        trade_id_slug = slugify_text(str(trade.get("trade_id") or trade.get("order_id") or "unknown"))
        filename = f"trade_timeline_{timestamp_slug}_{pair_slug}_{trade_id_slug}.json"

        timeline = {
            "timeline_status": "OPEN",
            "pair": trade.get("pair") or trade.get("instrument", "EUR_USD"),
            "instrument": trade.get("instrument", "EUR_USD"),
            "direction": trade.get("direction", ""),
            "session": trade.get("session", ""),
            "order_id": trade.get("order_id"),
            "trade_id": trade.get("trade_id"),
            "opened_at": trade.get("open_time"),
            "entry_price": trade.get("entry_price"),
            "stop_loss": trade.get("stop_loss"),
            "take_profit_1": trade.get("tp1"),
            "take_profit_2": trade.get("tp2"),
            "risk_reward": trade.get("risk_reward"),
            "confidence": trade.get("confidence"),
            "confluence_score": trade.get("confluence"),
            "signal_log_filename": trade.get("signal_log_filename", ""),
            "analysis_events": [],
            "trade_management_events": [],
            "close_summary": None,
        }

        entry_signal = trade.get("entry_signal_snapshot")
        if isinstance(entry_signal, dict) and entry_signal:
            self._append_event_if_new(
                timeline,
                "analysis_events",
                self._build_signal_event(entry_signal, "ENTRY_SIGNAL"),
            )

        self._write_timeline(filename, timeline)
        trade["signal_timeline_file"] = filename
        return filename

    def _append_management_event(self, trade: dict, event: dict):
        filename = self._ensure_trade_signal_timeline(trade)
        if not filename:
            return
        timeline = self._read_timeline(filename)
        if not timeline:
            return
        payload = {"recorded_at": datetime.now(timezone.utc).isoformat(), **self._copy_json_value(event)}
        if self._append_event_if_new(timeline, "trade_management_events", payload):
            self._write_timeline(filename, timeline)

    def record_signal_snapshot_for_open_trades(self, signal: dict):
        tracked = self.load_open_trades()
        if not tracked:
            return

        signal_pair = normalize_pair(signal.get("pair", "EUR/USD"))
        updated = False
        for trade in tracked.values():
            if not trade.get("trade_id"):
                continue
            trade_pair = normalize_pair(trade.get("pair") or trade.get("instrument", ""))
            if trade_pair and signal_pair and trade_pair != signal_pair:
                continue

            filename = self._ensure_trade_signal_timeline(trade)
            if not filename:
                continue

            timeline = self._read_timeline(filename)
            if not timeline:
                continue

            event = self._build_signal_event(signal, "LOOP_SIGNAL")
            if self._append_event_if_new(timeline, "analysis_events", event):
                self._write_timeline(filename, timeline)
                updated = True

        if updated:
            self.save_open_trades(tracked)

    def _finalize_trade_signal_timeline(self, trade: dict, close_record: dict):
        filename = trade.get("signal_timeline_file") or self._ensure_trade_signal_timeline(trade)
        if not filename:
            return

        timeline = self._read_timeline(filename)
        if not timeline:
            return

        timeline["timeline_status"] = "CLOSED"
        timeline["closed_at"] = datetime.now(timezone.utc).isoformat()
        timeline["close_summary"] = self._copy_json_value(close_record)
        timeline["trade_id"] = trade.get("trade_id") or timeline.get("trade_id")
        timeline["order_id"] = trade.get("order_id") or timeline.get("order_id")

        self._append_event_if_new(
            timeline,
            "trade_management_events",
            {
                "event_type": "TRADE_CLOSED",
                "close_reason": close_record.get("close_reason", ""),
                "outcome": close_record.get("outcome", ""),
                "pnl_r": close_record.get("pnl_r"),
                "pnl_usd": close_record.get("pnl_usd"),
                "pnl_is_partial_estimate": close_record.get("pnl_is_partial_estimate", False),
            },
        )
        self._write_timeline(filename, timeline)

    def _describe_missing_close_details(
        self, trade: dict, reason: str, exit_pnl_known: bool
    ) -> list[str]:
        gaps: list[str] = []

        if not trade.get("reasoning"):
            gaps.append(
                "Exact entry reasoning bullets were not attached to the open trade record. "
                "That usually means this trade was opened before richer review context was persisted "
                "or the signal payload did not include reasoning."
            )

        if not trade.get("fundamental_context"):
            gaps.append(
                "No entry-time fundamental snapshot was saved with the trade, so calendar/news attribution "
                "cannot be reconstructed exactly."
            )

        if not trade.get("signal_log_filename"):
            gaps.append(
                "No signal log filename was linked to the trade, so the review cannot reload the exact "
                "saved signal JSON for fallback context."
            )

        if reason == "CLOSED_BY_OANDA" and not exit_pnl_known:
            gaps.append(
                "The trade was closed by a broker-managed order on OANDA, but the executor does not yet fetch "
                "the broker close transaction. Exact exit price and full realized PnL for the final leg were not captured."
            )

        return gaps

    @staticmethod
    def _determine_close_outcome(trade: dict, total_pnl: float, exit_pnl_known: bool) -> str:
        if exit_pnl_known:
            if total_pnl > 0:
                return "WIN"
            if total_pnl < 0:
                return "LOSS"
            return "BREAKEVEN"

        if trade.get("tp1_hit") and float(trade.get("partial_realized_pnl_usd", 0) or 0) > 0:
            return "PARTIAL_WIN"
        return "UNKNOWN"

    def record_trade_open(self, signal: dict, result: dict):
        tracked = self.load_open_trades()
        sig = signal.get("signal", {})
        key = f"trade_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        entry_context = self._build_entry_context(signal, result)

        tracked[key] = {
            "order_id": result.get("order_id"),
            "trade_id": result.get("trade_id"),
            "instrument": "EUR_USD",
            "direction": sig.get("direction"),
            "units": result.get("units"),
            "entry_price": result.get("entry_price"),
            "stop_loss": sig.get("stop_loss"),
            "tp1": sig.get("take_profit_1"),
            "tp2": sig.get("take_profit_2"),
            "risk_reward": sig.get("risk_reward"),
            "tp1_hit": False,
            "open_time": datetime.now(timezone.utc).isoformat(),
            "confluence": signal.get("confluence_score"),
            "confidence": sig.get("confidence"),
            "session": signal.get("session", ""),
            "partial_realized_pnl_usd": 0.0,
            "partial_close_events": [],
            **entry_context,
        }
        if tracked[key].get("trade_id"):
            self._ensure_trade_signal_timeline(tracked[key])
        self.save_open_trades(tracked)
        self._log_trade_open_to_csv(signal, result)

    def _log_trade_open_to_csv(self, signal: dict, result: dict):
        sig = signal.get("signal", {})
        row = ",".join(
            str(x)
            for x in [
                datetime.now(timezone.utc).isoformat(),
                result.get("order_id", ""),
                result.get("trade_id", ""),
                "EUR_USD",
                sig.get("direction", ""),
                result.get("units", 0),
                result.get("entry_price", 0),
                sig.get("stop_loss", 0),
                sig.get("take_profit_1", 0),
                sig.get("take_profit_2", 0),
                "OPEN",
                "0",
                f"Conf:{sig.get('confidence')} Score:{signal.get('confluence_score')} Session:{signal.get('session', '')}",
            ]
        )
        with open(self.trades_csv, "a") as f:
            f.write(row + "\n")

    def record_order_fill(self, trade: dict, order_id: str, trade_id: str):
        trade["trade_id"] = trade_id
        trade["open_time"] = datetime.now(timezone.utc).isoformat()
        self._ensure_trade_signal_timeline(trade)
        self._append_management_event(
            trade,
            {
                "event_type": "ORDER_FILLED",
                "order_id": order_id,
                "trade_id": trade_id,
                "note": "Pending entry order filled and trade became active.",
            },
        )

    def record_tp1_partial(self, trade: dict, mid_price: float, close_units: int, partial_pnl: float):
        trade["tp1_hit"] = True
        trade["tp1_fill_price"] = round(mid_price, 5)
        trade["tp1_closed_units"] = close_units
        trade["stop_moved_to_entry"] = True
        trade["partial_realized_pnl_usd"] = round(
            float(trade.get("partial_realized_pnl_usd", 0) or 0) + partial_pnl,
            2,
        )
        trade.setdefault("partial_close_events", []).append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": "TP1_PARTIAL",
                "price": round(mid_price, 5),
                "units_closed": close_units,
                "estimated_realized_pnl_usd": round(partial_pnl, 2),
                "note": "Closed 50% at TP1 and moved stop loss to entry.",
            }
        )
        self._append_management_event(
            trade,
            {
                "event_type": "TP1_PARTIAL",
                "price": round(mid_price, 5),
                "units_closed": close_units,
                "estimated_realized_pnl_usd": round(partial_pnl, 2),
                "note": "Closed 50% at TP1 and moved stop loss to entry.",
            },
        )

    def drain_closed_trades(self) -> list:
        closed = self._pending_feedback[:]
        self._pending_feedback = []
        return closed

    def get_daily_pnl_pct(self, current_balance: float) -> float:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        state: dict = {}

        if self.daily_state_file.exists():
            try:
                with open(self.daily_state_file) as f:
                    state = json.load(f)
            except Exception:
                state = {}

        if state.get("date") != today:
            state = {"date": today, "start_balance": current_balance}
            with open(self.daily_state_file, "w") as f:
                json.dump(state, f)
            return 0.0

        start_balance = state.get("start_balance", current_balance)
        if start_balance == 0:
            return 0.0
        return (current_balance - start_balance) / start_balance * 100

    # =========================================================================
    # STRUCTURED TRADE ANALYSIS HELPERS
    # =========================================================================

    @staticmethod
    def _grade_setup(confluence: int, confidence: int, rr: float, validator_overrides: list) -> str:
        """
        Grade the setup quality independent of outcome.
        A = full confluence, high confidence, strong R:R
        B = good setup, all rules met
        C = marginal (barely met thresholds)
        F = rule violation or override fired
        """
        if validator_overrides:
            return "F"
        score = (
            (3 if confluence >= 85 else 2 if confluence >= 75 else 1)
            + (2 if confidence >= 75 else 1 if confidence >= 70 else 0)
            + (2 if rr >= 3.0 else 1 if rr >= 2.5 else 0)
        )
        return "A" if score >= 6 else ("B" if score >= 4 else ("C" if score >= 2 else "F"))

    @staticmethod
    def _classify_entry_timing(trade: dict) -> str:
        """How close was the actual entry price to the optimal zone midpoint."""
        signal = trade.get("entry_signal_snapshot") or {}
        entry_zone = (signal.get("signal") or {}).get("entry_zone", [])
        entry_price = float(trade.get("entry_price", 0) or 0)
        if not entry_zone or len(entry_zone) < 2 or not entry_price:
            return "UNKNOWN"
        zone_low, zone_high = float(entry_zone[0]), float(entry_zone[1])
        zone_width = zone_high - zone_low
        if zone_width <= 0:
            return "UNKNOWN"
        zone_mid = (zone_low + zone_high) / 2
        deviation = abs(entry_price - zone_mid)
        if deviation <= zone_width * 0.25:
            return "OPTIMAL"
        if zone_low <= entry_price <= zone_high:
            return "ACCEPTABLE"
        if deviation <= zone_width:
            return "AT_EDGE"
        return "OUTSIDE_ZONE"

    @staticmethod
    def _classify_ict_post_hoc(trade: dict, outcome: str) -> dict:
        """
        Proxy check: did the ICT concepts used at entry actually play out?
        Uses trade result as the signal since we don't have live price data at close.
        """
        ict = trade.get("ict_analysis") or {}
        direction = str(trade.get("direction", "")).upper()
        tp1_hit = bool(trade.get("tp1_hit"))
        won = outcome in ("WIN", "PARTIAL_WIN")

        ob = ict.get("order_block") or {}
        fvg = ict.get("fair_value_gap") or {}
        liq = ict.get("liquidity") or {}
        pd = str(ict.get("premium_discount") or "").upper()

        in_correct_zone = (direction == "BUY" and "DISCOUNT" in pd) or (
            direction == "SELL" and "PREMIUM" in pd
        )

        return {
            "ob_held": (tp1_hit or won) if ob.get("present") else None,
            "fvg_acted_as_magnet": tp1_hit if fvg.get("present") else None,
            "sweep_led_to_reversal": won if liq.get("recent_sweep") else None,
            "pd_zone_respected": won if (in_correct_zone and pd) else (False if pd else None),
        }

    @staticmethod
    def _classify_root_cause(
        outcome: str,
        setup_grade: str,
        close_reason: str,
        macro_bias: dict,
        fundamental: dict,
        validator_overrides: list,
    ) -> str:
        """
        Classify why the trade won or lost based on process, not just result.
        This is the key field for process-based learning.
        """
        if setup_grade == "F" or validator_overrides:
            return "RULE_VIOLATION"
        news_risk = str((fundamental or {}).get("news_risk", "")).upper()
        if news_risk in ("HIGH", "MEDIUM") and outcome == "LOSS":
            return "NEWS_INTERFERENCE"
        alignment = str((macro_bias or {}).get("alignment", "")).upper()
        if alignment in ("MIXED", "CONFLICTING") and outcome == "LOSS":
            return "WRONG_MTF_READ"
        if close_reason == "TIME_STOP" and outcome not in ("WIN",):
            return "ENTRY_TIMING_COST"
        if setup_grade in ("A", "B") and outcome == "WIN":
            return "CORRECT_PROCESS_CORRECT_OUTCOME"
        if setup_grade in ("A", "B") and outcome in ("LOSS", "BREAKEVEN", "UNKNOWN"):
            return "CORRECT_PROCESS_ADVERSE_OUTCOME"
        if setup_grade == "C" and outcome == "WIN":
            return "MARGINAL_SETUP_GOT_LUCKY"
        if setup_grade == "C":
            return "MARGINAL_SETUP_POOR_OUTCOME"
        return "UNDETERMINED"

    @staticmethod
    def _generate_pattern_tags(trade: dict, setup_grade: str) -> list:
        """
        Generate structured tags that accumulate into an edge database over time.
        Allows queries like: 'win rate on ob_entry + post_sweep + with_weekly_trend'
        """
        tags = []
        session = str(trade.get("session", "")).lower().replace(" ", "_")
        if session:
            tags.append(session)

        ict = trade.get("ict_analysis") or {}
        if (ict.get("order_block") or {}).get("present"):
            tags.append("ob_entry")
        if (ict.get("fair_value_gap") or {}).get("present"):
            tags.append("fvg_confluence")
        if (ict.get("liquidity") or {}).get("recent_sweep"):
            tags.append("post_sweep")
        pd = str(ict.get("premium_discount") or "").upper()
        if "DISCOUNT" in pd:
            tags.append("discount_zone")
        elif "PREMIUM" in pd:
            tags.append("premium_zone")

        macro = trade.get("macro_bias") or {}
        if isinstance(macro, dict):
            alignment = str(macro.get("alignment", "")).upper()
            weekly = str(macro.get("weekly", "")).upper()
            direction = str(trade.get("direction", "")).upper()
            tags.append("mtf_aligned" if alignment == "ALIGNED" else "mtf_mixed")
            if (direction == "BUY" and weekly == "BULLISH") or (
                direction == "SELL" and weekly == "BEARISH"
            ):
                tags.append("with_weekly_trend")
            elif weekly in ("BULLISH", "BEARISH"):
                tags.append("against_weekly_trend")

        confluence = int(trade.get("confluence", 0) or 0)
        tags.append("high_confluence" if confluence >= 85 else "moderate_confluence")
        if int(trade.get("confidence", 0) or 0) < 70:
            tags.append("marginal_confidence")
        if float(trade.get("risk_reward", 0) or 0) < 2.5:
            tags.append("marginal_rr")

        fund = trade.get("fundamental_context") or {}
        if str((fund if isinstance(fund, dict) else {}).get("news_risk", "")).upper() in (
            "HIGH",
            "MEDIUM",
        ):
            tags.append("news_day")

        grade_tag = {"A": "grade_a", "B": "grade_b", "C": "grade_c", "F": "grade_f"}.get(
            setup_grade
        )
        if grade_tag:
            tags.append(grade_tag)

        return tags

    def record_trade_close(self, trade: dict, reason: str, pnl: float | None = None):
        duration_hours = ""
        open_time = trade.get("open_time")
        if open_time:
            try:
                opened_at = datetime.fromisoformat(open_time)
                duration_hours = round(
                    (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600,
                    2,
                )
            except Exception:
                duration_hours = ""

        units = abs(float(trade.get("units", 0) or 0))
        stop_distance = abs(
            float(trade.get("entry_price", 0) or 0) - float(trade.get("stop_loss", 0) or 0)
        )
        risk_amount = max(
            float(trade.get("initial_risk_usd", 0) or 0),
            stop_distance * units,
            0.0001,
        )
        partial_realized_pnl = float(trade.get("partial_realized_pnl_usd", 0) or 0)
        exit_pnl_known = pnl is not None
        total_pnl = partial_realized_pnl + (float(pnl) if pnl is not None else 0.0)
        outcome = self._determine_close_outcome(trade, total_pnl, exit_pnl_known)
        pnl_missing_reason = ""
        if not exit_pnl_known and reason == "CLOSED_BY_OANDA":
            pnl_missing_reason = (
                "Final broker-managed exit was not fetched from OANDA transaction history. "
                "Only the recorded partial-close estimate, if any, is known."
            )

        setup_grade = TradeJournal._grade_setup(
            int(trade.get("confluence", 0) or 0),
            int(trade.get("confidence", 0) or 0),
            float(trade.get("risk_reward", 0) or 0),
            list(trade.get("validator_overrides") or []),
        )
        entry_timing = TradeJournal._classify_entry_timing(trade)
        ict_post_hoc = TradeJournal._classify_ict_post_hoc(trade, outcome)
        root_cause = TradeJournal._classify_root_cause(
            outcome,
            setup_grade,
            reason,
            trade.get("macro_bias") or {},
            trade.get("fundamental_context") or {},
            list(trade.get("validator_overrides") or []),
        )
        pattern_tags = TradeJournal._generate_pattern_tags(trade, setup_grade)

        feedback_record = {
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "pair": trade.get("pair") or trade.get("instrument", "EUR_USD"),
            "direction": trade.get("direction", ""),
            "entry_price": trade.get("entry_price", 0),
            "stop_loss": trade.get("stop_loss", 0),
            "take_profit": trade.get("tp2", 0),
            "lot_size": trade.get("units", 0),
            "outcome": outcome,
            "pnl_r": round(total_pnl / risk_amount, 2),
            "pnl_usd": round(total_pnl, 2),
            "duration_hours": duration_hours,
            "session": trade.get("session", ""),
            "confluence_score": trade.get("confluence", 0),
            "close_reason": reason,
            "close_reason_detail": (
                "Broker-managed protective order closed the trade."
                if reason == "CLOSED_BY_OANDA"
                else "Trade was closed by the executor's time-stop rule."
            ),
            "setup_grade": setup_grade,
            "entry_timing": entry_timing,
            "ict_post_hoc": ict_post_hoc,
            "root_cause": root_cause,
            "pattern_tags": pattern_tags,
            "signal_timestamp": trade.get("signal_timestamp", ""),
            "signal_log_filename": trade.get("signal_log_filename", ""),
            "signal_strength": trade.get("signal_strength", ""),
            "confidence": trade.get("confidence", 0),
            "risk_reward": trade.get("risk_reward", 0),
            "macro_bias": self._copy_json_value(trade.get("macro_bias", {})),
            "technical_analysis": self._copy_json_value(trade.get("technical_analysis", {})),
            "ict_analysis": self._copy_json_value(trade.get("ict_analysis", {})),
            "fundamental_context": self._copy_json_value(trade.get("fundamental_context", {})),
            "reasoning": self._copy_json_value(trade.get("reasoning", [])),
            "key_risk": trade.get("key_risk", ""),
            "knowledge_sources": self._copy_json_value(trade.get("knowledge_sources", [])),
            "trade_management_plan": self._copy_json_value(trade.get("trade_management_plan", {})),
            "validator_overrides": self._copy_json_value(trade.get("validator_overrides", [])),
            "tp1_hit": bool(trade.get("tp1_hit")),
            "tp1_fill_price": trade.get("tp1_fill_price"),
            "tp1_closed_units": trade.get("tp1_closed_units", 0),
            "stop_moved_to_entry": bool(trade.get("stop_moved_to_entry")),
            "partial_realized_pnl_usd": round(partial_realized_pnl, 2),
            "partial_close_events": self._copy_json_value(trade.get("partial_close_events", [])),
            "pnl_is_partial_estimate": not exit_pnl_known,
            "pnl_missing_reason": pnl_missing_reason,
            "missing_detail_reasons": self._describe_missing_close_details(
                trade, reason, exit_pnl_known
            ),
        }

        self._pending_feedback.append(feedback_record)
        self._finalize_trade_signal_timeline(trade, feedback_record)
        with open(self.closed_trades_file, "a") as f:
            f.write(json.dumps(self._pending_feedback[-1]) + "\n")

        row = ",".join(
            str(x)
            for x in [
                datetime.now(timezone.utc).isoformat(),
                trade.get("order_id", ""),
                trade.get("trade_id", ""),
                "EUR_USD",
                trade.get("direction", ""),
                trade.get("units", 0),
                trade.get("entry_price", 0),
                trade.get("stop_loss", 0),
                trade.get("tp1", 0),
                trade.get("tp2", 0),
                reason,
                round(total_pnl, 2),
                f"Session:{trade.get('session', '')}",
            ]
        )
        with open(self.trades_csv, "a") as f:
            f.write(row + "\n")

    def has_session_loss_streak(self, session: str, limit: int = 2) -> bool:
        if not session or not self.closed_trades_file.exists():
            return False

        try:
            closed_rows = []
            with open(self.closed_trades_file) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    row = json.loads(line)
                    if row.get("session") == session:
                        closed_rows.append(row)
        except Exception:
            return False

        if len(closed_rows) < limit:
            return False

        recent = closed_rows[-limit:]
        try:
            return all((row.get("outcome") == "LOSS") for row in recent)
        except Exception:
            return False
