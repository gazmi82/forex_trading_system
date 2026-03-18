import pandas as pd


class IndicatorCalculator:
    """
    Calculates the technical and ICT-derived reference levels used by the
    trading agent. This module is pure DataFrame math with no network access.
    """

    @staticmethod
    def calculate_all(df_4h: pd.DataFrame, df_1h: pd.DataFrame, df_daily: pd.DataFrame) -> dict:
        indicators = {}

        indicators["ema20_4h"] = IndicatorCalculator._ema(df_4h, 20)
        indicators["ema50_4h"] = IndicatorCalculator._ema(df_4h, 50)
        indicators["ema200_daily"] = IndicatorCalculator._ema(df_daily, 200)

        indicators["rsi_4h"] = IndicatorCalculator._rsi(df_4h, 14)
        indicators["rsi_1h"] = IndicatorCalculator._rsi(df_1h, 14)
        indicators["adx_4h"] = IndicatorCalculator._adx(df_4h, 14)
        indicators["atr_4h"] = IndicatorCalculator._atr(df_4h, 14)

        indicators["market_regime"] = IndicatorCalculator._regime(
            indicators["adx_4h"],
            indicators["atr_4h"],
            df_4h["close"].iloc[-1],
        )

        indicators["resistance_levels"] = IndicatorCalculator._resistance(df_daily)
        indicators["support_levels"] = IndicatorCalculator._support(df_daily)
        indicators["round_numbers"] = IndicatorCalculator._round_numbers(df_4h["close"].iloc[-1])

        indicators["premium_discount_zone"] = IndicatorCalculator._premium_discount(df_4h)
        indicators["bullish_ob"] = IndicatorCalculator._find_order_block(df_4h, "bullish")
        indicators["bearish_ob"] = IndicatorCalculator._find_order_block(df_4h, "bearish")
        indicators["bullish_fvg"] = IndicatorCalculator._find_fvg(df_1h, "bullish")
        indicators["bearish_fvg"] = IndicatorCalculator._find_fvg(df_1h, "bearish")
        indicators["recent_liquidity_sweep"] = IndicatorCalculator._find_liquidity_sweep(df_1h)
        indicators["ote_zone"] = IndicatorCalculator._ote_zone(df_4h)

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
        tr = IndicatorCalculator._true_range(df)
        high = df["high"]
        low = df["low"]
        dm_plus = high.diff().clip(lower=0)
        dm_minus = (-low.diff()).clip(lower=0)

        tr_smooth = tr.rolling(period).mean()
        dmp_smooth = dm_plus.rolling(period).mean()
        dmm_smooth = dm_minus.rolling(period).mean()

        di_plus = 100 * dmp_smooth / tr_smooth
        di_minus = 100 * dmm_smooth / tr_smooth
        dx = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus)
        adx = dx.rolling(period).mean()

        return round(adx.iloc[-1], 2)

    @staticmethod
    def _atr(df: pd.DataFrame, period: int = 14) -> float:
        tr = IndicatorCalculator._true_range(df)
        atr = tr.rolling(period).mean()
        return round(atr.iloc[-1], 5)

    @staticmethod
    def _true_range(df: pd.DataFrame) -> pd.Series:
        high = df["high"]
        low = df["low"]
        close = df["close"]
        return pd.concat(
            [
                high - low,
                (high - close.shift()).abs(),
                (low - close.shift()).abs(),
            ],
            axis=1,
        ).max(axis=1)

    @staticmethod
    def _regime(adx: float, atr: float, price: float) -> str:
        atr_pct = (atr / price) * 100
        if adx > 25 and atr_pct > 0.3:
            return "HIGH_VOLATILITY" if atr_pct > 0.6 else "TRENDING"
        return "RANGING"

    @staticmethod
    def _resistance(df: pd.DataFrame, lookback: int = 50) -> list:
        return IndicatorCalculator._swing_levels(
            df["high"].tail(lookback),
            selector=max,
            reverse=True,
        )

    @staticmethod
    def _support(df: pd.DataFrame, lookback: int = 50) -> list:
        return IndicatorCalculator._swing_levels(
            df["low"].tail(lookback),
            selector=min,
            reverse=False,
        )

    @staticmethod
    def _swing_levels(series: pd.Series, *, selector, reverse: bool) -> list:
        levels = []
        for i in range(2, len(series) - 2):
            window = series.iloc[i - 2:i + 3]
            if series.iloc[i] == selector(window):
                levels.append(round(series.iloc[i], 4))
        levels = sorted(set(levels))
        if reverse:
            levels.reverse()
        return levels[:3]

    @staticmethod
    def _round_numbers(price: float) -> list:
        base = round(price, 2)
        return [
            round(base - 0.01, 2),
            round(base, 2),
            round(base + 0.01, 2),
        ]

    @staticmethod
    def _premium_discount(df: pd.DataFrame, lookback: int = 20) -> str:
        recent = df.tail(lookback)
        swing_high = recent["high"].max()
        swing_low = recent["low"].min()
        current = recent["close"].iloc[-1]
        if swing_high == swing_low:
            return "EQUILIBRIUM (flat range)"

        equilibrium = (swing_high + swing_low) / 2

        if current > equilibrium * 1.002:
            pct = round((current - swing_low) / (swing_high - swing_low) * 100)
            return f"PREMIUM ({pct}% of range)"
        if current < equilibrium * 0.998:
            pct = round((current - swing_low) / (swing_high - swing_low) * 100)
            return f"DISCOUNT ({pct}% of range)"
        return "EQUILIBRIUM (50% of range)"

    @staticmethod
    def _ote_zone(df: pd.DataFrame, lookback: int = 20) -> list:
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
        lookback = df.tail(50)

        if direction == "bullish":
            for i in range(len(lookback) - 3, 2, -1):
                candle = lookback.iloc[i]
                next_3 = lookback.iloc[i + 1:i + 4]
                is_bearish = candle["close"] < candle["open"]
                strong_move = (next_3["close"].max() - candle["low"]) > (
                    candle["high"] - candle["low"]
                ) * 2

                if is_bearish and strong_move:
                    ob_low = round(candle["low"], 5)
                    ob_high = round(candle["high"], 5)
                    return f"{ob_low}–{ob_high} (4H, valid)"

        elif direction == "bearish":
            for i in range(len(lookback) - 3, 2, -1):
                candle = lookback.iloc[i]
                next_3 = lookback.iloc[i + 1:i + 4]
                is_bullish = candle["close"] > candle["open"]
                strong_move = (candle["high"] - next_3["close"].min()) > (
                    candle["high"] - candle["low"]
                ) * 2

                if is_bullish and strong_move:
                    ob_low = round(candle["low"], 5)
                    ob_high = round(candle["high"], 5)
                    return f"{ob_low}–{ob_high} (4H, valid)"

        return "None identified in last 50 candles"

    @staticmethod
    def _find_fvg(df: pd.DataFrame, direction: str) -> str:
        lookback = df.tail(30)

        for i in range(len(lookback) - 3, 1, -1):
            c1 = lookback.iloc[i - 1]
            c3 = lookback.iloc[i + 1]

            if direction == "bullish":
                if c1["high"] < c3["low"]:
                    return f"{round(c1['high'], 5)}–{round(c3['low'], 5)} (1H, unfilled)"

            elif direction == "bearish":
                if c1["low"] > c3["high"]:
                    return f"{round(c3['high'], 5)}–{round(c1['low'], 5)} (1H, unfilled)"

        return "None identified"

    @staticmethod
    def _find_liquidity_sweep(df: pd.DataFrame) -> str:
        recent = df.tail(10)

        for i in range(len(recent) - 1, 0, -1):
            candle = recent.iloc[i]
            prev_low = recent.iloc[:i]["low"].min()
            prev_high = recent.iloc[:i]["high"].max()

            if candle["low"] < prev_low and candle["close"] > prev_low:
                hours_ago = len(recent) - 1 - i
                return f"SSL swept at {round(prev_low, 5)} ({hours_ago}h ago, strong rejection)"

            if candle["high"] > prev_high and candle["close"] < prev_high:
                hours_ago = len(recent) - 1 - i
                return f"BSL swept at {round(prev_high, 5)} ({hours_ago}h ago, strong rejection)"

        return "No recent sweep identified"


class MarketStructureAnalyzer:
    """Identifies trend structure across multiple timeframes."""

    PIVOT_WINDOW = 2
    EQUALITY_TOLERANCE_ATR_RATIO = 0.15

    @staticmethod
    def analyze(df: pd.DataFrame, timeframe: str) -> dict:
        if len(df) < (MarketStructureAnalyzer.PIVOT_WINDOW * 2) + 5:
            return {"trend": "NEUTRAL", "structure": "Insufficient data"}

        highs = df["high"].astype(float)
        lows = df["low"].astype(float)

        swing_highs = MarketStructureAnalyzer._find_confirmed_pivots(
            highs,
            is_high=True,
            window=MarketStructureAnalyzer.PIVOT_WINDOW,
        )
        swing_lows = MarketStructureAnalyzer._find_confirmed_pivots(
            lows,
            is_high=False,
            window=MarketStructureAnalyzer.PIVOT_WINDOW,
        )

        if len(swing_highs) < 2 or len(swing_lows) < 2:
            return {
                "trend": "NEUTRAL",
                "structure": f"Transitional — awaiting confirmed swing sequence on {timeframe}",
            }

        tolerance = MarketStructureAnalyzer._structure_tolerance(df)

        high_state = MarketStructureAnalyzer._classify_level_change(
            previous_level=swing_highs[-2][1],
            current_level=swing_highs[-1][1],
            tolerance=tolerance,
            higher_label="HH",
            lower_label="LH",
            equal_label="EH",
        )
        low_state = MarketStructureAnalyzer._classify_level_change(
            previous_level=swing_lows[-2][1],
            current_level=swing_lows[-1][1],
            tolerance=tolerance,
            higher_label="HL",
            lower_label="LL",
            equal_label="EL",
        )

        if high_state == "HH" and low_state == "HL":
            trend = "BULLISH"
            structure = f"HH + HL — Bullish {timeframe} structure"
        elif high_state == "LH" and low_state == "LL":
            trend = "BEARISH"
            structure = f"LH + LL — Bearish {timeframe} structure"
        elif high_state == "HH" and low_state == "LL":
            trend = "NEUTRAL"
            structure = f"Mixed — Expanding range {timeframe}"
        elif high_state == "LH" and low_state == "HL":
            trend = "NEUTRAL"
            structure = f"Mixed — Contracting range {timeframe}"
        elif high_state in {"HH", "EH"} and low_state in {"HL", "EL"}:
            trend = "NEUTRAL"
            structure = f"Bullish pressure — awaiting full swing confirmation on {timeframe}"
        elif high_state in {"LH", "EH"} and low_state in {"LL", "EL"}:
            trend = "NEUTRAL"
            structure = f"Bearish pressure — awaiting full swing confirmation on {timeframe}"
        else:
            trend = "NEUTRAL"
            structure = f"Ranging — Equal highs/lows {timeframe}"

        return {"trend": trend, "structure": structure}

    @staticmethod
    def _find_confirmed_pivots(
        series: pd.Series,
        *,
        is_high: bool,
        window: int,
    ) -> list[tuple[pd.Timestamp, float]]:
        pivots: list[tuple[pd.Timestamp, float]] = []
        if len(series) < (window * 2) + 1:
            return pivots

        for idx in range(window, len(series) - window):
            current = float(series.iloc[idx])
            left = series.iloc[idx - window:idx]
            right = series.iloc[idx + 1:idx + window + 1]

            if is_high:
                if current > float(left.max()) and current >= float(right.max()):
                    pivots.append((series.index[idx], current))
            else:
                if current < float(left.min()) and current <= float(right.min()):
                    pivots.append((series.index[idx], current))

        return pivots

    @staticmethod
    def _structure_tolerance(df: pd.DataFrame) -> float:
        tr = IndicatorCalculator._true_range(df).tail(20).dropna()
        close = float(df["close"].iloc[-1])
        pip_floor = max(close * 0.0001, 0.00001)

        if tr.empty:
            return pip_floor

        return max(float(tr.median()) * MarketStructureAnalyzer.EQUALITY_TOLERANCE_ATR_RATIO, pip_floor)

    @staticmethod
    def _classify_level_change(
        *,
        previous_level: float,
        current_level: float,
        tolerance: float,
        higher_label: str,
        lower_label: str,
        equal_label: str,
    ) -> str:
        if current_level > previous_level + tolerance:
            return higher_label
        if current_level < previous_level - tolerance:
            return lower_label
        return equal_label
