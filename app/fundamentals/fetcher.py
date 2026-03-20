# =============================================================================
# fetcher.py — cached public façade for live fundamentals
#
# Responsibilities:
#   - maintain module-level caches
#   - expose stable public fetch_* API used across the app/tests
#   - aggregate all source fetchers into get_auto_fundamentals()
#
# Source-specific scraping/parsing lives in:
#   - app.fundamentals.common
#   - app.fundamentals.providers
# =============================================================================

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from app.fundamentals.common import (
    cache_fresh as _cache_fresh,
    classify_news_risk as _classify_news_risk,
    format_rate_differential as _format_rate_differential,
    humanize_delta as _humanize_delta,
    is_high_impact_event as _is_high_impact_event,
    parse_utc as _parse_utc,
    relative_minutes as _relative_minutes,
)
from app.fundamentals.providers import (
    build_calendar_snapshot,
    build_cot_eur_snapshot,
    build_dxy_snapshot,
    build_policy_rates_snapshot,
    build_recent_fx_headline_snapshot,
    build_retail_sentiment_snapshot,
    build_risk_sentiment_snapshot,
)

logger = logging.getLogger(__name__)

# In-memory caches — avoids hitting external services on every loop iteration
_dxy_cache: dict = {}
_dxy_cache_time: datetime | None = None
_cot_cache: dict = {}
_cot_cache_time: datetime | None = None
_calendar_cache: dict = {}
_calendar_cache_time: datetime | None = None
_news_cache: dict = {}
_news_cache_time: datetime | None = None
_risk_cache: dict = {}
_risk_cache_time: datetime | None = None
_sentiment_cache: dict = {}
_sentiment_cache_time: datetime | None = None
_rates_cache: dict = {}
_rates_cache_time: datetime | None = None

_DXY_CACHE_MINUTES = 5
_CALENDAR_CACHE_HOURS = int(os.getenv("FOREX_FACTORY_CALENDAR_CACHE_HOURS", "24"))
_NEWS_CACHE_MINUTES = 10
_COT_CACHE_HOURS = 12
_RISK_CACHE_MINUTES = 5
_SENTIMENT_CACHE_MINUTES = 30
_RATES_CACHE_HOURS = int(os.getenv("POLICY_RATES_CACHE_HOURS", "2"))


def fetch_policy_rates(force_refresh: bool = False) -> dict:
    """
    Fetch USD and EUR policy rates from official public sources.

    USD: official Fed open-market page target range
    EUR: official ECB key-rates webpage
    """
    global _rates_cache, _rates_cache_time

    now = datetime.now(timezone.utc)
    if not force_refresh and _rates_cache and _cache_fresh(
        _rates_cache_time, timedelta(hours=_RATES_CACHE_HOURS)
    ):
        return _rates_cache

    print("  📥 Fetching policy rates from Fed and ECB webpages...")
    result = build_policy_rates_snapshot(now)
    if not result:
        return {}

    _rates_cache = result
    _rates_cache_time = now

    status_parts = []
    if result["usd_rate"] is not None:
        status_parts.append(
            f"USD {result['usd_rate']:.2f}% "
            f"({result['fed_target_lower_rate']:.2f}-{result['fed_target_upper_rate']:.2f})"
        )
    if result["eur_rate"] is not None:
        status_parts.append(
            f"ECB deposit {result['eur_rate']:.2f}% | "
            f"MRO {result['ecb_main_refi_rate']:.2f}% | "
            f"MLF {result['ecb_marginal_lending_rate']:.2f}%"
        )
    if result["rate_differential_value"] is not None:
        status_parts.append(f"Diff {result['rate_differential_value']:+.2f}%")
    print(f"  ✅ Rates: {' | '.join(status_parts)}")
    return result


def fetch_dxy(force_refresh: bool = False) -> dict:
    """
    Fetch an intraday US Dollar Index (DXY) signal from Yahoo Finance.
    Ticker: DX-Y.NYB (ICE Dollar Index Futures, continuous)
    """
    global _dxy_cache, _dxy_cache_time

    now = datetime.now(timezone.utc)
    if not force_refresh and _dxy_cache and _cache_fresh(
        _dxy_cache_time, timedelta(minutes=_DXY_CACHE_MINUTES)
    ):
        return _dxy_cache

    print("  📥 Fetching DXY from Yahoo Finance...")
    result = build_dxy_snapshot(now)
    if not result:
        return {}

    _dxy_cache = result
    _dxy_cache_time = now

    arrow = "↑" if result["direction"] == "RISING" else ("↓" if result["direction"] == "FALLING" else "→")
    print(
        f"  ✅ DXY: {result['level']} {arrow} {result['direction']}  "
        f"(1h MA: {result['ma_1h']}, 15m: {result['pct_15m']:+.2f}%, vs MA: {result['pct_vs_ma']:+.2f}%)"
    )
    return result


def fetch_cot_eur(force_refresh: bool = False) -> dict:
    """
    Return the latest EUR FX COT positioning from the CFTC public futures report.
    """
    global _cot_cache, _cot_cache_time

    now = datetime.now(timezone.utc)
    if not force_refresh and _cot_cache and _cache_fresh(
        _cot_cache_time, timedelta(hours=_COT_CACHE_HOURS)
    ):
        return _cot_cache

    print("  📥 Fetching COT data from CFTC.gov...")
    result = build_cot_eur_snapshot(now)
    if not result:
        return {}

    _cot_cache = result
    _cot_cache_time = now
    print(
        f"  ✅ COT: {result['bias']} | Asset Mgr net: {result['net_str']} | "
        f"Hedge Funds: {result['lm_str']} (as of {result['as_of']})"
    )
    return result


def fetch_next_calendar_event(force_refresh: bool = False) -> dict:
    """
    Fetch the next high-impact USD or EUR event from Forex Factory.
    Checks this week and next week so Friday runs never hit a gap.
    """
    global _calendar_cache, _calendar_cache_time

    if not force_refresh and _calendar_cache and _cache_fresh(
        _calendar_cache_time, timedelta(hours=_CALENDAR_CACHE_HOURS)
    ):
        return _calendar_cache

    now = datetime.now(timezone.utc)
    print("  📥 Fetching economic calendar from Forex Factory...")
    result = build_calendar_snapshot(now)
    if not result:
        return {}

    _calendar_cache = result
    _calendar_cache_time = now

    if result["news_risk"] == "CLEAR":
        print("  ✅ Calendar: clear — no high-impact USD/EUR event upcoming")
    else:
        event_time = result.get("event_time", "")
        event_suffix = ""
        if event_time:
            try:
                parsed = datetime.fromisoformat(event_time)
                event_suffix = f" ({parsed.strftime('%Y-%m-%d %H:%M UTC')})"
            except ValueError:
                event_suffix = ""
        print(
            f"  ✅ Calendar: {result['next_event_name']} in {result['time_to_event']}{event_suffix}"
        )
    return result


def fetch_recent_fx_headline(force_refresh: bool = False) -> dict:
    """
    Fetch the most recent FX-relevant headline.
    Tries Finnhub first, then NewsAPI.
    """
    global _news_cache, _news_cache_time

    if not force_refresh and _news_cache and _cache_fresh(
        _news_cache_time, timedelta(minutes=_NEWS_CACHE_MINUTES)
    ):
        return _news_cache

    has_finnhub = bool(os.getenv("FINNHUB_API_KEY", "").strip())
    has_newsapi = bool(os.getenv("NEWS_API_KEY", "").strip())
    if has_finnhub:
        print("  📥 Fetching FX headlines from Finnhub...")
    elif has_newsapi:
        print("  📥 Fetching FX headlines from NewsAPI...")

    result = build_recent_fx_headline_snapshot()
    if not result:
        return {}

    _news_cache = result
    _news_cache_time = datetime.now(timezone.utc)
    title = result["headline"]
    source_name = result.get("source", "unknown")
    print(f"  ✅ News: {title[:90]}{'...' if len(title) > 90 else ''} ({source_name})")
    return result


def fetch_risk_sentiment(force_refresh: bool = False) -> dict:
    """
    Use SPY intraday movement as a lightweight risk sentiment proxy.
    Returns RISK_ON, RISK_OFF, or NEUTRAL.
    """
    global _risk_cache, _risk_cache_time

    now = datetime.now(timezone.utc)
    if not force_refresh and _risk_cache and _cache_fresh(
        _risk_cache_time, timedelta(minutes=_RISK_CACHE_MINUTES)
    ):
        return _risk_cache

    print("  📥 Fetching S&P 500 risk sentiment from Yahoo Finance...")
    result = build_risk_sentiment_snapshot(now)
    if not result:
        return {}

    _risk_cache = result
    _risk_cache_time = now
    print(
        f"  ✅ Risk sentiment: {result['risk_sentiment']} "
        f"(SPY {result['level']}, 15m: {result['pct_15m']:+.2f}%, vs MA: {result['pct_vs_ma']:+.2f}%)"
    )
    return result


def fetch_retail_sentiment(force_refresh: bool = False) -> dict:
    """
    Fetch EUR/USD retail positioning from OANDA's public position book.
    Uses OANDA_API_KEY — no additional credentials needed.
    """
    global _sentiment_cache, _sentiment_cache_time

    now = datetime.now(timezone.utc)
    if not force_refresh and _sentiment_cache and _cache_fresh(
        _sentiment_cache_time, timedelta(minutes=_SENTIMENT_CACHE_MINUTES)
    ):
        return _sentiment_cache

    print("  📥 Fetching EUR/USD retail positioning from OANDA position book...")
    result = build_retail_sentiment_snapshot()
    if not result:
        return {}

    _sentiment_cache = result
    _sentiment_cache_time = now
    print(f"  ✅ Retail sentiment: {result['sentiment']}")
    return result


def get_auto_fundamentals(
    eur_usd_daily_trend: str = "NEUTRAL",
    eur_usd_h4_trend: str = "NEUTRAL",
) -> dict:
    """
    Build the fundamental override dict for market_data injection.

    Priority for each field:
      1. Auto-fetched (rates / yfinance / CFTC / Forex Factory / OANDA / news feeds)
      2. MANUAL_CHECK (live source unavailable — user must intervene)
    """

    def _resolve_future(name: str, future) -> dict:
        try:
            result = future.result()
        except Exception as exc:
            logger.warning(f"{name} fetch raised unexpectedly: {exc}")
            return {}
        return result or {}

    with ThreadPoolExecutor(max_workers=7) as executor:
        futures = {
            "rates": executor.submit(fetch_policy_rates),
            "dxy": executor.submit(fetch_dxy),
            "cot": executor.submit(fetch_cot_eur),
            "calendar": executor.submit(fetch_next_calendar_event),
            "news": executor.submit(fetch_recent_fx_headline),
            "retail_sentiment": executor.submit(fetch_retail_sentiment),
            "risk_sentiment": executor.submit(fetch_risk_sentiment),
        }

        rates = _resolve_future("policy rates", futures["rates"])
        dxy = _resolve_future("dxy", futures["dxy"])
        cot = _resolve_future("cot", futures["cot"])
        calendar = _resolve_future("calendar", futures["calendar"])
        news = _resolve_future("news", futures["news"])
        sentiment_data = _resolve_future("retail sentiment", futures["retail_sentiment"])
        risk = _resolve_future("risk sentiment", futures["risk_sentiment"])

    if rates:
        usd_rate = rates["usd_rate"]
        fed_target_lower_rate = rates["fed_target_lower_rate"]
        fed_target_upper_rate = rates["fed_target_upper_rate"]
        eur_rate = rates["eur_rate"]
        ecb_main_refi_rate = rates["ecb_main_refi_rate"]
        ecb_marginal_lending_rate = rates["ecb_marginal_lending_rate"]
        ecb_deposit_rate = rates["ecb_deposit_rate"]
        rate_differential = rates["rate_differential"]
        rates_source = rates["source"]
    else:
        usd_rate = None
        fed_target_lower_rate = None
        fed_target_upper_rate = None
        eur_rate = None
        ecb_main_refi_rate = None
        ecb_marginal_lending_rate = None
        ecb_deposit_rate = None
        rate_differential = "N/A (live Fed/ECB data unavailable)"
        rates_source = "unavailable"

    if dxy:
        dxy_direction = dxy["direction"]
        dxy_lvl_str = str(dxy["level"])
    else:
        # Keep enum-typed fields as valid enum values so Claude's prompt stays clean.
        dxy_direction = "NEUTRAL"
        dxy_lvl_str = "N/A"

    if cot:
        cot_bias = cot["bias"]
        cot_net = (
            f"Asset Mgr: {cot['net_str']} | "
            f"Hedge Funds: {cot['lm_str']} (as of {cot['as_of']})"
        )
    else:
        cot_bias = "NEUTRAL"
        cot_net = "N/A (CFTC unavailable)"

    if calendar:
        next_event_name = calendar.get("next_event_name", "")
        next_news_event = calendar.get("next_news_event", next_event_name)
        time_to_event = calendar.get("time_to_event")
        news_risk = calendar.get("news_risk", "LOW")
    else:
        # "MANUAL_CHECK" prefix is intentional — agent.py detects it and hard-blocks
        # trading when the calendar is unavailable (fail-closed). Keep it short so
        # the validator's startswith("MANUAL_CHECK") check still fires, but don't
        # inject a long warning string into Claude's prompt.
        next_event_name = "MANUAL_CHECK"
        next_news_event = "MANUAL_CHECK"
        time_to_event = None
        news_risk = "HIGH"

    if news:
        recent_headline = news["headline"]
    else:
        recent_headline = "None available"

    if sentiment_data:
        sentiment = sentiment_data["sentiment"]
    else:
        sentiment = "NEUTRAL"

    if risk:
        risk_sentiment = risk["risk_sentiment"]
    else:
        risk_sentiment = "NEUTRAL"

    return {
        "usd_rate": usd_rate,
        "fed_target_lower_rate": fed_target_lower_rate,
        "fed_target_upper_rate": fed_target_upper_rate,
        "eur_rate": eur_rate,
        "ecb_main_refi_rate": ecb_main_refi_rate,
        "ecb_marginal_lending_rate": ecb_marginal_lending_rate,
        "ecb_deposit_rate": ecb_deposit_rate,
        "rate_differential": rate_differential,
        "rates_source": rates_source,
        "dxy_direction": dxy_direction,
        "dxy_level": dxy_lvl_str,
        "cot_bias": cot_bias,
        "cot_net": cot_net,
        "retail_sentiment": sentiment,
        "next_event_name": next_event_name,
        "next_news_event": next_news_event,
        "time_to_event": time_to_event,
        "news_risk": news_risk,
        "recent_headline": recent_headline,
        "risk_sentiment": risk_sentiment,
    }


if __name__ == "__main__":
    print("=" * 60)
    print("FUNDAMENTALS FETCHER TEST")
    print("=" * 60)

    print("\n--- Policy Rates (Fed + ECB webpages) ---")
    rates = fetch_policy_rates(force_refresh=True)
    if rates:
        print(f"  USD rate:   {rates['usd_rate']}")
        print(f"  EUR rate:   {rates['eur_rate']}")
        print(f"  Diff:       {rates['rate_differential']}")
        print(f"  Source:     {rates['source']}")
    else:
        print("  Failed — check access to Fed/ECB pages")

    print("\n--- DXY (yfinance) ---")
    dxy = fetch_dxy(force_refresh=True)
    if dxy:
        print(f"  Level:     {dxy['level']}")
        print(f"  Direction: {dxy['direction']}")
        print(f"  1h MA:     {dxy['ma_1h']}")
        print(f"  vs MA:     {dxy['pct_vs_ma']:+.3f}%")
        print(f"  15m move:  {dxy['pct_15m']:+.3f}%")
    else:
        print("  Failed — check: pip install yfinance")

    print("\n--- COT (CFTC.gov) ---")
    cot = fetch_cot_eur(force_refresh=True)
    if cot:
        print(f"  Bias:           {cot['bias']}")
        print(f"  Asset Mgr net:  {cot['net_str']}")
        print(f"  Hedge Funds:    {cot['lm_str']}")
        print(f"  As of:          {cot['as_of']}")
    else:
        print("  Failed — check internet connection")

    print("\n--- Calendar (Forex Factory) ---")
    cal = fetch_next_calendar_event(force_refresh=True)
    if cal:
        print(f"  Next event: {cal.get('next_event_name')}")
        print(f"  Time:       {cal.get('time_to_event')}")
        print(f"  Risk:       {cal.get('news_risk')}")
    else:
        print("  Failed — check internet connection")

    print("\n--- Full fundamentals dict ---")
    result = get_auto_fundamentals("BULLISH", "BULLISH")
    print(json.dumps(result, indent=2))
