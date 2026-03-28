# =============================================================================
# replay_confluence.py — Deterministic Replay Confluence Calculator
#
# Builds a repeatable score from historical market_data for offline replay.
# Same market_data → same score, always.
#
# Score table mirrors the system prompt thresholds:
#   85+    = STRONG signal
#   65-84  = MODERATE signal
#   <65    = NEUTRAL (no trade)
#
# Usage:
#   from app.backtesting.replay_confluence import calculate_confluence
#   result = calculate_confluence(market_data, signal)
# =============================================================================

from __future__ import annotations

from app.fundamentals.common import relative_minutes
from app.fundamentals.payloads import HISTORICAL_NEWS_UNAVAILABLE

# ---------------------------------------------------------------------------
# Max points per component (must sum to 150 for the full live-style stack)
# ---------------------------------------------------------------------------
_TREND_MAX = 15
_OB_MAX = 20
_FVG_MAX = 15
_SWEEP_MAX = 15
_PD_MAX = 10
_OTE_MAX = 10
_RSI_MAX = 10
_ADX_MAX = 10
_EMA_MAX = 5
_RATE_DIFF_MAX = 15
_DXY_MAX = 10
_COT_MAX = 10
_NEWS_MAX = 5

_COMPONENT_MAX = {
    "trend_alignment": _TREND_MAX,
    "order_block": _OB_MAX,
    "fvg": _FVG_MAX,
    "liquidity_sweep": _SWEEP_MAX,
    "premium_discount": _PD_MAX,
    "ote": _OTE_MAX,
    "rsi": _RSI_MAX,
    "adx": _ADX_MAX,
    "ema": _EMA_MAX,
    "rate_differential": _RATE_DIFF_MAX,
    "dxy": _DXY_MAX,
    "cot": _COT_MAX,
    "news_clear": _NEWS_MAX,
}

_TOTAL_POSSIBLE = sum(_COMPONENT_MAX.values())  # 150


def calculate_confluence(market_data: dict, signal: dict) -> dict:
    """
    Compute a replay confluence score for the direction asserted in *signal*.

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
            "direction_implied": str,          # rules-derived direction
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
    component_availability = _available_component_weights(ohlcv, indicators, fundamental)

    raw_total = sum(components.values())
    available_total = sum(component_availability.values())
    if available_total <= 0:
        normalised = 0
    else:
        # Historical replay can legitimately miss macro/news feeds, so score only
        # against the buckets that were actually available for this snapshot.
        normalised = round(min(100, (raw_total / available_total) * 100))
    unavailable_components = [
        name for name, max_points in component_availability.items() if max_points == 0
    ]

    return {
        "confluence_score":  normalised,
        "direction_implied": _implied_direction(ohlcv, fundamental),
        "component_scores":  components,
        "available_component_points": available_total,
        "unavailable_components": unavailable_components,
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
           Mitigated order blocks receive zero points.
    """
    if direction == "BUY":
        ob_str = str(indicators.get("bullish_ob") or "")
    elif direction == "SELL":
        ob_str = str(indicators.get("bearish_ob") or "")
    else:
        return 0

    ob_text = ob_str.lower()
    if "mitigated" in ob_text:
        return 0
    return _OB_MAX if "valid" in ob_text else 0


def _score_fvg(direction: str, indicators: dict) -> int:
    """
    FAIR VALUE GAP (max 15)
      +15  Relevant FVG identified and still unfilled.
      +8   Gap has been partially filled.
      +0   No FVG or the gap has been fully filled.
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
    fvg_text = fvg_str.lower()
    if fvg_text.startswith(none_indicators) or "filled" in fvg_text and "unfilled" not in fvg_text and "partial" not in fvg_text:
        return 0
    if "partial" in fvg_text:
        return round(_FVG_MAX / 2)
    if "unfilled" in fvg_text:
        return _FVG_MAX
    return 0


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
    if risk in {"LOW", "CLEAR"}:
        return _NEWS_MAX

    minutes = _time_to_event_minutes(fundamental.get("time_to_event"))
    if minutes is not None and minutes > 240:
        return _NEWS_MAX

    return 0


# ---------------------------------------------------------------------------
# Direction implied — independent of Claude's signal
# ---------------------------------------------------------------------------

def _implied_direction(ohlcv: dict, fundamental: dict) -> str:
    """
    Determine the market-implied direction using only replay rules.
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


def _time_to_event_minutes(value) -> float | None:
    minutes = _to_float(value)
    if minutes is not None:
        return minutes
    if value is None:
        return None
    return relative_minutes(str(value))


def _available_component_weights(ohlcv: dict, indicators: dict, fundamental: dict) -> dict[str, int]:
    return {
        "trend_alignment": _TREND_MAX if _trend_available(ohlcv) else 0,
        "order_block": _OB_MAX if _order_block_available(indicators) else 0,
        "fvg": _FVG_MAX if _fvg_available(indicators) else 0,
        "liquidity_sweep": _SWEEP_MAX if "recent_liquidity_sweep" in indicators else 0,
        "premium_discount": _PD_MAX if "premium_discount_zone" in indicators else 0,
        "ote": _OTE_MAX if "ote_zone" in indicators else 0,
        "rsi": _RSI_MAX if ("rsi_4h" in indicators or "rsi_1h" in indicators) else 0,
        "adx": _ADX_MAX if "adx_4h" in indicators else 0,
        "ema": _EMA_MAX if ("ema20_4h" in indicators and "ema50_4h" in indicators) else 0,
        "rate_differential": _RATE_DIFF_MAX if _rate_data_available(fundamental) else 0,
        "dxy": _DXY_MAX if _dxy_data_available(fundamental) else 0,
        "cot": _COT_MAX if _cot_data_available(fundamental) else 0,
        "news_clear": _NEWS_MAX if _news_data_available(fundamental) else 0,
    }


def _trend_available(ohlcv: dict) -> bool:
    required = ("weekly_trend", "daily_trend", "h4_trend")
    return all(key in ohlcv for key in required)


def _order_block_available(indicators: dict) -> bool:
    return "bullish_ob" in indicators or "bearish_ob" in indicators


def _fvg_available(indicators: dict) -> bool:
    return "bullish_fvg" in indicators or "bearish_fvg" in indicators


def _rate_data_available(fundamental: dict) -> bool:
    return _to_float(fundamental.get("usd_rate")) is not None and _to_float(
        fundamental.get("pair_rate")
    ) is not None


def _dxy_data_available(fundamental: dict) -> bool:
    if _to_float(fundamental.get("dxy_level")) is not None:
        return True
    return str(fundamental.get("dxy_direction") or "").upper() in {"RISING", "FALLING"}


def _cot_data_available(fundamental: dict) -> bool:
    if _to_float(fundamental.get("cot_net")) is not None:
        return True
    return str(fundamental.get("cot_bias") or "").upper() in {"BULLISH", "BEARISH"}


def _news_data_available(fundamental: dict) -> bool:
    risk = str(fundamental.get("news_risk") or "").upper()
    event_name = str(
        fundamental.get("next_news_event") or fundamental.get("next_event_name") or ""
    ).upper()
    if _time_to_event_minutes(fundamental.get("time_to_event")) is not None:
        return True
    if risk in {"LOW", "MEDIUM", "HIGH", "CLEAR"}:
        return True
    if event_name and event_name not in {HISTORICAL_NEWS_UNAVAILABLE, "N/A"}:
        return True
    return False
