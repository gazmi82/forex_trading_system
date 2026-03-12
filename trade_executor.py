# =============================================================================
# trade_executor.py — OANDA Order Execution Engine
#
# Handles:
#   - Position sizing (1% risk rule)
#   - Order placement (LIMIT / MARKET / STOP)
#   - TP1 partial close (50%) + SL move to breakeven
#   - Time stop (close if -0.5R after 8 hours)
#   - Trade tracking (open_trades.json + trades.csv)
#
# demo_mode = True  → OANDA PRACTICE account (fake money, real fills/spreads)
# demo_mode = False → OANDA LIVE account (NEVER before March 2027)
# =============================================================================

import json
import logging
import requests
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class TradeExecutor:
    """
    Executes validated signals on OANDA demo account.
    Called from the demo loop after ForexAnalystAgent generates a signal.
    """

    def __init__(self, oanda_client, trading_config: dict, log_dir: Path):
        self.client       = oanda_client
        self.config       = trading_config
        self.log_dir      = Path(log_dir)
        self.demo_mode    = trading_config.get("demo_mode", True)

        self.open_trades_file = self.log_dir / "open_trades.json"
        self.trades_csv       = self.log_dir / "trades.csv"
        self.daily_state_file = self.log_dir / "daily_state.json"
        self.closed_trades_file = self.log_dir / "closed_trades.jsonl"

        # Holds closed-trade records for the feedback loop; drained by demo loop
        self._pending_feedback: list = []

        self._init_logs()

    # =========================================================================
    # INIT
    # =========================================================================

    def _init_logs(self):
        if not self.trades_csv.exists():
            with open(self.trades_csv, "w") as f:
                f.write("timestamp,order_id,trade_id,instrument,direction,"
                        "units,entry_price,stop_loss,tp1,tp2,status,pnl,notes\n")

    def _load_open_trades(self) -> dict:
        if self.open_trades_file.exists():
            with open(self.open_trades_file) as f:
                return json.load(f)
        return {}

    def _save_open_trades(self, trades: dict):
        with open(self.open_trades_file, "w") as f:
            json.dump(trades, f, indent=2)

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
            "executed":    False,
            "reason":      "",
            "order_id":    None,
            "trade_id":    None,
            "units":       0,
            "entry_price": 0.0,
        }

        direction  = signal.get("signal", {}).get("direction", "NEUTRAL")
        confidence = signal.get("signal", {}).get("confidence", 0)

        if direction == "NEUTRAL":
            result["reason"] = "Signal NEUTRAL — no trade"
            return result

        # Fetch live account state
        try:
            account = self.client.get_account_summary()
        except Exception as e:
            result["reason"] = f"Account fetch failed: {e}"
            return result

        # All safety checks
        ok, reason = self._pre_trade_checks(signal, account)
        if not ok:
            result["reason"] = reason
            logger.info(f"Trade blocked: {reason}")
            return result

        # Calculate position size
        units = self._calculate_units(signal, account["equity"])
        if units == 0:
            result["reason"] = "Invalid position size calculated"
            return result

        # Place order
        try:
            order_result = self._place_order(signal, units, direction)
            result.update({
                "executed":    True,
                "order_id":    order_result.get("order_id"),
                "trade_id":    order_result.get("trade_id"),
                "units":       units,
                "entry_price": order_result.get("fill_price",
                               signal["signal"]["entry_zone"][0]),
                "reason":      "Order placed successfully",
            })

            self._track_trade(signal, result)
            self._log_to_csv(signal, result)

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
            logger.info(f"Order placed: {direction} {units} EUR_USD | "
                        f"Entry:{result['entry_price']} SL:{sig['stop_loss']} TP2:{sig['take_profit_2']}")

        except Exception as e:
            result["reason"] = f"Order placement failed: {e}"
            logger.error(f"Order error: {e}")

        return result

    # =========================================================================
    # PRE-TRADE SAFETY CHECKS (all must pass)
    # =========================================================================

    def _pre_trade_checks(self, signal: dict, account: dict) -> tuple:
        """
        Returns (True, "") if all checks pass.
        Returns (False, reason) if any check fails.
        """
        sig        = signal.get("signal", {})
        confidence = sig.get("confidence", 0)
        rr         = sig.get("risk_reward", 0)
        balance    = account["balance"]
        session    = signal.get("session", "")

        # 1. Confidence minimum
        min_conf = self.config.get("min_confidence", 65)
        if confidence < min_conf:
            return False, f"Confidence {confidence}% < {min_conf}% threshold"

        # 2. Risk:Reward minimum
        min_rr = self.config.get("min_rr_ratio", 2.0)
        if rr < min_rr:
            return False, f"R:R {rr} below minimum {min_rr}"

        # 3. Daily loss limit (2%) — uses realized balance delta, not unrealized
        daily_pnl_pct = self._get_daily_pnl_pct(balance)
        max_loss = self.config.get("max_daily_loss", 0.02)
        if daily_pnl_pct <= -(max_loss * 100):
            return False, f"Daily loss limit hit ({daily_pnl_pct:.1f}%) — no more trades today"

        # 4. Max open trades
        if account["open_trade_count"] >= 3:
            return False, f"Already {account['open_trade_count']} open trades — max 3"

        # 5. Only documented kill zones are tradable
        if session not in {"London Kill Zone", "NY Kill Zone", "London Close"}:
            return False, f"Session {session or 'UNKNOWN'} is outside allowed kill zones"

        # 6. No trading after 2 consecutive losses in the same session
        if self._has_session_loss_streak(session):
            return False, f"Two consecutive losses already recorded in {session}"

        # 7. No existing EUR_USD position
        try:
            for t in self.client.get_open_trades():
                if t["instrument"] == "EUR_USD":
                    return False, "EUR_USD position already open"
        except Exception:
            pass

        # 8. Valid entry zone
        entry_zone = sig.get("entry_zone", [0, 0])
        if not entry_zone or entry_zone[0] == 0 or entry_zone[1] == 0:
            return False, "Invalid entry zone"

        # 9. Valid stop loss
        if sig.get("stop_loss", 0) == 0:
            return False, "No stop loss in signal"

        return True, ""

    # =========================================================================
    # POSITION SIZING (1% risk rule)
    # =========================================================================

    def _calculate_units(self, signal: dict, equity: float) -> int:
        """
        units = (equity × 1%) / stop_distance_in_price

        For EUR/USD: P&L = units × price_change
        Example: $1,000 risk / 0.0030 stop = 333,333 units (3.3 lots)
        """
        sig        = signal.get("signal", {})
        entry_zone = sig.get("entry_zone", [0, 0])
        stop_loss  = sig.get("stop_loss", 0)

        if not entry_zone or stop_loss == 0:
            return 0

        entry_price   = (entry_zone[0] + entry_zone[1]) / 2
        stop_distance = abs(entry_price - stop_loss)

        if stop_distance < 0.0005:  # < 5 pips — reject
            logger.warning(f"Stop distance too small: {stop_distance:.5f}")
            return 0

        risk_amount = equity * self.config.get("max_risk_per_trade", 0.01)
        units = int(risk_amount / stop_distance)

        # Safety caps
        units = min(units, 500_000)   # 5 standard lots max
        units = max(units, 1_000)     # 0.01 lot minimum

        logger.info(f"Units: {units:,} | Equity: {equity:.0f} | "
                    f"Risk: ${risk_amount:.0f} | Stop: {stop_distance:.5f}")
        return units

    # =========================================================================
    # ORDER PLACEMENT
    # =========================================================================

    def _place_order(self, signal: dict, units: int, direction: str) -> dict:
        """
        POST order to OANDA REST API.
        BUY = positive units, SELL = negative units.
        Uses TP2 as main take profit (TP1 handled by monitor).
        """
        sig        = signal.get("signal", {})
        entry_zone = sig.get("entry_zone", [0, 0])
        stop_loss  = sig.get("stop_loss", 0)
        tp2        = sig.get("take_profit_2", 0)
        order_type = sig.get("order_type", "LIMIT")

        entry_price  = round((entry_zone[0] + entry_zone[1]) / 2, 5)
        signed_units = units if direction == "BUY" else -units

        # Base order body (all order types share these)
        order = {
            "instrument": "EUR_USD",
            "units":      str(signed_units),
            "stopLossOnFill": {
                "price":       str(round(stop_loss, 5)),
                "timeInForce": "GTC"
            },
            "takeProfitOnFill": {
                "price":       str(round(tp2, 5)),
                "timeInForce": "GTC"
            },
        }

        if order_type in ("LIMIT", "STOP_LIMIT"):
            # STOP_LIMIT treated as LIMIT — fills at entry_price or better
            order["type"]        = "LIMIT"
            order["price"]       = str(entry_price)
            order["timeInForce"] = "GTC"
        elif order_type == "STOP":
            order["type"]        = "STOP"
            order["price"]       = str(entry_price)
            order["timeInForce"] = "GTC"
        else:  # MARKET
            order["type"] = "MARKET"

        url      = f"{self.client.base_url}/accounts/{self.client.account_id}/orders"
        response = requests.post(url, headers=self.client.headers,
                                 json={"order": order}, timeout=10)
        data     = response.json()

        if response.status_code not in (200, 201):
            raise Exception(f"OANDA {response.status_code}: {data.get('errorMessage', data)}")

        result = {"order_id": None, "trade_id": None, "fill_price": entry_price}

        # LIMIT/STOP: pending order
        if "orderCreateTransaction" in data:
            result["order_id"] = data["orderCreateTransaction"]["id"]
            logger.info(f"Limit order queued: ID {result['order_id']} @ {entry_price}")

        # MARKET: filled immediately
        if "orderFillTransaction" in data:
            fill = data["orderFillTransaction"]
            result["trade_id"]  = fill["tradeOpened"]["tradeID"]
            result["fill_price"] = float(fill["price"])
            logger.info(f"Market order filled: trade {result['trade_id']} @ {result['fill_price']}")

        return result

    # =========================================================================
    # TRADE MONITORING (called every 30 min from demo loop)
    # =========================================================================

    def monitor_open_trades(self) -> list:
        """
        Check all tracked trades for:
        - TP1 hit → close 50%, move SL to breakeven
        - Time stop → close if -0.5R after 8 hours
        - Already closed by OANDA (SL or TP2 hit)
        """
        actions = []
        tracked = self._load_open_trades()

        if not tracked:
            return actions

        try:
            price_data  = self.client.get_current_price("EUR_USD")
            mid_price   = price_data["mid"]
            live_trades = {t["id"]: t for t in self.client.get_open_trades()}
        except Exception as e:
            logger.error(f"Monitor fetch error: {e}")
            return actions

        now = datetime.now(timezone.utc)

        for key, trade in list(tracked.items()):
            trade_id = trade.get("trade_id")

            # Check if OANDA already closed it (SL or TP2 hit)
            if trade_id and trade_id not in live_trades:
                msg = f"Trade {trade_id} closed by OANDA (SL/TP2 hit)"
                actions.append(msg)
                print(f"\n📋 {msg}")
                self._log_trade_close(trade, "CLOSED_BY_OANDA")
                del tracked[key]
                continue

            # Check pending LIMIT orders that filled
            if not trade_id and trade.get("order_id"):
                order_id = trade.get("order_id")
                filled_id = self._check_order_filled(order_id)
                if filled_id:
                    trade["trade_id"] = filled_id
                    trade_id = filled_id
                    # Reset open_time to fill time so time-stop clock is correct
                    trade["open_time"] = datetime.now(timezone.utc).isoformat()
                    print(f"\n✅ Limit order {order_id} filled → trade {trade_id}")
                    actions.append(f"Order {order_id} filled as trade {trade_id}")

            if not trade_id or trade_id not in live_trades:
                continue

            direction = trade.get("direction")
            tp1       = trade.get("tp1", 0)
            entry     = trade.get("entry_price", 0)
            tp1_hit   = trade.get("tp1_hit", False)
            open_time_str = trade.get("open_time")

            live = live_trades[trade_id]

            # --- TIME STOP ---
            if open_time_str:
                open_time  = datetime.fromisoformat(open_time_str)
                hours_open = (now - open_time).total_seconds() / 3600
                unrealized = live["unrealized_pl"]

                try:
                    eq = self.client.get_account_summary()["equity"]
                    half_r = -(eq * self.config.get("max_risk_per_trade", 0.01) * 0.5)
                except Exception:
                    half_r = -500

                max_h = self.config.get("time_stop_hours", 8)
                if hours_open >= max_h and unrealized < half_r:
                    self._close_trade(trade_id)
                    msg = (f"Time stop: closed trade {trade_id} "
                           f"after {hours_open:.1f}h | P&L: ${unrealized:.2f}")
                    actions.append(msg)
                    print(f"\n⏱  TIME STOP: {msg}")
                    self._log_trade_close(trade, "TIME_STOP", unrealized)
                    del tracked[key]
                    continue

            # --- TP1 CHECK ---
            if not tp1_hit and tp1 > 0:
                tp1_reached = (direction == "BUY"  and mid_price >= tp1) or \
                              (direction == "SELL" and mid_price <= tp1)

                if tp1_reached:
                    current_units = int(abs(live["units"]))
                    close_units   = max(1, current_units // 2)

                    self._close_partial(trade_id, close_units)
                    self._move_sl_to_entry(trade_id, entry)

                    trade["tp1_hit"] = True
                    msg = (f"TP1 hit @ {mid_price:.5f}: "
                           f"closed {close_units} units, SL → breakeven {entry}")
                    actions.append(msg)
                    print(f"\n🎯 TP1 HIT: {msg}")
                    logger.info(msg)

        self._save_open_trades(tracked)
        return actions

    # =========================================================================
    # OANDA REST HELPERS
    # =========================================================================

    def _check_order_filled(self, order_id: str) -> str | None:
        """Check if a pending order has been filled. Returns trade_id or None."""
        try:
            url      = (f"{self.client.base_url}/accounts/{self.client.account_id}"
                        f"/orders/{order_id}")
            response = requests.get(url, headers=self.client.headers, timeout=10)
            data     = response.json().get("order", {})
            state    = data.get("state", "")
            if state == "FILLED":
                return data.get("tradeOpenedID")
        except Exception as e:
            logger.error(f"Order status check failed: {e}")
        return None

    def _close_partial(self, trade_id: str, units: int):
        """Close N units of an open trade."""
        url      = (f"{self.client.base_url}/accounts/{self.client.account_id}"
                    f"/trades/{trade_id}/close")
        response = requests.put(url, headers=self.client.headers,
                                json={"units": str(units)}, timeout=10)
        if response.status_code != 200:
            logger.error(f"Partial close {trade_id} failed: {response.text}")

    def _move_sl_to_entry(self, trade_id: str, entry_price: float):
        """Move stop loss to entry price (breakeven)."""
        url      = (f"{self.client.base_url}/accounts/{self.client.account_id}"
                    f"/trades/{trade_id}/orders")
        body     = {"stopLoss": {"price": str(round(entry_price, 5)),
                                 "timeInForce": "GTC"}}
        response = requests.put(url, headers=self.client.headers,
                                json=body, timeout=10)
        if response.status_code != 200:
            logger.error(f"SL move {trade_id} failed: {response.text}")

    def _close_trade(self, trade_id: str):
        """Close entire trade at market."""
        url      = (f"{self.client.base_url}/accounts/{self.client.account_id}"
                    f"/trades/{trade_id}/close")
        response = requests.put(url, headers=self.client.headers, timeout=10)
        if response.status_code != 200:
            logger.error(f"Trade close {trade_id} failed: {response.text}")

    # =========================================================================
    # LOGGING & TRACKING
    # =========================================================================

    def _track_trade(self, signal: dict, result: dict):
        """Save new trade to open_trades.json."""
        tracked  = self._load_open_trades()
        sig      = signal.get("signal", {})
        key      = f"trade_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"

        tracked[key] = {
            "order_id":    result.get("order_id"),
            "trade_id":    result.get("trade_id"),
            "instrument":  "EUR_USD",
            "direction":   sig.get("direction"),
            "units":       result.get("units"),
            "entry_price": result.get("entry_price"),
            "stop_loss":   sig.get("stop_loss"),
            "tp1":         sig.get("take_profit_1"),
            "tp2":         sig.get("take_profit_2"),
            "risk_reward": sig.get("risk_reward"),
            "tp1_hit":     False,
            "open_time":   datetime.now(timezone.utc).isoformat(),
            "confluence":  signal.get("confluence_score"),
            "confidence":  sig.get("confidence"),
            "session":     signal.get("session", ""),
        }
        self._save_open_trades(tracked)

    def _log_to_csv(self, signal: dict, result: dict):
        """Append trade entry to trades.csv."""
        sig = signal.get("signal", {})
        row = ",".join(str(x) for x in [
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
        ])
        with open(self.trades_csv, "a") as f:
            f.write(row + "\n")

    def drain_closed_trades(self) -> list:
        """
        Returns and clears trade records closed since last call.
        The demo loop calls this after monitor_open_trades() to feed
        outcomes into agent.record_trade_outcome().
        """
        closed = self._pending_feedback[:]
        self._pending_feedback = []
        return closed

    def _get_daily_pnl_pct(self, current_balance: float) -> float:
        """
        Returns today's realized P&L as a percentage of the day's starting balance.
        Persists the daily start balance in daily_state.json so it survives
        the time.sleep(1800) calls but resets at midnight UTC.
        """
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

    def _log_trade_close(self, trade: dict, reason: str, pnl: float = 0):
        """Append trade close to trades.csv and queue for feedback loop."""
        duration_hours = ""
        open_time = trade.get("open_time")
        if open_time:
            try:
                opened_at = datetime.fromisoformat(open_time)
                duration_hours = round(
                    (datetime.now(timezone.utc) - opened_at).total_seconds() / 3600, 2
                )
            except Exception:
                duration_hours = ""

        units = abs(float(trade.get("units", 0) or 0))
        stop_distance = abs(float(trade.get("entry_price", 0) or 0) - float(trade.get("stop_loss", 0) or 0))
        risk_amount = max(stop_distance * units, 0.0001)

        # Queue for agent.record_trade_outcome() feedback loop
        self._pending_feedback.append({
            "date":             datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "pair":             trade.get("instrument", "EUR_USD"),
            "direction":        trade.get("direction", ""),
            "entry_price":      trade.get("entry_price", 0),
            "stop_loss":        trade.get("stop_loss", 0),
            "take_profit":      trade.get("tp2", 0),
            "lot_size":         trade.get("units", 0),
            "outcome":          "WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "BREAKEVEN"),
            "pnl_r":            round(pnl / risk_amount, 2),
            "pnl_usd":          round(pnl, 2),
            "duration_hours":   duration_hours,
            "session":          trade.get("session", ""),
            "confluence_score": trade.get("confluence", 0),
            "close_reason":     reason,
        })
        with open(self.closed_trades_file, "a") as f:
            f.write(json.dumps(self._pending_feedback[-1]) + "\n")
        row = ",".join(str(x) for x in [
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
            round(pnl, 2),
            f"Session:{trade.get('session', '')}",
        ])
        with open(self.trades_csv, "a") as f:
            f.write(row + "\n")

    def _has_session_loss_streak(self, session: str, limit: int = 2) -> bool:
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
