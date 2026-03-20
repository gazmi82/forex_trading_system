# =============================================================================
# confluence_scorer.py — Mechanical Confluence Calculator
#
# Replaces Claude's self-reported confluence score with a deterministic,
# independently-verifiable calculation.  Same market_data → same score, always.
#
# Score table mirrors the system prompt thresholds:
#   85+    = STRONG signal
#   65-84  = MODERATE signal
#   <65    = NEUTRAL (no trade)
#
# Usage:
#   from app.analysis.confluence_scorer import calculate_confluence
#   result = calculate_confluence(market_data, signal)
# =============================================================================

from __future__ import annotations

# ---------------------------------------------------------------------------
# Max points per component (must sum to 150 — scores are capped per bucket)
# ---------------------------------------------------------------------------
_TREND_MAX          = 15
_OB_MAX             = 20
_FVG_MAX            = 15
_SWEEP_MAX          = 15
_PD_MAX             = 10
_OTE_MAX            = 10
_RSI_MAX            = 10
_ADX_MAX            = 10
_EMA_MAX            = 5
_RATE_DIFF_MAX      = 15
_DXY_MAX            = 10
_COT_MAX            = 10
_NEWS_MAX           = 5

_TOTAL_POSSIBLE = (
    _TREND_MAX + _OB_MAX + _FVG_MAX + _SWEEP_MAX + _PD_MAX + _OTE_MAX
    + _RSI_MAX + _ADX_MAX + _EMA_MAX + _RATE_DIFF_MAX + _DXY_MAX + _COT_MAX + _NEWS_MAX
)  # 150 — score is normalised to 0-100 before returning


def calculate_confluence(market_data: dict, signal: dict) -> dict:
    """
    Compute a mechanical confluence score for the direction asserted in *signal*.

    Parameters
    ----------
    market_data : dict
        Output of ``MarketDataBuilder.build_market_data()`` — contains
        ``ohlcv``, ``indicators``, and ``fundamental`` sub-dicts.
    signal : dict
        Claude's full signal JSON (the dict stored in signal_*.json).
        Only ``signal["signal"]["direction"]`` is used here; everything
        else comes from market_data.

    Returns
    -------
    dict
        {
            "confluence_score":  int,          # 0-100, normalised
            "direction_implied": str,          # mechanically-derived direction
            "component_scores": {
                "trend_alignment":   int,
                "order_block":       int,
                "fvg":               int,
                "liquidity_sweep":   int,
                "premium_discount":  int,
                "ote":               int,
                "rsi":               int,
                "adx":               int,
                "ema":               int,
                "rate_differential": int,
                "dxy":               int,
                "cot":               int,
                "news_clear":        int,
            }
        }
    """
    direction = (
        (signal.get("signal") or {}).get("direction", "NEUTRAL") or "NEUTRAL"
    ).upper()

    ohlcv       = market_data.get("ohlcv", {})
    indicators  = market_data.get("indicators", {})
    fundamental = market_data.get("fundamental", {})
    price       = float(market_data.get("price", 0) or 0)

    components: dict[str, int] = {
        "trend_alignment":   _score_trend(direction, ohlcv),
        "order_block":       _score_order_block(direction, indicators),
        "fvg":               _score_fvg(direction, indicators),
        "liquidity_sweep":   _score_liquidity_sweep(indicators),
        "premium_discount":  _score_premium_discount(direction, indicators),
        "ote":               _score_ote(price, indicators),
        "rsi":               _score_rsi(direction, indicators),
        "adx":               _score_adx(indicators),
        "ema":               _score_ema(direction, price, indicators),
        "rate_differential": _score_rate_differential(direction, fundamental),
        "dxy":               _score_dxy(direction, fundamental),
        "cot":               _score_cot(direction, fundamental),
        "news_clear":        _score_news(fundamental),
    }

    raw_total = sum(components.values())
    # Normalise from 0-150 to 0-100
    normalised = round(min(100, (raw_total / _TOTAL_POSSIBLE) * 100))

    return {
        "confluence_score":  normalised,
        "direction_implied": _implied_direction(ohlcv, fundamental),
        "component_scores":  components,
    }


# ---------------------------------------------------------------------------
# Individual component scorers
# ---------------------------------------------------------------------------

def _score_trend(direction: str, ohlcv: dict) -> int:
    """
    TREND ALIGNMENT (max 15)
      +15  Weekly + Daily + 4H all aligned with direction
      +10  Daily + 4H aligned, Weekly NEUTRAL
      +5   Only 4H shows direction
    """
    if direction == "NEUTRAL":
        return 0

    weekly = (ohlcv.get("weekly_trend") or "NEUTRAL").upper()
    daily  = (ohlcv.get("daily_trend")  or "NEUTRAL").upper()
    h4     = (ohlcv.get("h4_trend")     or "NEUTRAL").upper()

    direction_bull = direction == "BUY"
    target = "BULLISH" if direction_bull else "BEARISH"

    h4_ok     = h4     == target
    daily_ok  = daily  == target
    weekly_ok = weekly == target

    if weekly_ok and daily_ok and h4_ok:
        return _TREND_MAX          # +15
    if daily_ok and h4_ok and weekly == "NEUTRAL":
        return 10                  # +10
    if h4_ok:
        return 5                   # +5
    return 0


def _score_order_block(direction: str, indicators: dict) -> int:
    """
    ORDER BLOCK (max 20)
      +20  Valid OB exists that is aligned with the trade direction.
           BUY → bullish OB; SELL → bearish OB.
           "valid" must appear in the string returned by _find_order_block.
    """
    if direction == "BUY":
        ob_str = str(indicators.get("bullish_ob") or "")
    elif direction == "SELL":
        ob_str = str(indicators.get("bearish_ob") or "")
    else:
        return 0

    return _OB_MAX if "valid" in ob_str.lower() else 0


def _score_fvg(direction: str, indicators: dict) -> int:
    """
    FAIR VALUE GAP (max 15)
      +15  Relevant FVG (bullish for BUY, bearish for SELL) identified and unfilled.
      +0   No FVG or FVG string indicates "None".
    """
    if direction == "BUY":
        fvg_str = str(indicators.get("bullish_fvg") or "")
    elif direction == "SELL":
        fvg_str = str(indicators.get("bearish_fvg") or "")
    else:
        return 0

    if not fvg_str:
        return 0
    none_indicators = ("none identified", "no fvg")
    return 0 if fvg_str.lower().startswith(none_indicators) else _FVG_MAX


def _score_liquidity_sweep(indicators: dict) -> int:
    """
    LIQUIDITY SWEEP (max 15)
      +15  A sweep was identified in the last 48H.
           Direction-agnostic: the live detector already confirms rejection at the
           swept level, which is the signal that matters regardless of sweep type.
    """
    sweep_str = str(indicators.get("recent_liquidity_sweep") or "").lower()
    has_sweep = sweep_str and "no recent sweep" not in sweep_str
    return _SWEEP_MAX if has_sweep else 0


def _score_premium_discount(direction: str, indicators: dict) -> int:
    """
    PREMIUM / DISCOUNT (max 10)
      +10  BUY in DISCOUNT zone, or SELL in PREMIUM zone.
    """
    pd_str = str(indicators.get("premium_discount_zone") or "").upper()
    if direction == "BUY" and pd_str.startswith("DISCOUNT"):
        return _PD_MAX
    if direction == "SELL" and pd_str.startswith("PREMIUM"):
        return _PD_MAX
    return 0


def _score_ote(price: float, indicators: dict) -> int:
    """
    OTE FIBONACCI ZONE 62-79% (max 10)
      +10  Current price is inside the computed OTE zone.
    """
    ote = indicators.get("ote_zone")
    if not ote or len(ote) < 2:
        return 0
    lo, hi = float(ote[0]), float(ote[1])
    if lo > hi:
        lo, hi = hi, lo
    return _OTE_MAX if lo <= price <= hi else 0


def _score_rsi(direction: str, indicators: dict) -> int:
    """
    RSI DIVERGENCE — simplified as extreme RSI reading (max 10).
      +10  BUY: rsi_4h < 40 OR rsi_1h < 40  (oversold — potential bullish divergence)
      +10  SELL: rsi_4h > 60 OR rsi_1h > 60 (overbought — potential bearish divergence)

    True divergence requires comparing price extremes with RSI extremes across
    multiple candles; that analysis is not available in market_data at this time.
    This proxy scores the precondition: RSI must be in extreme territory for
    divergence to matter.  Phase 5 can refine this to true divergence detection.
    """
    rsi_4h = float(indicators.get("rsi_4h") or 50)
    rsi_1h = float(indicators.get("rsi_1h") or 50)

    if direction == "BUY" and (rsi_4h < 40 or rsi_1h < 40):
        return _RSI_MAX
    if direction == "SELL" and (rsi_4h > 60 or rsi_1h > 60):
        return _RSI_MAX
    return 0


def _score_adx(indicators: dict) -> int:
    """ADX > 25 confirms trend strength (max 10). Direction-agnostic."""
    adx = float(indicators.get("adx_4h") or 0)
    return _ADX_MAX if adx > 25 else 0


def _score_ema(direction: str, price: float, indicators: dict) -> int:
    """
    EMA ALIGNMENT (max 5)
      +5  BUY: price > EMA20(4H) > EMA50(4H)
      +5  SELL: price < EMA20(4H) < EMA50(4H)
    """
    ema20 = float(indicators.get("ema20_4h") or 0)
    ema50 = float(indicators.get("ema50_4h") or 0)
    if not ema20 or not ema50 or not price:
        return 0
    if direction == "BUY" and price > ema20 > ema50:
        return _EMA_MAX
    if direction == "SELL" and price < ema20 < ema50:
        return _EMA_MAX
    return 0


def _score_rate_differential(direction: str, fundamental: dict) -> int:
    """
    RATE DIFFERENTIAL (max 15)
      For EUR/USD:
        BUY  → ECB deposit rate > Fed rate (EUR yield advantage)
        SELL → Fed rate > ECB deposit rate (USD yield advantage)

    Uses ``pair_rate`` (ECB deposit) and ``usd_rate`` (Fed target mid-point).
    Falls back to 0 when data is unavailable.
    """
    usd_rate  = _to_float(fundamental.get("usd_rate"))
    pair_rate = _to_float(fundamental.get("pair_rate"))

    if usd_rate is None or pair_rate is None:
        return 0

    if direction == "BUY" and pair_rate > usd_rate:
        return _RATE_DIFF_MAX
    if direction == "SELL" and usd_rate > pair_rate:
        return _RATE_DIFF_MAX
    return 0


def _score_dxy(direction: str, fundamental: dict) -> int:
    """
    DXY CONFIRMATION (max 10)
      EUR/USD is ~57.6% of DXY (inverse relationship).
        BUY  → DXY FALLING
        SELL → DXY RISING
    """
    dxy = (fundamental.get("dxy_direction") or "NEUTRAL").upper()
    if direction == "BUY" and dxy == "FALLING":
        return _DXY_MAX
    if direction == "SELL" and dxy == "RISING":
        return _DXY_MAX
    return 0


def _score_cot(direction: str, fundamental: dict) -> int:
    """
    COT POSITIONING (max 10)
      +10  COT bias (BULLISH/BEARISH) matches signal direction.
    """
    cot = (fundamental.get("cot_bias") or "NEUTRAL").upper()
    if direction == "BUY" and cot == "BULLISH":
        return _COT_MAX
    if direction == "SELL" and cot == "BEARISH":
        return _COT_MAX
    return 0


def _score_news(fundamental: dict) -> int:
    """
    NO HIGH-IMPACT NEWS IN NEXT 4 HOURS (max 5)
      +5  news_risk is LOW   OR   next event is more than 240 minutes away.
    """
    risk = (fundamental.get("news_risk") or "HIGH").upper()
    if risk == "LOW":
        return _NEWS_MAX

    minutes = _to_float(fundamental.get("time_to_event"))
    if minutes is not None and minutes > 240:
        return _NEWS_MAX

    return 0


# ---------------------------------------------------------------------------
# Direction implied — independent of Claude's signal
# ---------------------------------------------------------------------------

def _implied_direction(ohlcv: dict, fundamental: dict) -> str:
    """
    Determine the market-implied direction using only mechanical signals.
    Uses a simple vote across 5 independent factors:
      - Weekly trend
      - Daily trend
      - H4 trend
      - DXY direction (inverted for EUR/USD)
      - COT bias
    Returns "BUY", "SELL", or "NEUTRAL".
    """
    votes_buy  = 0
    votes_sell = 0

    for tf_key in ("weekly_trend", "daily_trend", "h4_trend"):
        trend = (ohlcv.get(tf_key) or "NEUTRAL").upper()
        if trend == "BULLISH":
            votes_buy += 1
        elif trend == "BEARISH":
            votes_sell += 1

    dxy = (fundamental.get("dxy_direction") or "NEUTRAL").upper()
    if dxy == "FALLING":
        votes_buy += 1
    elif dxy == "RISING":
        votes_sell += 1

    cot = (fundamental.get("cot_bias") or "NEUTRAL").upper()
    if cot == "BULLISH":
        votes_buy += 1
    elif cot == "BEARISH":
        votes_sell += 1

    if votes_buy > votes_sell:
        return "BUY"
    if votes_sell > votes_buy:
        return "SELL"
    return "NEUTRAL"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_float(value) -> float | None:
    """Convert a value to float, returning None on failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
