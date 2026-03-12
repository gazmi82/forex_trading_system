# =============================================================================
# oanda_connector.py — Live OANDA Data Feed
# Fetches real EUR/USD prices, candles, and calculates all indicators
#
# Setup:
#   1. Create free OANDA demo account: oanda.com → "Open Demo Account"
#   2. Get API key: My Account → Manage API Access → Generate Token
#   3. Get Account ID: shown on dashboard
#   4. Set environment variables:
#      export OANDA_API_KEY="your-token-here"
#      export OANDA_ACCOUNT_ID="your-account-id-here"
#
# Install: pip install oandapyV20 pandas ta requests
# =============================================================================

import os
import time
import logging
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# =============================================================================
# OANDA CLIENT
# =============================================================================

class OANDAClient:
    """
    Connects to OANDA REST API v20.
    Works with both demo (practice) and live accounts.
    Always use practice=True during your 12-month demo period.
    """

    def __init__(self, api_key: str, account_id: str, practice: bool = True):
        self.account_id = account_id
        self.practice = practice

        if practice:
            self.base_url = "https://api-fxpractice.oanda.com/v3"
        else:
            self.base_url = "https://api-fxtrade.oanda.com/v3"

        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

        # Test connection on init
        self._test_connection()

    def _test_connection(self):
        """Verify API key and account are working."""
        import requests
        try:
            url = f"{self.base_url}/accounts/{self.account_id}/summary"
            response = requests.get(url, headers=self.headers, timeout=10)

            if response.status_code == 200:
                data = response.json()
                balance = data["account"]["balance"]
                currency = data["account"]["currency"]
                print(f"✅ OANDA Connected: Account balance {currency} {balance}")
                logger.info(f"OANDA connected. Balance: {balance}")
            else:
                print(f"❌ OANDA connection failed: {response.status_code}")
                print(f"   {response.text}")
                raise ConnectionError(f"OANDA API error: {response.status_code}")

        except ImportError:
            raise ImportError("Run: pip install requests")

    def get_current_price(self, instrument: str = "EUR_USD") -> dict:
        """
        Get current bid/ask price for an instrument.
        Returns: {bid, ask, mid, spread, timestamp}
        """
        import requests

        url = f"{self.base_url}/accounts/{self.account_id}/pricing"
        params = {"instruments": instrument}

        response = requests.get(url, headers=self.headers, params=params, timeout=10)
        data = response.json()

        if "prices" not in data or not data["prices"]:
            raise ValueError(f"No price data for {instrument}")

        price = data["prices"][0]
        bid = float(price["bids"][0]["price"])
        ask = float(price["asks"][0]["price"])
        mid = round((bid + ask) / 2, 5)
        spread = round((ask - bid) * 10000, 1)  # in pips

        return {
            "instrument":   instrument,
            "bid":          bid,
            "ask":          ask,
            "mid":          mid,
            "spread_pips":  spread,
            "timestamp":    price["time"],
            "tradeable":    price.get("tradeable", True)
        }

    def get_candles(
        self,
        instrument: str = "EUR_USD",
        granularity: str = "H4",
        count: int = 200
    ) -> pd.DataFrame:
        """
        Fetch OHLCV candlestick data.

        Granularity options:
            M1, M5, M15, M30  (minutes)
            H1, H4             (hours)
            D, W, M            (day, week, month)

        Returns: DataFrame with columns [time, open, high, low, close, volume]
        """
        import requests

        url = f"{self.base_url}/instruments/{instrument}/candles"
        params = {
            "granularity": granularity,
            "count": count,
            "price": "M"  # Midpoint candles
        }

        response = requests.get(url, headers=self.headers, params=params, timeout=15)
        data = response.json()

        if "candles" not in data:
            raise ValueError(f"No candle data: {data}")

        candles = []
        for c in data["candles"]:
            if c["complete"]:
                candles.append({
                    "time":   c["time"],
                    "open":   float(c["mid"]["o"]),
                    "high":   float(c["mid"]["h"]),
                    "low":    float(c["mid"]["l"]),
                    "close":  float(c["mid"]["c"]),
                    "volume": int(c["volume"])
                })

        df = pd.DataFrame(candles)
        df["time"] = pd.to_datetime(df["time"])
        df = df.set_index("time").sort_index()

        return df

    def get_account_summary(self) -> dict:
        """Get current account balance, margin, open trades."""
        import requests

        url = f"{self.base_url}/accounts/{self.account_id}/summary"
        response = requests.get(url, headers=self.headers, timeout=10)
        data = response.json()["account"]

        return {
            "balance":          float(data["balance"]),
            "equity":           float(data["NAV"]),
            "unrealized_pnl":   float(data["unrealizedPL"]),
            "margin_used":      float(data["marginUsed"]),
            "margin_available": float(data["marginAvailable"]),
            "open_trade_count": int(data["openTradeCount"]),
            "currency":         data["currency"]
        }

    def get_open_trades(self) -> list:
        """Get all currently open trades."""
        import requests

        url = f"{self.base_url}/accounts/{self.account_id}/openTrades"
        response = requests.get(url, headers=self.headers, timeout=10)
        data = response.json()

        trades = []
        for t in data.get("trades", []):
            trades.append({
                "id":           t["id"],
                "instrument":   t["instrument"],
                "units":        float(t["currentUnits"]),
                "open_price":   float(t["price"]),
                "unrealized_pl":float(t["unrealizedPL"]),
                "open_time":    t["openTime"]
            })

        return trades


# =============================================================================
# INDICATOR CALCULATOR
# =============================================================================

class IndicatorCalculator:
    """
    Calculates all technical indicators needed by the Forex Analyst agent.
    Uses the 'ta' library (free, no API needed).
    """

    @staticmethod
    def calculate_all(df_4h: pd.DataFrame, df_1h: pd.DataFrame, df_daily: pd.DataFrame) -> dict:
        """
        Calculate all indicators from OHLCV data.
        Returns dict ready for injection into agent context.
        """
        indicators = {}

        # EMAs
        indicators["ema20_4h"]      = IndicatorCalculator._ema(df_4h, 20)
        indicators["ema50_4h"]      = IndicatorCalculator._ema(df_4h, 50)
        indicators["ema200_daily"]  = IndicatorCalculator._ema(df_daily, 200)

        # RSI
        indicators["rsi_4h"]        = IndicatorCalculator._rsi(df_4h, 14)
        indicators["rsi_1h"]        = IndicatorCalculator._rsi(df_1h, 14)

        # ADX
        indicators["adx_4h"]        = IndicatorCalculator._adx(df_4h, 14)

        # ATR
        indicators["atr_4h"]        = IndicatorCalculator._atr(df_4h, 14)

        # Market regime
        indicators["market_regime"]  = IndicatorCalculator._regime(
            indicators["adx_4h"],
            indicators["atr_4h"],
            df_4h["close"].iloc[-1]
        )

        # Key levels
        indicators["resistance_levels"] = IndicatorCalculator._resistance(df_daily)
        indicators["support_levels"]    = IndicatorCalculator._support(df_daily)
        indicators["round_numbers"]     = IndicatorCalculator._round_numbers(df_4h["close"].iloc[-1])

        # ICT concepts
        indicators["premium_discount_zone"] = IndicatorCalculator._premium_discount(df_4h)
        indicators["bullish_ob"]            = IndicatorCalculator._find_order_block(df_4h, "bullish")
        indicators["bearish_ob"]            = IndicatorCalculator._find_order_block(df_4h, "bearish")
        indicators["bullish_fvg"]           = IndicatorCalculator._find_fvg(df_1h, "bullish")
        indicators["bearish_fvg"]           = IndicatorCalculator._find_fvg(df_1h, "bearish")
        indicators["recent_liquidity_sweep"] = IndicatorCalculator._find_liquidity_sweep(df_1h)
        indicators["ote_zone"]               = IndicatorCalculator._ote_zone(df_4h)

        return indicators

    @staticmethod
    def _ema(df: pd.DataFrame, period: int) -> float:
        ema = df["close"].ewm(span=period, adjust=False).mean()
        return round(ema.iloc[-1], 5)

    @staticmethod
    def _rsi(df: pd.DataFrame, period: int = 14) -> float:
        delta = df["close"].diff()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return round(rsi.iloc[-1], 2)

    @staticmethod
    def _adx(df: pd.DataFrame, period: int = 14) -> float:
        high, low, close = df["high"], df["low"], df["close"]
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs()
        ], axis=1).max(axis=1)

        dm_plus  = (high.diff()).clip(lower=0)
        dm_minus = (-low.diff()).clip(lower=0)

        tr_smooth   = tr.rolling(period).mean()
        dmp_smooth  = dm_plus.rolling(period).mean()
        dmm_smooth  = dm_minus.rolling(period).mean()

        di_plus  = 100 * dmp_smooth / tr_smooth
        di_minus = 100 * dmm_smooth / tr_smooth
        dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus)
        adx = dx.rolling(period).mean()

        return round(adx.iloc[-1], 2)

    @staticmethod
    def _atr(df: pd.DataFrame, period: int = 14) -> float:
        high, low, close = df["high"], df["low"], df["close"]
        tr = pd.concat([
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs()
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
        highs = df["high"].tail(lookback)
        levels = []
        for i in range(2, len(highs) - 2):
            if highs.iloc[i] == highs.iloc[i-2:i+3].max():
                levels.append(round(highs.iloc[i], 4))
        levels = sorted(set(levels), reverse=True)
        return levels[:3]

    @staticmethod
    def _support(df: pd.DataFrame, lookback: int = 50) -> list:
        lows = df["low"].tail(lookback)
        levels = []
        for i in range(2, len(lows) - 2):
            if lows.iloc[i] == lows.iloc[i-2:i+3].min():
                levels.append(round(lows.iloc[i], 4))
        levels = sorted(set(levels))
        return levels[:3]

    @staticmethod
    def _round_numbers(price: float) -> list:
        base = round(price, 2)
        return [
            round(base - 0.01, 2),
            round(base, 2),
            round(base + 0.01, 2)
        ]

    @staticmethod
    def _premium_discount(df: pd.DataFrame, lookback: int = 20) -> str:
        recent = df.tail(lookback)
        swing_high = recent["high"].max()
        swing_low  = recent["low"].min()
        current    = recent["close"].iloc[-1]
        if swing_high == swing_low:
            return "EQUILIBRIUM (flat range)"
        equilibrium = (swing_high + swing_low) / 2

        if current > equilibrium * 1.002:
            return f"PREMIUM ({round((current - swing_low) / (swing_high - swing_low) * 100)}% of range)"
        elif current < equilibrium * 0.998:
            return f"DISCOUNT ({round((current - swing_low) / (swing_high - swing_low) * 100)}% of range)"
        return "EQUILIBRIUM (50% of range)"

    @staticmethod
    def _ote_zone(df: pd.DataFrame, lookback: int = 20) -> list:
        """
        Returns a 62%-79% retracement zone from the most recent 4H swing.
        This is a structural reference zone, not an execution trigger on its own.
        """
        recent = df.tail(lookback)
        swing_high = float(recent["high"].max())
        swing_low = float(recent["low"].min())
        move_range = swing_high - swing_low
        if move_range <= 0:
            return []

        last_close = float(recent["close"].iloc[-1])
        midpoint = (swing_high + swing_low) / 2

        if last_close >= midpoint:
            lower = round(swing_high - (move_range * 0.79), 5)
            upper = round(swing_high - (move_range * 0.62), 5)
        else:
            lower = round(swing_low + (move_range * 0.62), 5)
            upper = round(swing_low + (move_range * 0.79), 5)
        return [lower, upper]

    @staticmethod
    def _find_order_block(df: pd.DataFrame, direction: str) -> str:
        """Find the most recent valid order block."""
        lookback = df.tail(50)

        if direction == "bullish":
            # Last bearish candle before strong bullish move
            for i in range(len(lookback) - 3, 2, -1):
                candle = lookback.iloc[i]
                next_3 = lookback.iloc[i+1:i+4]
                is_bearish = candle["close"] < candle["open"]
                strong_move = (next_3["close"].max() - candle["low"]) > \
                              (candle["high"] - candle["low"]) * 2

                if is_bearish and strong_move:
                    ob_low  = round(candle["low"], 5)
                    ob_high = round(candle["high"], 5)
                    return f"{ob_low}–{ob_high} (4H, valid)"

        elif direction == "bearish":
            # Last bullish candle before strong bearish move
            for i in range(len(lookback) - 3, 2, -1):
                candle = lookback.iloc[i]
                next_3 = lookback.iloc[i+1:i+4]
                is_bullish = candle["close"] > candle["open"]
                strong_move = (candle["high"] - next_3["close"].min()) > \
                              (candle["high"] - candle["low"]) * 2

                if is_bullish and strong_move:
                    ob_low  = round(candle["low"], 5)
                    ob_high = round(candle["high"], 5)
                    return f"{ob_low}–{ob_high} (4H, valid)"

        return "None identified in last 50 candles"

    @staticmethod
    def _find_fvg(df: pd.DataFrame, direction: str) -> str:
        """Find the most recent Fair Value Gap."""
        lookback = df.tail(30)

        for i in range(len(lookback) - 3, 1, -1):
            c1 = lookback.iloc[i-1]
            c3 = lookback.iloc[i+1]

            if direction == "bullish":
                # Gap between c1 high and c3 low
                if c1["high"] < c3["low"]:
                    return f"{round(c1['high'], 5)}–{round(c3['low'], 5)} (1H, unfilled)"

            elif direction == "bearish":
                # Gap between c1 low and c3 high
                if c1["low"] > c3["high"]:
                    return f"{round(c3['high'], 5)}–{round(c1['low'], 5)} (1H, unfilled)"

        return "None identified"

    @staticmethod
    def _find_liquidity_sweep(df: pd.DataFrame) -> str:
        """Detect recent liquidity sweeps (stop hunts)."""
        recent = df.tail(10)

        for i in range(len(recent) - 1, 0, -1):
            candle = recent.iloc[i]
            prev_low  = recent.iloc[:i]["low"].min()
            prev_high = recent.iloc[:i]["high"].max()

            # Sweep below recent low then close above
            if candle["low"] < prev_low and candle["close"] > prev_low:
                hours_ago = len(recent) - 1 - i
                return f"SSL swept at {round(prev_low, 5)} ({hours_ago}h ago, strong rejection)"

            # Sweep above recent high then close below
            if candle["high"] > prev_high and candle["close"] < prev_high:
                hours_ago = len(recent) - 1 - i
                return f"BSL swept at {round(prev_high, 5)} ({hours_ago}h ago, strong rejection)"

        return "No recent sweep identified"


# =============================================================================
# MARKET STRUCTURE ANALYZER
# =============================================================================

class MarketStructureAnalyzer:
    """Identifies trend structure across multiple timeframes."""

    @staticmethod
    def analyze(df: pd.DataFrame, timeframe: str) -> dict:
        """Returns trend direction and structure description."""
        if len(df) < 10:
            return {"trend": "NEUTRAL", "structure": "Insufficient data"}

        closes = df["close"]
        highs  = df["high"]
        lows   = df["low"]

        # Simple swing point analysis
        recent_highs = highs.tail(20)
        recent_lows  = lows.tail(20)

        hh = recent_highs.iloc[-1] > recent_highs.iloc[-10]  # Higher high
        hl = recent_lows.iloc[-1]  > recent_lows.iloc[-10]   # Higher low
        lh = recent_highs.iloc[-1] < recent_highs.iloc[-10]  # Lower high
        ll = recent_lows.iloc[-1]  < recent_lows.iloc[-10]   # Lower low

        if hh and hl:
            trend     = "BULLISH"
            structure = f"HH + HL — Bullish {timeframe} structure"
        elif lh and ll:
            trend     = "BEARISH"
            structure = f"LH + LL — Bearish {timeframe} structure"
        elif hh and ll:
            trend     = "NEUTRAL"
            structure = f"Mixed — Expanding range {timeframe}"
        else:
            trend     = "NEUTRAL"
            structure = f"Ranging — Equal highs/lows {timeframe}"

        return {"trend": trend, "structure": structure}


# =============================================================================
# MARKET DATA BUILDER
# =============================================================================

class MarketDataBuilder:
    """
    Orchestrates all data fetching and indicator calculation.
    Produces the complete market_data dict ready for the agent.
    """

    def __init__(self, oanda_client: OANDAClient):
        self.client     = oanda_client
        self.calculator = IndicatorCalculator()
        self.structure  = MarketStructureAnalyzer()
        # Daily P&L tracking — records starting balance once per UTC day
        self._daily_start_balance: Optional[float] = None
        self._daily_start_date: Optional[str]      = None

    def build_market_data(
        self,
        pair: str = "EUR_USD",
        account_equity: float = None
    ) -> dict:
        """
        Fetches all live data and builds the complete market_data dict.
        This replaces the static example data in main.py.
        """
        print(f"\n📡 Fetching live data for {pair}...")

        # Fetch candles for all timeframes
        print("  Fetching 4H candles...")
        df_4h    = self.client.get_candles(pair, "H4",  count=200)

        print("  Fetching 1H candles...")
        df_1h    = self.client.get_candles(pair, "H1",  count=200)

        print("  Fetching 15M candles...")
        df_15m   = self.client.get_candles(pair, "M15", count=200)

        print("  Fetching Daily candles...")
        df_daily = self.client.get_candles(pair, "D",   count=200)

        print("  Fetching Weekly candles...")
        df_weekly = self.client.get_candles(pair, "W",  count=52)

        # Current price
        price_data = self.client.get_current_price(pair)
        current_price = price_data["mid"]
        print(f"  Current price: {current_price} (spread: {price_data['spread_pips']} pips)")

        # Market structure across timeframes
        weekly_struct  = self.structure.analyze(df_weekly, "Weekly")
        daily_struct   = self.structure.analyze(df_daily,  "Daily")
        h4_struct      = self.structure.analyze(df_4h,     "4H")
        h1_struct      = self.structure.analyze(df_1h,     "1H")
        m15_struct     = self.structure.analyze(df_15m,    "15M")

        # Calculate all indicators
        print("  Calculating indicators...")
        indicators = IndicatorCalculator.calculate_all(df_4h, df_1h, df_daily)

        # OHLCV reference levels
        current_day = df_daily.index[-1]
        month_mask = (
            (df_daily.index.year == current_day.year) &
            (df_daily.index.month == current_day.month)
        )
        ohlcv = {
            "day_open":          round(df_daily["open"].iloc[-1], 5),
            "week_open":         round(df_weekly["open"].iloc[-1], 5),
            "month_open":        round(df_daily.loc[month_mask, "open"].iloc[0], 5),
            "prev_day_high":     round(df_daily["high"].iloc[-2], 5),
            "prev_day_low":      round(df_daily["low"].iloc[-2], 5),
            "prev_week_high":    round(df_weekly["high"].iloc[-2], 5),
            "prev_week_low":     round(df_weekly["low"].iloc[-2], 5),
            "weekly_structure":  weekly_struct["structure"],
            "daily_structure":   daily_struct["structure"],
            "h4_structure":      h4_struct["structure"],
            "h1_structure":      h1_struct["structure"],
            "m15_structure":     m15_struct["structure"],
            "weekly_trend":      weekly_struct["trend"],
            "daily_trend":       daily_struct["trend"],
            "h4_trend":          h4_struct["trend"],
            "h1_trend":          h1_struct["trend"],
            "m15_trend":         m15_struct["trend"],
        }

        # Session detection
        session_info = self._get_session_info()

        # Fundamental data — DXY, calendar, and news are live-fetched where available
        fundamental = self._get_fundamental_data(session_info, ohlcv)

        # Portfolio state from OANDA
        account = self.client.get_account_summary()
        open_trades = self.client.get_open_trades()
        equity = account_equity or account["equity"]

        open_risk_pct = 0.0
        if open_trades:
            open_risk_pct = round(
                abs(sum(t["unrealized_pl"] for t in open_trades)) / equity * 100, 2
            )

        # Daily P&L: realized balance delta since start of UTC day
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._daily_start_date != today:
            self._daily_start_balance = account["balance"]
            self._daily_start_date    = today
        start_bal     = self._daily_start_balance or account["balance"]
        daily_pnl_pct = round((account["balance"] - start_bal) / start_bal * 100, 2) \
                        if start_bal > 0 else 0.0

        portfolio = {
            "equity":           equity,
            "open_trades":      len(open_trades),
            "open_risk_pct":    open_risk_pct,
            "daily_pnl_pct":    daily_pnl_pct,
            "trades_today":     len(open_trades),
            "usd_exposure":     self._calculate_usd_exposure(open_trades),
            "margin_used_pct":  round(account["margin_used"] / equity * 100, 2),
        }

        print(f"  ✅ Live data ready — Account: ${equity:,.2f}")

        return {
            "pair":      pair.replace("_", "/"),
            "price":     current_price,
            "spread":    price_data["spread_pips"],
            "demo_mode": self.client.practice,
            "ohlcv":     ohlcv,
            "indicators":indicators,
            "fundamental": fundamental,
            "portfolio": portfolio,
            "fetch_time": datetime.now(timezone.utc).isoformat(),
        }

    def _get_session_info(self) -> dict:
        """Determine current trading session based on UTC time, DST-aware."""
        import zoneinfo
        now_utc  = datetime.now(timezone.utc)
        now_et   = now_utc.astimezone(zoneinfo.ZoneInfo("America/New_York"))
        hour_est = now_et.hour   # Correct for both EST (UTC-5) and EDT (UTC-4)

        if 3 <= hour_est < 4:
            session = "London Kill Zone"
            kill_zone = "YES — London Kill Zone (3-4 AM EST)"
        elif 4 <= hour_est < 8:
            session = "London Session"
            kill_zone = "NO — London session, wait for kill zone quality"
        elif 8 <= hour_est < 10:
            session = "NY Kill Zone"
            kill_zone = "YES — NY Kill Zone (8-10 AM EST)"
        elif 10 <= hour_est < 12:
            session = "London Close"
            kill_zone = "YES — London Close (10 AM-12 PM EST)"
        elif 12 <= hour_est < 14:
            session = "Low Liquidity"
            kill_zone = "NO — Lunch hours (12PM-2PM EST)"
        elif 14 <= hour_est < 17:
            session = "New York Session"
            kill_zone = "NO — NY session, but outside kill zone"
        elif 20 <= hour_est or hour_est < 3:
            session = "Asian Session"
            kill_zone = "NO — Asian session (observe only)"
        else:
            session = "Low Liquidity"
            kill_zone = "NO — Avoid trading"

        return {
            "active_session":   session,
            "kill_zone_active": kill_zone,
            "hour_est":         hour_est,
            "trade_window_active": session in {"London Kill Zone", "NY Kill Zone", "London Close"},
        }

    def _get_fundamental_data(self, session_info: dict, ohlcv: dict = None) -> dict:
        """
        Returns fundamental context.
        DXY is fetched intraday, COT is weekly macro positioning,
        FMP provides the next high-impact calendar event,
        NewsAPI provides the latest FX headline when NEWS_API_KEY is set,
        and SPY is used as a live risk-on/risk-off proxy.
        Manual env vars take priority over auto-fetched values:

            export DXY_DIRECTION="FALLING"        # override auto DXY
            export DXY_LEVEL="104.20"             # override auto DXY level
            export COT_BIAS="BULLISH"             # override auto COT
            export COT_NET="+18500"               # override auto COT net
            export RETAIL_SENTIMENT="72% SHORT"   # myfxbook.com/community/outlook
            export USD_RATE="4.50"                # Fed Funds Rate (after FOMC)
            export EUR_RATE="3.65"                # ECB Rate (after ECB meeting)
        """
        from fundamentals_fetcher import get_auto_fundamentals

        usd_rate = float(os.getenv("USD_RATE", "4.50"))
        eur_rate = float(os.getenv("EUR_RATE", "3.65"))
        diff     = round(usd_rate - eur_rate, 2)
        diff_str = f"+{diff}% USD favor" if diff > 0 else f"{diff}% EUR favor"

        daily_trend = (ohlcv or {}).get("daily_trend", "NEUTRAL")
        h4_trend    = (ohlcv or {}).get("h4_trend",    "NEUTRAL")

        auto = get_auto_fundamentals(daily_trend, h4_trend)

        return {
            "usd_rate":          usd_rate,
            "pair_rate":         eur_rate,
            "rate_differential": diff_str,
            "dxy_direction":     auto["dxy_direction"],
            "dxy_level":         auto["dxy_level"],
            "cot_net":           auto["cot_net"],
            "cot_bias":          auto["cot_bias"],
            "retail_sentiment":  auto["retail_sentiment"],
            "risk_sentiment":    auto["risk_sentiment"],
            "next_event_name":   auto["next_event_name"],
            "next_news_event":   auto["next_news_event"],
            "time_to_event":     auto["time_to_event"],
            "news_risk":         auto["news_risk"],
            "recent_headline":   auto["recent_headline"],
            "active_session":    session_info["active_session"],
            "kill_zone_active":  session_info["kill_zone_active"],
            "trade_window_active": session_info["trade_window_active"],
        }

    def _calculate_usd_exposure(self, open_trades: list) -> str:
        """Calculate total USD exposure across open trades."""
        if not open_trades:
            return "NONE"
        usd_pairs = [t for t in open_trades if "USD" in t["instrument"]]
        if not usd_pairs:
            return "NONE"
        total_units = sum(abs(t["units"]) for t in usd_pairs)
        return f"{len(usd_pairs)} trades, {total_units:,.0f} units"


# =============================================================================
# UPDATED MAIN LOOP — Replaces static data with live OANDA data
# =============================================================================

def create_live_market_data_function():
    """
    Returns a function that fetches live data from OANDA.
    Drop-in replacement for get_example_market_data() in main.py
    """
    api_key    = os.getenv("OANDA_API_KEY")
    account_id = os.getenv("OANDA_ACCOUNT_ID")

    if not api_key or not account_id:
        print("⚠️  OANDA credentials not set.")
        print("   Set them with:")
        print("   export OANDA_API_KEY='your-token'")
        print("   export OANDA_ACCOUNT_ID='your-account-id'")
        return None

    try:
        client  = OANDAClient(api_key, account_id, practice=True)
        builder = MarketDataBuilder(client)
        return lambda: builder.build_market_data("EUR_USD")
    except Exception as e:
        print(f"❌ OANDA connection failed: {e}")
        return None


# =============================================================================
# STANDALONE TEST
# =============================================================================

if __name__ == "__main__":
    print("="*60)
    print("OANDA LIVE DATA CONNECTOR TEST")
    print("="*60)

    api_key    = os.getenv("OANDA_API_KEY")
    account_id = os.getenv("OANDA_ACCOUNT_ID")

    if not api_key or not account_id:
        print("\n⚠️  Set your OANDA credentials first:")
        print("   export OANDA_API_KEY='your-token-here'")
        print("   export OANDA_ACCOUNT_ID='your-account-id-here'")
        print("\n   Get them from: oanda.com → My Account → Manage API Access")
    else:
        client  = OANDAClient(api_key, account_id, practice=True)
        builder = MarketDataBuilder(client)

        print("\nFetching live EUR/USD data...")
        data = builder.build_market_data("EUR_USD")

        print("\n" + "="*60)
        print("LIVE MARKET DATA:")
        print("="*60)
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
