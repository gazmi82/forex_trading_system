# =============================================================================
# capital_connector.py — Live Capital.com Data Feed
# Fetches real EUR/USD prices, candles, and calculates all indicators
#
# Setup:
#   1. Create Capital.com demo account at capital.com
#   2. Enable 2FA in Settings (required before API key generation)
#   3. Generate API key: Settings → API integrations → Add new key
#   4. Set environment variables:
#      export CAPITAL_API_KEY="your-api-key"
#      export CAPITAL_IDENTIFIER="your-email@example.com"
#      export CAPITAL_PASSWORD="your-password"
#
# Install: pip install pandas requests numpy
# =============================================================================

import os
import time
import logging
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

DEMO_BASE_URL = "https://demo-api-capital.backend-capital.com/api/v1"
LIVE_BASE_URL  = "https://api-capital.backend-capital.com/api/v1"


# =============================================================================
# CAPITAL.COM CLIENT
# =============================================================================

class CapitalClient:
    """
    Connects to Capital.com REST API v1.
    Auth: POST /session with X-CAP-API-KEY header + email/password body.
    Returns CST + X-SECURITY-TOKEN session tokens (valid 10 min inactivity).
    Always use demo=True during your 12-month demo period.
    """

    def __init__(self, api_key: str, identifier: str, password: str, demo: bool = True):
        self.api_key    = api_key
        self.identifier = identifier
        self.password   = password
        self.base_url   = DEMO_BASE_URL if demo else LIVE_BASE_URL
        self.demo       = demo

        self.cst            = None
        self.security_token = None
        self._session_time  = 0.0

        self._create_session()

    def _create_session(self):
        """Authenticate and obtain CST + X-SECURITY-TOKEN session tokens."""
        url = f"{self.base_url}/session"
        headers = {
            "X-CAP-API-KEY": self.api_key,
            "Content-Type":  "application/json"
        }
        body = {
            "identifier":        self.identifier,
            "password":          self.password,
            "encryptedPassword": False
        }
        response = requests.post(url, headers=headers, json=body)
        if response.status_code != 200:
            hint = ""
            if response.status_code == 401:
                hint = (
                    "\n   HINT: CAPITAL_PASSWORD must be the API key password "
                    "(set when creating the key in Settings → API integrations), "
                    "NOT your Capital.com account login password. "
                    "Also ensure 2FA is enabled on your account."
                )
            raise ConnectionError(
                f"Capital.com login failed [{response.status_code}]: {response.text}{hint}"
            )

        self.cst            = response.headers.get("CST")
        self.security_token = response.headers.get("X-SECURITY-TOKEN")
        self._session_time  = time.time()

        data    = response.json()
        account = data.get("accountInfo", {})
        balance = account.get("balance", data.get("balance", "N/A"))
        acct_id = data.get("currentAccountId", "")
        print(f"✅ Capital.com Connected | Account: {acct_id} | Balance: {balance}")
        logger.info(f"Capital.com session created. Balance: {balance}")

    def _ensure_session(self):
        """Re-authenticate if session is older than 9 minutes (expires at 10 min)."""
        if time.time() - self._session_time > 540:
            logger.info("Session expiring — refreshing...")
            self._create_session()

    def _headers(self) -> dict:
        self._ensure_session()
        return {
            "X-SECURITY-TOKEN": self.security_token,
            "CST":              self.cst,
            "Content-Type":     "application/json"
        }

    def get_current_price(self, epic: str = "EURUSD") -> dict:
        """
        Get current bid/ask price snapshot.
        Returns: {instrument, bid, ask, mid, spread_pips, timestamp, tradeable}
        """
        url = f"{self.base_url}/prices/{epic}"
        response = requests.get(url, headers=self._headers())
        if response.status_code != 200:
            raise ValueError(f"Price snapshot failed [{response.status_code}]: {response.text}")

        data = response.json()

        # Handle both possible response formats
        if "prices" in data and data["prices"]:
            # Historical format with no resolution — take last closePrice
            last = data["prices"][-1]
            cp   = last["closePrice"]
            bid  = float(cp["bid"])
            ask  = float(cp.get("ask", cp.get("ofr", bid)))
            ts   = last.get("snapshotTimeUTC", datetime.now(timezone.utc).isoformat())
        else:
            # Live tick format: {"epic": ..., "bid": ..., "ofr": ..., "timestamp": ...}
            bid = float(data["bid"])
            ask = float(data.get("ofr", data.get("ask", data["bid"])))
            ts  = str(data.get("timestamp", datetime.now(timezone.utc).isoformat()))

        mid    = round((bid + ask) / 2, 5)
        spread = round((ask - bid) * 10000, 1)

        return {
            "instrument":  epic,
            "bid":         bid,
            "ask":         ask,
            "mid":         mid,
            "spread_pips": spread,
            "timestamp":   ts,
            "tradeable":   True
        }

    def get_candles(
        self,
        epic:       str = "EURUSD",
        resolution: str = "HOUR_4",
        max:        int = 200
    ) -> pd.DataFrame:
        """
        Fetch OHLCV candlestick data.

        Resolution options:
            MINUTE, MINUTE_5, MINUTE_15, MINUTE_30
            HOUR, HOUR_4
            DAY, WEEK

        Returns: DataFrame with columns [open, high, low, close, volume]
                 indexed by UTC datetime.
        """
        url    = f"{self.base_url}/prices/{epic}"
        params = {"resolution": resolution, "max": max}

        response = requests.get(url, headers=self._headers(), params=params)
        data     = response.json()

        if "prices" not in data:
            raise ValueError(f"No candle data for {epic} {resolution}: {data}")

        def _mid(price_obj: dict) -> float:
            bid = float(price_obj["bid"])
            ask = float(price_obj.get("ask", price_obj.get("ofr", bid)))
            return round((bid + ask) / 2, 5)

        candles = []
        for p in data["prices"]:
            try:
                raw_time = p.get("snapshotTimeUTC", p.get("snapshotTime", ""))
                ts = datetime.fromisoformat(raw_time[:19])
                candles.append({
                    "time":   ts,
                    "open":   _mid(p["openPrice"]),
                    "high":   _mid(p["highPrice"]),
                    "low":    _mid(p["lowPrice"]),
                    "close":  _mid(p["closePrice"]),
                    "volume": int(p.get("lastTradedVolume", 0))
                })
            except (KeyError, ValueError) as e:
                logger.warning(f"Skipping candle: {e}")

        if not candles:
            raise ValueError(f"No valid candles parsed for {epic} {resolution}")

        df = pd.DataFrame(candles)
        df["time"] = pd.to_datetime(df["time"])
        df = df.set_index("time").sort_index()
        return df

    def get_account_summary(self) -> dict:
        """Get current account balance, margin, and unrealized P&L."""
        url      = f"{self.base_url}/accounts"
        response = requests.get(url, headers=self._headers())
        data     = response.json()
        accounts = data.get("accounts", [])

        account = next(
            (a for a in accounts if a.get("preferred")),
            accounts[0] if accounts else {}
        )

        bal           = account.get("balance", {})
        balance       = float(bal.get("balance",    0))
        unrealized_pl = float(bal.get("profitLoss", 0))
        equity        = balance + unrealized_pl
        margin_used   = float(bal.get("deposit",    0))
        margin_avail  = float(bal.get("available",  balance))

        return {
            "balance":          balance,
            "equity":           equity,
            "unrealized_pnl":   unrealized_pl,
            "margin_used":      margin_used,
            "margin_available": margin_avail,
            "open_trade_count": 0,  # filled separately by get_open_trades
            "currency":         account.get("currency", "USD")
        }

    def get_open_trades(self) -> list:
        """Get all currently open positions."""
        url      = f"{self.base_url}/positions"
        response = requests.get(url, headers=self._headers())
        data     = response.json()

        trades = []
        for pos in data.get("positions", []):
            p = pos.get("position", {})
            m = pos.get("market",   {})

            direction  = p.get("direction", "BUY")
            size       = float(p.get("size", 0))
            units      = size if direction == "BUY" else -size
            open_price = float(p.get("level", 0))

            # Estimate unrealized P&L from current market price in the position snapshot
            if direction == "BUY":
                current    = float(m.get("bid",   open_price))
                unrealized = (current - open_price) * abs(units)
            else:
                current    = float(m.get("offer", m.get("ofr", open_price)))
                unrealized = (open_price - current) * abs(units)

            trades.append({
                "id":            p.get("dealId",        ""),
                "instrument":    m.get("epic",          "EURUSD"),
                "units":         units,
                "open_price":    open_price,
                "unrealized_pl": round(unrealized, 2),
                "open_time":     p.get("createdDateUTC", "")
            })

        return trades


# =============================================================================
# INDICATOR CALCULATOR
# =============================================================================

class IndicatorCalculator:
    """
    Calculates all technical indicators needed by the Forex Analyst agent.
    Pure DataFrame math — no external API dependency.
    """

    @staticmethod
    def calculate_all(df_4h: pd.DataFrame, df_1h: pd.DataFrame, df_daily: pd.DataFrame) -> dict:
        indicators = {}

        indicators["ema20_4h"]      = IndicatorCalculator._ema(df_4h, 20)
        indicators["ema50_4h"]      = IndicatorCalculator._ema(df_4h, 50)
        indicators["ema200_daily"]  = IndicatorCalculator._ema(df_daily, 200)
        indicators["rsi_4h"]        = IndicatorCalculator._rsi(df_4h, 14)
        indicators["rsi_1h"]        = IndicatorCalculator._rsi(df_1h, 14)
        indicators["adx_4h"]        = IndicatorCalculator._adx(df_4h, 14)
        indicators["atr_4h"]        = IndicatorCalculator._atr(df_4h, 14)
        indicators["market_regime"]  = IndicatorCalculator._regime(
            indicators["adx_4h"], indicators["atr_4h"], df_4h["close"].iloc[-1]
        )
        indicators["resistance_levels"]      = IndicatorCalculator._resistance(df_daily)
        indicators["support_levels"]         = IndicatorCalculator._support(df_daily)
        indicators["round_numbers"]          = IndicatorCalculator._round_numbers(df_4h["close"].iloc[-1])
        indicators["premium_discount_zone"]  = IndicatorCalculator._premium_discount(df_4h)
        indicators["bullish_ob"]             = IndicatorCalculator._find_order_block(df_4h, "bullish")
        indicators["bearish_ob"]             = IndicatorCalculator._find_order_block(df_4h, "bearish")
        indicators["bullish_fvg"]            = IndicatorCalculator._find_fvg(df_1h, "bullish")
        indicators["bearish_fvg"]            = IndicatorCalculator._find_fvg(df_1h, "bearish")
        indicators["recent_liquidity_sweep"] = IndicatorCalculator._find_liquidity_sweep(df_1h)

        return indicators

    @staticmethod
    def _ema(df: pd.DataFrame, period: int) -> float:
        ema = df["close"].ewm(span=period, adjust=False).mean()
        return round(ema.iloc[-1], 5)

    @staticmethod
    def _rsi(df: pd.DataFrame, period: int = 14) -> float:
        delta = df["close"].diff()
        gain  = delta.clip(lower=0).rolling(period).mean()
        loss  = (-delta.clip(upper=0)).rolling(period).mean()
        rs    = gain / loss
        rsi   = 100 - (100 / (1 + rs))
        return round(rsi.iloc[-1], 2)

    @staticmethod
    def _adx(df: pd.DataFrame, period: int = 14) -> float:
        high, low, close = df["high"], df["low"], df["close"]
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low  - close.shift()).abs()
        ], axis=1).max(axis=1)

        dm_plus  = (high.diff()).clip(lower=0)
        dm_minus = (-low.diff()).clip(lower=0)

        tr_smooth  = tr.rolling(period).mean()
        dmp_smooth = dm_plus.rolling(period).mean()
        dmm_smooth = dm_minus.rolling(period).mean()

        di_plus  = 100 * dmp_smooth / tr_smooth
        di_minus = 100 * dmm_smooth / tr_smooth
        dx  = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus)
        adx = dx.rolling(period).mean()
        return round(adx.iloc[-1], 2)

    @staticmethod
    def _atr(df: pd.DataFrame, period: int = 14) -> float:
        high, low, close = df["high"], df["low"], df["close"]
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low  - close.shift()).abs()
        ], axis=1).max(axis=1)
        atr = tr.rolling(period).mean()
        return round(atr.iloc[-1], 5)

    @staticmethod
    def _regime(adx: float, atr: float, price: float) -> str:
        atr_pct = (atr / price) * 100
        if adx > 25 and atr_pct > 0.3:
            return "HIGH_VOLATILITY" if atr_pct > 0.6 else "TRENDING"
        return "RANGING"

    @staticmethod
    def _resistance(df: pd.DataFrame, lookback: int = 50) -> list:
        highs  = df["high"].tail(lookback)
        levels = []
        for i in range(2, len(highs) - 2):
            if highs.iloc[i] == highs.iloc[i-2:i+3].max():
                levels.append(round(highs.iloc[i], 4))
        return sorted(set(levels), reverse=True)[:3]

    @staticmethod
    def _support(df: pd.DataFrame, lookback: int = 50) -> list:
        lows   = df["low"].tail(lookback)
        levels = []
        for i in range(2, len(lows) - 2):
            if lows.iloc[i] == lows.iloc[i-2:i+3].min():
                levels.append(round(lows.iloc[i], 4))
        return sorted(set(levels))[:3]

    @staticmethod
    def _round_numbers(price: float) -> list:
        base = round(price, 2)
        return [round(base - 0.01, 2), round(base, 2), round(base + 0.01, 2)]

    @staticmethod
    def _premium_discount(df: pd.DataFrame, lookback: int = 20) -> str:
        recent     = df.tail(lookback)
        swing_high = recent["high"].max()
        swing_low  = recent["low"].min()
        current    = recent["close"].iloc[-1]
        equilibrium = (swing_high + swing_low) / 2

        if current > equilibrium * 1.002:
            pct = round((current - swing_low) / (swing_high - swing_low) * 100)
            return f"PREMIUM ({pct}% of range)"
        elif current < equilibrium * 0.998:
            pct = round((current - swing_low) / (swing_high - swing_low) * 100)
            return f"DISCOUNT ({pct}% of range)"
        return "EQUILIBRIUM (50% of range)"

    @staticmethod
    def _find_order_block(df: pd.DataFrame, direction: str) -> str:
        lookback = df.tail(50)
        if direction == "bullish":
            for i in range(len(lookback) - 3, 2, -1):
                candle  = lookback.iloc[i]
                next_3  = lookback.iloc[i+1:i+4]
                is_bear = candle["close"] < candle["open"]
                strong  = (next_3["close"].max() - candle["low"]) > (candle["high"] - candle["low"]) * 2
                if is_bear and strong:
                    return f"{round(candle['low'], 5)}–{round(candle['high'], 5)} (4H, valid)"
        elif direction == "bearish":
            for i in range(len(lookback) - 3, 2, -1):
                candle  = lookback.iloc[i]
                next_3  = lookback.iloc[i+1:i+4]
                is_bull = candle["close"] > candle["open"]
                strong  = (candle["high"] - next_3["close"].min()) > (candle["high"] - candle["low"]) * 2
                if is_bull and strong:
                    return f"{round(candle['low'], 5)}–{round(candle['high'], 5)} (4H, valid)"
        return "None identified in last 50 candles"

    @staticmethod
    def _find_fvg(df: pd.DataFrame, direction: str) -> str:
        lookback = df.tail(30)
        for i in range(len(lookback) - 3, 1, -1):
            c1 = lookback.iloc[i-1]
            c3 = lookback.iloc[i+1]
            if direction == "bullish" and c1["high"] < c3["low"]:
                return f"{round(c1['high'], 5)}–{round(c3['low'], 5)} (1H, unfilled)"
            if direction == "bearish" and c1["low"] > c3["high"]:
                return f"{round(c3['high'], 5)}–{round(c1['low'], 5)} (1H, unfilled)"
        return "None identified"

    @staticmethod
    def _find_liquidity_sweep(df: pd.DataFrame) -> str:
        recent = df.tail(10)
        for i in range(len(recent) - 1, 0, -1):
            candle    = recent.iloc[i]
            prev_low  = recent.iloc[:i]["low"].min()
            prev_high = recent.iloc[:i]["high"].max()
            hours_ago = len(recent) - 1 - i
            if candle["low"] < prev_low and candle["close"] > prev_low:
                return f"SSL swept at {round(prev_low, 5)} ({hours_ago}h ago, strong rejection)"
            if candle["high"] > prev_high and candle["close"] < prev_high:
                return f"BSL swept at {round(prev_high, 5)} ({hours_ago}h ago, strong rejection)"
        return "No recent sweep identified"


# =============================================================================
# MARKET STRUCTURE ANALYZER
# =============================================================================

class MarketStructureAnalyzer:
    """Identifies trend structure across multiple timeframes."""

    @staticmethod
    def analyze(df: pd.DataFrame, timeframe: str) -> dict:
        if len(df) < 10:
            return {"trend": "NEUTRAL", "structure": "Insufficient data"}

        highs = df["high"]
        lows  = df["low"]

        recent_highs = highs.tail(20)
        recent_lows  = lows.tail(20)

        hh = recent_highs.iloc[-1] > recent_highs.iloc[-10]
        hl = recent_lows.iloc[-1]  > recent_lows.iloc[-10]
        lh = recent_highs.iloc[-1] < recent_highs.iloc[-10]
        ll = recent_lows.iloc[-1]  < recent_lows.iloc[-10]

        if hh and hl:
            return {"trend": "BULLISH", "structure": f"HH + HL — Bullish {timeframe} structure"}
        elif lh and ll:
            return {"trend": "BEARISH", "structure": f"LH + LL — Bearish {timeframe} structure"}
        elif hh and ll:
            return {"trend": "NEUTRAL", "structure": f"Mixed — Expanding range {timeframe}"}
        return {"trend": "NEUTRAL", "structure": f"Ranging — Equal highs/lows {timeframe}"}


# =============================================================================
# MARKET DATA BUILDER
# =============================================================================

class MarketDataBuilder:
    """
    Orchestrates all data fetching and indicator calculation.
    Produces the complete market_data dict ready for the Forex Analyst agent.
    """

    # Capital.com resolution names
    RES_4H     = "HOUR_4"
    RES_1H     = "HOUR"
    RES_DAILY  = "DAY"
    RES_WEEKLY = "WEEK"

    def __init__(self, client: CapitalClient):
        self.client     = client
        self.calculator = IndicatorCalculator()
        self.structure  = MarketStructureAnalyzer()

    def build_market_data(
        self,
        epic:           str   = "EURUSD",
        account_equity: float = None
    ) -> dict:
        """
        Fetches all live data and builds the complete market_data dict.
        Drop-in replacement for get_example_market_data() in main.py.
        """
        print(f"\n📡 Fetching live data for {epic}...")

        print("  Fetching 4H candles...")
        df_4h    = self.client.get_candles(epic, self.RES_4H,    max=200)

        print("  Fetching 1H candles...")
        df_1h    = self.client.get_candles(epic, self.RES_1H,    max=200)

        print("  Fetching Daily candles...")
        df_daily = self.client.get_candles(epic, self.RES_DAILY, max=200)

        print("  Fetching Weekly candles...")
        df_weekly = self.client.get_candles(epic, self.RES_WEEKLY, max=52)

        print("  Fetching current price...")
        price_data    = self.client.get_current_price(epic)
        current_price = price_data["mid"]
        print(f"  Current price: {current_price} (spread: {price_data['spread_pips']} pips)")

        print("  Calculating indicators...")
        indicators = IndicatorCalculator.calculate_all(df_4h, df_1h, df_daily)

        weekly_struct = self.structure.analyze(df_weekly, "Weekly")
        daily_struct  = self.structure.analyze(df_daily,  "Daily")
        h4_struct     = self.structure.analyze(df_4h,     "4H")
        h1_struct     = self.structure.analyze(df_1h,     "1H")

        ohlcv = {
            "day_open":          round(df_daily["open"].iloc[-1],   5),
            "week_open":         round(df_weekly["open"].iloc[-1],  5),
            "month_open":        round(df_daily["open"].iloc[-22],  5) if len(df_daily) > 22 else None,
            "prev_day_high":     round(df_daily["high"].iloc[-2],   5),
            "prev_day_low":      round(df_daily["low"].iloc[-2],    5),
            "prev_week_high":    round(df_weekly["high"].iloc[-2],  5),
            "prev_week_low":     round(df_weekly["low"].iloc[-2],   5),
            "weekly_structure":  weekly_struct["structure"],
            "daily_structure":   daily_struct["structure"],
            "h4_structure":      h4_struct["structure"],
            "h1_structure":      h1_struct["structure"],
            "weekly_trend":      weekly_struct["trend"],
            "daily_trend":       daily_struct["trend"],
            "h4_trend":          h4_struct["trend"],
            "h1_trend":          h1_struct["trend"],
        }

        session_info = self._get_session_info()
        fundamental  = self._get_fundamental_data(session_info)

        account     = self.client.get_account_summary()
        open_trades = self.client.get_open_trades()
        equity      = account_equity or account["equity"]

        open_risk_pct = 0.0
        if open_trades:
            open_risk_pct = round(
                abs(sum(t["unrealized_pl"] for t in open_trades)) / equity * 100, 2
            )

        portfolio = {
            "equity":          equity,
            "open_trades":     len(open_trades),
            "open_risk_pct":   open_risk_pct,
            "daily_pnl_pct":   round(account["unrealized_pnl"] / equity * 100, 2) if equity else 0,
            "trades_today":    len(open_trades),
            "usd_exposure":    self._calculate_usd_exposure(open_trades),
            "margin_used_pct": round(account["margin_used"] / equity * 100, 2) if equity else 0,
        }

        print(f"  ✅ Live data ready — Account: ${equity:,.2f}")

        return {
            "pair":       "EUR/USD",
            "price":      current_price,
            "spread":     price_data["spread_pips"],
            "demo_mode":  True,
            "ohlcv":      ohlcv,
            "indicators": indicators,
            "fundamental":fundamental,
            "portfolio":  portfolio,
            "fetch_time": datetime.now(timezone.utc).isoformat(),
        }

    def _get_session_info(self) -> dict:
        """Determine current trading session based on UTC time."""
        now_utc  = datetime.now(timezone.utc)
        hour_est = (now_utc.hour - 5) % 24  # EST = UTC - 5

        if 3 <= hour_est < 4:
            session   = "London Kill Zone"
            kill_zone = "YES — London Kill Zone (3-4 AM EST)"
        elif 8 <= hour_est < 10:
            session   = "NY Kill Zone"
            kill_zone = "YES — NY Kill Zone (8-10 AM EST)"
        elif 10 <= hour_est < 12:
            session   = "London Close"
            kill_zone = "YES — London Close (10 AM-12 PM EST)"
        elif 3 <= hour_est < 12:
            session   = "London/NY Overlap"
            kill_zone = "Active session — not peak Kill Zone"
        elif 20 <= hour_est or hour_est < 3:
            session   = "Asian Session"
            kill_zone = "NO — Asian session (observe only)"
        else:
            session   = "Low Liquidity"
            kill_zone = "NO — Avoid trading (12PM-8PM EST)"

        return {"active_session": session, "kill_zone_active": kill_zone, "hour_est": hour_est}

    def _get_fundamental_data(self, session_info: dict) -> dict:
        """
        Returns fundamental context.
        Update USD/EUR interest rates manually each week.
        """
        return {
            "usd_rate":          4.50,
            "pair_rate":         3.65,
            "rate_differential": "+0.85% USD favor",
            "dxy_direction":     "Check DXY chart manually",
            "dxy_level":         "Check live",
            "cot_net":           "Check cftc.gov Friday 3:30PM EST",
            "cot_bias":          "Check latest COT report",
            "retail_sentiment":  "Check myfxbook.com/community/outlook",
            "next_event_name":   "Check forexfactory.com calendar",
            "time_to_event":     "Check forexfactory.com",
            "recent_headline":   "Check reuters.com/markets/currencies",
            "active_session":    session_info["active_session"],
            "kill_zone_active":  session_info["kill_zone_active"],
        }

    def _calculate_usd_exposure(self, open_trades: list) -> str:
        if not open_trades:
            return "NONE"
        usd_pairs = [t for t in open_trades if "USD" in t["instrument"]]
        if not usd_pairs:
            return "NONE"
        total_units = sum(abs(t["units"]) for t in usd_pairs)
        return f"{len(usd_pairs)} trades, {total_units:,.0f} units"


# =============================================================================
# HELPER — DROP-IN REPLACEMENT FOR main.py
# =============================================================================

def create_live_market_data_function():
    """
    Returns a function that fetches live data from Capital.com.
    Drop-in replacement for get_example_market_data() in main.py.
    """
    api_key    = os.getenv("CAPITAL_API_KEY")
    identifier = os.getenv("CAPITAL_IDENTIFIER")
    password   = os.getenv("CAPITAL_PASSWORD")

    if not api_key or not identifier or not password:
        print("⚠️  Capital.com credentials not set.")
        print("   Set them with:")
        print("   export CAPITAL_API_KEY='your-api-key'")
        print("   export CAPITAL_IDENTIFIER='your-email@example.com'")
        print("   export CAPITAL_PASSWORD='your-password'")
        return None

    try:
        client  = CapitalClient(api_key, identifier, password, demo=True)
        builder = MarketDataBuilder(client)
        return lambda: builder.build_market_data("EURUSD")
    except Exception as e:
        print(f"❌ Capital.com connection failed: {e}")
        return None


# =============================================================================
# STANDALONE TEST
# =============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("CAPITAL.COM LIVE DATA CONNECTOR TEST")
    print("=" * 60)

    api_key    = os.getenv("CAPITAL_API_KEY")
    identifier = os.getenv("CAPITAL_IDENTIFIER")
    password   = os.getenv("CAPITAL_PASSWORD")

    if not api_key or not identifier or not password:
        print("\n⚠️  Set your Capital.com credentials first:")
        print("   export CAPITAL_API_KEY='your-api-key'")
        print("   export CAPITAL_IDENTIFIER='your-email@example.com'")
        print("   export CAPITAL_PASSWORD='your-password'")
        print("\n   Get API key: Settings → API integrations → Add new key")
    else:
        client  = CapitalClient(api_key, identifier, password, demo=True)
        builder = MarketDataBuilder(client)

        print("\nFetching live EUR/USD data...")
        data = builder.build_market_data("EURUSD")

        print("\n" + "=" * 60)
        print("LIVE MARKET DATA:")
        print("=" * 60)
        print(f"Price:          {data['price']}")
        print(f"Spread:         {data['spread']} pips")
        print(f"4H Trend:       {data['ohlcv']['h4_trend']}")
        print(f"Daily Trend:    {data['ohlcv']['daily_trend']}")
        print(f"Weekly Trend:   {data['ohlcv']['weekly_trend']}")
        print(f"RSI 4H:         {data['indicators']['rsi_4h']}")
        print(f"ADX 4H:         {data['indicators']['adx_4h']}")
        print(f"Regime:         {data['indicators']['market_regime']}")
        print(f"Session:        {data['fundamental']['active_session']}")
        print(f"Kill Zone:      {data['fundamental']['kill_zone_active']}")
        print(f"Account:        ${data['portfolio']['equity']:,.2f}")
        print(f"Bullish OB:     {data['indicators']['bullish_ob']}")
        print(f"Bearish OB:     {data['indicators']['bearish_ob']}")
        print(f"Premium/Disc:   {data['indicators']['premium_discount_zone']}")
