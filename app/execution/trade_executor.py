# =============================================================================
# trade_executor.py — OANDA execution and live trade monitoring
#
# Responsibilities:
#   - pre-trade safety checks
#   - position sizing
#   - order placement / broker actions
#   - monitoring live trades for TP1 and time-stop rules
#
# Trade journaling, timeline persistence, and feedback payload creation live in
# app.execution.trade_journal.TradeJournal.
# =============================================================================

import logging
from datetime import datetime, timezone
from pathlib import Path

import requests

from app.analysis.scheduler import ALLOWED_ENTRY_SESSIONS
from app.execution.trade_journal import TradeJournal

logger = logging.getLogger(__name__)


class TradeExecutor:
    """
    Executes validated signals on OANDA demo account.
    Called from the demo loop after ForexAnalystAgent generates a signal.
    """

    INSTRUMENT = "EUR_USD"

    def __init__(self, oanda_client, trading_config: dict, log_dir: Path):
        self.client = oanda_client
        self.config = trading_config
        self.log_dir = Path(log_dir)
        self.demo_mode = trading_config.get("demo_mode", True)
        self.journal = TradeJournal(self.log_dir)

    def record_signal_snapshot_for_open_trades(self, signal: dict):
        self.journal.record_signal_snapshot_for_open_trades(signal)

    def drain_closed_trades(self) -> list:
        """
        Returns and clears trade records closed since last call.
        The demo loop calls this after monitor_open_trades() to feed
        outcomes into agent.record_trade_outcome().
        """
        return self.journal.drain_closed_trades()

    # =========================================================================
    # MAIN ENTRY POINT
    # =========================================================================

    def execute_signal(self, signal: dict) -> dict:
        """
        Called from demo loop after every signal.
        Places order on OANDA if signal qualifies.
        Returns execution result dict.
        """
        result = {
            "executed": False,
            "reason": "",
            "order_id": None,
            "trade_id": None,
            "units": 0,
            "entry_price": 0.0,
        }

        direction = signal.get("signal", {}).get("direction", "NEUTRAL")
        confidence = signal.get("signal", {}).get("confidence", 0)

        if direction == "NEUTRAL":
            result["reason"] = "Signal NEUTRAL — no trade"
            return result

        try:
            account = self.client.get_account_summary()
        except Exception as exc:
            result["reason"] = f"Account fetch failed: {exc}"
            return result

        ok, reason = self._pre_trade_checks(signal, account)
        if not ok:
            result["reason"] = reason
            logger.info(f"Trade blocked: {reason}")
            return result

        units = self._calculate_units(signal, account["equity"])
        if units == 0:
            result["reason"] = "Invalid position size calculated"
            return result

        try:
            order_result = self._place_order(signal, units, direction)
            result.update(
                {
                    "executed": True,
                    "order_id": order_result.get("order_id"),
                    "trade_id": order_result.get("trade_id"),
                    "units": units,
                    "entry_price": order_result.get(
                        "fill_price", signal["signal"]["entry_zone"][0]
                    ),
                    "reason": "Order placed successfully",
                }
            )

            self.journal.record_trade_open(signal, result)

            sig = signal["signal"]
            print(f"\n{'='*50}")
            print(f"🎯 ORDER PLACED — {'DEMO' if self.demo_mode else 'LIVE'}")
            print(f"{'='*50}")
            print(f"  Direction:  {direction}")
            print(f"  Units:      {units:,}")
            print(f"  Entry:      {result['entry_price']}")
            print(f"  Stop Loss:  {sig['stop_loss']}")
            print(f"  TP1:        {sig['take_profit_1']}  (close 50%)")
            print(f"  TP2:        {sig['take_profit_2']}  (trail rest)")
            print(f"  R:R:        {sig['risk_reward']}")
            print(f"  Confidence: {confidence}%")
            print(f"  Order ID:   {result['order_id']}")
            logger.info(
                f"Order placed: {direction} {units} {self.INSTRUMENT} | "
                f"Entry:{result['entry_price']} SL:{sig['stop_loss']} TP2:{sig['take_profit_2']}"
            )
        except Exception as exc:
            result["reason"] = f"Order placement failed: {exc}"
            logger.error(f"Order error: {exc}")

        return result

    # =========================================================================
    # PRE-TRADE SAFETY CHECKS
    # =========================================================================

    def _pre_trade_checks(self, signal: dict, account: dict) -> tuple:
        """
        Returns (True, "") if all checks pass.
        Returns (False, reason) if any check fails.
        """
        sig = signal.get("signal", {})
        confidence = sig.get("confidence", 0)
        rr = sig.get("risk_reward", 0)
        balance = account["balance"]
        session = signal.get("session", "")

        min_conf = self.config.get("min_confidence", 65)
        if confidence < min_conf:
            return False, f"Confidence {confidence}% < {min_conf}% threshold"

        min_rr = self.config.get("min_rr_ratio", 2.0)
        if rr < min_rr:
            return False, f"R:R {rr} below minimum {min_rr}"

        daily_pnl_pct = self.journal.get_daily_pnl_pct(balance)
        max_loss = self.config.get("max_daily_loss", 0.02)
        if daily_pnl_pct <= -(max_loss * 100):
            return (
                False,
                f"Daily loss limit hit ({daily_pnl_pct:.1f}%) — no more trades today",
            )

        if account["open_trade_count"] >= 3:
            return False, f"Already {account['open_trade_count']} open trades — max 3"

        if session not in ALLOWED_ENTRY_SESSIONS:
            return False, f"Session {session or 'UNKNOWN'} is outside allowed kill zones"

        if self.journal.has_session_loss_streak(session):
            return False, f"Two consecutive losses already recorded in {session}"

        try:
            for trade in self.client.get_open_trades():
                if trade["instrument"] == self.INSTRUMENT:
                    return False, f"{self.INSTRUMENT} position already open"
        except Exception:
            pass

        entry_zone = sig.get("entry_zone", [0, 0])
        if not entry_zone or entry_zone[0] == 0 or entry_zone[1] == 0:
            return False, "Invalid entry zone"

        if sig.get("stop_loss", 0) == 0:
            return False, "No stop loss in signal"

        return True, ""

    # =========================================================================
    # POSITION SIZING
    # =========================================================================

    def _calculate_units(self, signal: dict, equity: float) -> int:
        """
        units = (equity × risk%) / stop_distance_in_price

        For EUR/USD: P&L = units × price_change
        Example: $1,000 risk / 0.0030 stop = 333,333 units
        """
        sig = signal.get("signal", {})
        entry_zone = sig.get("entry_zone", [0, 0])
        stop_loss = sig.get("stop_loss", 0)

        if not entry_zone or stop_loss == 0:
            return 0

        entry_price = (entry_zone[0] + entry_zone[1]) / 2
        stop_distance = abs(entry_price - stop_loss)

        if stop_distance < 0.0005:
            logger.warning(f"Stop distance too small: {stop_distance:.5f}")
            return 0

        risk_amount = equity * self.config.get("max_risk_per_trade", 0.01)
        units = int(risk_amount / stop_distance)
        units = min(units, 500_000)
        units = max(units, 1_000)

        logger.info(
            f"Units: {units:,} | Equity: {equity:.0f} | "
            f"Risk: ${risk_amount:.0f} | Stop: {stop_distance:.5f}"
        )
        return units

    # =========================================================================
    # ORDER PLACEMENT
    # =========================================================================

    def _place_order(self, signal: dict, units: int, direction: str) -> dict:
        """
        POST order to OANDA REST API.
        BUY = positive units, SELL = negative units.
        Uses TP2 as the broker-managed take profit. TP1 is executor-managed.
        """
        sig = signal.get("signal", {})
        entry_zone = sig.get("entry_zone", [0, 0])
        stop_loss = sig.get("stop_loss", 0)
        tp2 = sig.get("take_profit_2", 0)
        order_type = sig.get("order_type", "LIMIT")

        entry_price = round((entry_zone[0] + entry_zone[1]) / 2, 5)
        signed_units = units if direction == "BUY" else -units

        order = {
            "instrument": self.INSTRUMENT,
            "units": str(signed_units),
            "stopLossOnFill": {
                "price": str(round(stop_loss, 5)),
                "timeInForce": "GTC",
            },
            "takeProfitOnFill": {
                "price": str(round(tp2, 5)),
                "timeInForce": "GTC",
            },
        }

        if order_type in {"LIMIT", "STOP_LIMIT"}:
            order["type"] = "LIMIT"
            order["price"] = str(entry_price)
            order["timeInForce"] = "GTC"
        elif order_type == "STOP":
            order["type"] = "STOP"
            order["price"] = str(entry_price)
            order["timeInForce"] = "GTC"
        else:
            order["type"] = "MARKET"

        url = f"{self.client.base_url}/accounts/{self.client.account_id}/orders"
        response = requests.post(
            url, headers=self.client.headers, json={"order": order}, timeout=10
        )
        data = response.json()

        if response.status_code not in (200, 201):
            raise Exception(f"OANDA {response.status_code}: {data.get('errorMessage', data)}")

        result = {"order_id": None, "trade_id": None, "fill_price": entry_price}

        if "orderCreateTransaction" in data:
            result["order_id"] = data["orderCreateTransaction"]["id"]
            logger.info(f"Limit order queued: ID {result['order_id']} @ {entry_price}")

        if "orderFillTransaction" in data:
            fill = data["orderFillTransaction"]
            result["trade_id"] = fill["tradeOpened"]["tradeID"]
            result["fill_price"] = float(fill["price"])
            logger.info(
                f"Market order filled: trade {result['trade_id']} @ {result['fill_price']}"
            )

        return result

    # =========================================================================
    # TRADE MONITORING
    # =========================================================================

    def monitor_open_trades(self) -> list:
        """
        Check tracked trades for:
        - TP1 hit → close 50%, move SL to breakeven
        - Time stop → close if -0.5R after N hours
        - Broker-managed close (SL / TP / manual)
        """
        actions = []
        tracked = self.journal.load_open_trades()
        if not tracked:
            return actions

        try:
            price_data = self.client.get_current_price(self.INSTRUMENT)
            mid_price = price_data["mid"]
            live_trades = {trade["id"]: trade for trade in self.client.get_open_trades()}
        except Exception as exc:
            logger.error(f"Monitor fetch error: {exc}")
            return actions

        now = datetime.now(timezone.utc)

        for key, trade in list(tracked.items()):
            trade_id = trade.get("trade_id")

            if trade_id and trade_id not in live_trades:
                msg = f"Trade {trade_id} closed by OANDA (SL/TP2 hit)"
                actions.append(msg)
                print(f"\n📋 {msg}")
                self.journal.record_trade_close(trade, "CLOSED_BY_OANDA")
                del tracked[key]
                continue

            trade_id = self._activate_pending_order_if_filled(trade, actions) or trade_id
            if not trade_id or trade_id not in live_trades:
                continue

            live_trade = live_trades[trade_id]
            time_stop_msg = self._apply_time_stop_if_needed(trade, live_trade, now)
            if time_stop_msg:
                actions.append(time_stop_msg)
                print(f"\n⏱  TIME STOP: {time_stop_msg}")
                del tracked[key]
                continue

            tp1_msg = self._apply_tp1_if_needed(trade, live_trade, mid_price)
            if tp1_msg:
                actions.append(tp1_msg)
                print(f"\n🎯 TP1 HIT: {tp1_msg}")
                logger.info(tp1_msg)

            trail_msg = self._apply_trailing_stop_if_needed(trade, mid_price)
            if trail_msg:
                actions.append(trail_msg)
                print(f"\n📈 TRAIL: {trail_msg}")
                logger.info(trail_msg)

        self.journal.save_open_trades(tracked)
        return actions

    def _activate_pending_order_if_filled(self, trade: dict, actions: list[str]) -> str | None:
        if trade.get("trade_id") or not trade.get("order_id"):
            return trade.get("trade_id")

        order_id = trade["order_id"]
        filled_id = self._check_order_filled(order_id)
        if not filled_id:
            return None

        self.journal.record_order_fill(trade, order_id, filled_id)
        print(f"\n✅ Limit order {order_id} filled → trade {filled_id}")
        actions.append(f"Order {order_id} filled as trade {filled_id}")
        return filled_id

    def _apply_time_stop_if_needed(
        self,
        trade: dict,
        live_trade: dict,
        now: datetime,
    ) -> str | None:
        open_time_str = trade.get("open_time")
        if not open_time_str:
            return None

        try:
            open_time = datetime.fromisoformat(open_time_str)
        except Exception:
            return None

        hours_open = (now - open_time).total_seconds() / 3600
        max_hours = self.config.get("time_stop_hours", 8)
        if hours_open < max_hours:
            return None

        unrealized = float(live_trade["unrealized_pl"])
        if unrealized >= self._half_r_threshold():
            return None

        trade_id = trade["trade_id"]
        self._close_trade(trade_id)
        self.journal.record_trade_close(trade, "TIME_STOP", unrealized)
        return (
            f"Time stop: closed trade {trade_id} "
            f"after {hours_open:.1f}h | P&L: ${unrealized:.2f}"
        )

    def _half_r_threshold(self) -> float:
        try:
            equity = self.client.get_account_summary()["equity"]
            return -(equity * self.config.get("max_risk_per_trade", 0.01) * 0.5)
        except Exception:
            return -500.0

    def _apply_tp1_if_needed(
        self,
        trade: dict,
        live_trade: dict,
        mid_price: float,
    ) -> str | None:
        if trade.get("tp1_hit"):
            return None

        tp1 = trade.get("tp1", 0)
        if not tp1:
            return None

        direction = trade.get("direction")
        tp1_reached = (direction == "BUY" and mid_price >= tp1) or (
            direction == "SELL" and mid_price <= tp1
        )
        if not tp1_reached:
            return None

        current_units = int(abs(live_trade["units"]))
        close_pct = self.config.get("tp1_close_percent", 0.50)
        close_units = max(1, int(current_units * close_pct))
        entry = float(trade.get("entry_price", 0) or 0)
        partial_pnl = (
            (mid_price - entry) * close_units
            if direction == "BUY"
            else (entry - mid_price) * close_units
        )

        trade_id = trade["trade_id"]
        self._close_partial(trade_id, close_units)
        self._move_sl_to_entry(trade_id, entry)
        self.journal.record_tp1_partial(trade, mid_price, close_units, partial_pnl)

        return (
            f"TP1 hit @ {mid_price:.5f}: "
            f"closed {close_units} units, SL → breakeven {entry}"
        )

    def _apply_trailing_stop_if_needed(self, trade: dict, mid_price: float) -> str | None:
        """
        Trail the stop loss after TP1 has been hit.

        Trail distance = entry-to-TP1 distance (1R equivalent).
        The stop only ever moves in the profitable direction — never back.
        Disabled when tp2_trail = False in config.
        """
        if not trade.get("tp1_hit"):
            return None
        if not self.config.get("tp2_trail", True):
            return None

        direction  = trade.get("direction")
        entry      = float(trade.get("entry_price", 0) or 0)
        tp1        = float(trade.get("tp1", 0) or 0)
        current_sl = float(trade.get("stop_loss", 0) or 0)
        trade_id   = trade.get("trade_id")

        if not entry or not tp1 or not current_sl or not trade_id:
            return None

        trail_distance = abs(tp1 - entry)  # = 1R; stop follows price at this gap

        if direction == "BUY":
            new_sl = round(mid_price - trail_distance, 5)
            if new_sl <= current_sl:           # never move SL down on a BUY
                return None
        elif direction == "SELL":
            new_sl = round(mid_price + trail_distance, 5)
            if new_sl >= current_sl:           # never move SL up on a SELL
                return None
        else:
            return None

        self._move_sl_to_entry(trade_id, new_sl)
        trade["stop_loss"] = new_sl            # persisted when save_open_trades() is called
        self.journal._append_management_event(
            trade,
            {
                "event_type": "TRAILING_STOP",
                "new_stop_loss": new_sl,
                "mid_price": round(mid_price, 5),
                "trail_distance": round(trail_distance, 5),
            },
        )
        return (
            f"Trailing stop → {new_sl:.5f} "
            f"(price {mid_price:.5f}, trail {trail_distance:.5f})"
        )

    # =========================================================================
    # OANDA REST HELPERS
    # =========================================================================

    def _check_order_filled(self, order_id: str) -> str | None:
        """Check if a pending order has been filled. Returns trade_id or None."""
        try:
            url = (
                f"{self.client.base_url}/accounts/{self.client.account_id}"
                f"/orders/{order_id}"
            )
            response = requests.get(url, headers=self.client.headers, timeout=10)
            data = response.json().get("order", {})
            if data.get("state", "") == "FILLED":
                return data.get("tradeOpenedID")
        except Exception as exc:
            logger.error(f"Order status check failed: {exc}")
        return None

    def _close_partial(self, trade_id: str, units: int):
        """Close N units of an open trade."""
        url = (
            f"{self.client.base_url}/accounts/{self.client.account_id}"
            f"/trades/{trade_id}/close"
        )
        response = requests.put(
            url, headers=self.client.headers, json={"units": str(units)}, timeout=10
        )
        if response.status_code != 200:
            logger.error(f"Partial close {trade_id} failed: {response.text}")

    def _move_sl_to_entry(self, trade_id: str, entry_price: float):
        """Move stop loss to entry price (breakeven)."""
        url = (
            f"{self.client.base_url}/accounts/{self.client.account_id}"
            f"/trades/{trade_id}/orders"
        )
        body = {
            "stopLoss": {
                "price": str(round(entry_price, 5)),
                "timeInForce": "GTC",
            }
        }
        response = requests.put(url, headers=self.client.headers, json=body, timeout=10)
        if response.status_code != 200:
            logger.error(f"SL move {trade_id} failed: {response.text}")

    def _close_trade(self, trade_id: str):
        """Close entire trade at market."""
        url = (
            f"{self.client.base_url}/accounts/{self.client.account_id}"
            f"/trades/{trade_id}/close"
        )
        response = requests.put(url, headers=self.client.headers, timeout=10)
        if response.status_code != 200:
            logger.error(f"Trade close {trade_id} failed: {response.text}")
