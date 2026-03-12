# =============================================================================
# fundamentals_fetcher.py — Auto-fetch live fundamentals
#
# Sources:
#   DXY:  yfinance → DX-Y.NYB (ICE US Dollar Index Futures)
#         Intraday 5m/1h signal, refreshed every 5 minutes
#
#   COT:  CFTC.gov public ZIP (Legacy Futures Only)
#         Published every Friday at 3:30 PM EST
#         Weekly macro positioning, not real-time
#
#   Calendar: Forex Factory public JSON (no API key required)
#         Next high-impact USD / EUR event
#         https://nfs.faireconomy.media/ff_calendar_thisweek.json
#
#   News: Finnhub (primary) → NewsAPI (fallback)
#         Latest FX-relevant headline — requires FINNHUB_API_KEY or NEWS_API_KEY
#
#   Risk sentiment: Yahoo Finance SPY intraday
#         S&P 500 proxy for risk-on / risk-off tone
#
# Manual overrides (env vars take priority over auto-fetched):
#   export DXY_DIRECTION="FALLING"
#   export DXY_LEVEL="104.20"
#   export COT_BIAS="BULLISH"
#   export COT_NET="+18500"
#   export RETAIL_SENTIMENT="72% SHORT"   ← manual override; auto-fetched from OANDA position book
#   export FINNHUB_API_KEY="your_finnhub_key"                # enables live headlines (primary)
#   export NEWS_API_KEY="your_newsapi_key"                   # enables live headlines (fallback)
#
# Install: pip install yfinance
# =============================================================================

import io
import csv
import logging
import os
import re
import zipfile
from datetime import datetime, timedelta, timezone

import requests

logger = logging.getLogger(__name__)

# In-memory caches — avoids hitting external services on every loop iteration
_dxy_cache:           dict            = {}
_dxy_cache_time:      datetime | None = None
_cot_cache:           dict            = {}
_cot_cache_time:      datetime | None = None
_calendar_cache:      dict            = {}
_calendar_cache_time: datetime | None = None
_news_cache:          dict            = {}
_news_cache_time:     datetime | None = None
_risk_cache:          dict            = {}
_risk_cache_time:     datetime | None = None
_sentiment_cache:     dict            = {}
_sentiment_cache_time: datetime | None = None

_DXY_CACHE_MINUTES        = 5
_CALENDAR_CACHE_MINUTES   = 5
_NEWS_CACHE_MINUTES       = 10
_COT_CACHE_HOURS          = 12
_RISK_CACHE_MINUTES       = 5
_SENTIMENT_CACHE_MINUTES  = 30


def _cache_fresh(cached_at: datetime | None, max_age: timedelta) -> bool:
    return cached_at is not None and (datetime.now(timezone.utc) - cached_at) < max_age


def _parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None

    cleaned = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(cleaned)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _humanize_delta(target_time: datetime, now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    total_minutes = int((target_time - now).total_seconds() // 60)
    is_past = total_minutes < 0
    total_minutes = abs(total_minutes)
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        text = f"{hours} hour{'s' if hours != 1 else ''} {minutes} minutes"
    elif hours:
        text = f"{hours} hour{'s' if hours != 1 else ''}"
    else:
        text = f"{minutes} minutes"
    return f"{text} ago" if is_past else text


def _relative_minutes(value: str | None) -> int | None:
    if not value:
        return None

    text = value.lower()
    mins_match = re.search(r"(\d+)\s*min", text)
    hours_match = re.search(r"(\d+)\s*hour", text)
    total_minutes = 0
    if hours_match:
        total_minutes += int(hours_match.group(1)) * 60
    if mins_match:
        total_minutes += int(mins_match.group(1))
    if total_minutes == 0:
        return None
    return -total_minutes if "ago" in text else total_minutes


def _classify_news_risk(event_name: str, time_to_event: str | None) -> str:
    if event_name.startswith("CLEAR"):
        return "CLEAR"
    if event_name.startswith("MANUAL_CHECK"):
        return "HIGH"

    minutes = _relative_minutes(time_to_event)
    if minutes is None:
        return "LOW"
    if -30 <= minutes <= 30:
        return "HIGH"
    if 30 < minutes <= 240:
        return "MEDIUM"
    return "LOW"


def _is_high_impact_event(event_name: str, raw_importance) -> bool:
    text = str(raw_importance or "").strip().lower()
    if text:
        if text.isdigit() and int(text) >= 3:
            return True
        if text in {"high", "3", "high impact"}:
            return True

    lowered = event_name.lower()
    high_impact_keywords = (
        "nonfarm", "nfp", "cpi", "consumer price", "fomc", "interest rate",
        "rate decision", "ecb", "gdp", "retail sales", "pmi", "ifo",
        "powell", "lagarde", "payrolls", "inflation"
    )
    return any(keyword in lowered for keyword in high_impact_keywords)


# =============================================================================
# DXY — via yfinance
# =============================================================================

def fetch_dxy(force_refresh: bool = False) -> dict:
    """
    Fetches an intraday US Dollar Index (DXY) signal from Yahoo Finance.
    Ticker: DX-Y.NYB  (ICE Dollar Index Futures, continuous)

    Returns:
        {
            "level":     float,   current DXY price
            "direction": str,     RISING | FALLING | NEUTRAL
            "ma_1h":     float,   1-hour intraday moving average
            "pct_vs_ma": float,   % above/below 1-hour MA
            "pct_15m":   float,   % move over last 15 minutes
            "source":    str
        }
    Returns empty dict on failure.
    """
    global _dxy_cache, _dxy_cache_time

    now = datetime.now(timezone.utc)
    if not force_refresh and _dxy_cache and _cache_fresh(
        _dxy_cache_time, timedelta(minutes=_DXY_CACHE_MINUTES)
    ):
            return _dxy_cache

    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not installed — run: pip install yfinance")
        return {}

    try:
        print("  📥 Fetching DXY from Yahoo Finance...")
        ticker = yf.Ticker("DX-Y.NYB")
        hist = ticker.history(
            period="2d",
            interval="5m",
            auto_adjust=True,
            prepost=True,
        )

        if hist.empty or len(hist) < 12:
            hist = ticker.history(
                period="5d",
                interval="60m",
                auto_adjust=True,
                prepost=True,
            )
            if hist.empty or len(hist) < 6:
                logger.warning("DXY: insufficient intraday data returned from yfinance")
                return {}

        current = round(float(hist["Close"].iloc[-1]), 2)
        ma_window = min(12, len(hist))
        ma_1h = round(float(hist["Close"].tail(ma_window).mean()), 2)

        if len(hist) >= 4:
            ref_15m = float(hist["Close"].iloc[-4])
        else:
            ref_15m = float(hist["Close"].iloc[0])

        pct_vs_ma = round((current - ma_1h) / ma_1h * 100, 3) if ma_1h else 0.0
        pct_15m = round((current - ref_15m) / ref_15m * 100, 3) if ref_15m else 0.0

        if pct_vs_ma > 0.05 and pct_15m >= -0.03:
            direction = "RISING"
        elif pct_vs_ma < -0.05 and pct_15m <= 0.03:
            direction = "FALLING"
        else:
            direction = "NEUTRAL"

        result = {
            "level":     current,
            "direction": direction,
            "ma_1h":     ma_1h,
            "pct_vs_ma": pct_vs_ma,
            "pct_15m":   pct_15m,
            "source":    f"yfinance DX-Y.NYB @ {now.strftime('%Y-%m-%d %H:%M UTC')}",
        }

        _dxy_cache      = result
        _dxy_cache_time = now

        arrow = "↑" if direction == "RISING" else ("↓" if direction == "FALLING" else "→")
        print(
            f"  ✅ DXY: {current} {arrow} {direction}  "
            f"(1h MA: {ma_1h}, 15m: {pct_15m:+.2f}%, vs MA: {pct_vs_ma:+.2f}%)"
        )
        return result

    except Exception as e:
        logger.warning(f"DXY fetch failed: {e}")
        return {}


# =============================================================================
# COT — CFTC.gov public download
# =============================================================================

def fetch_cot_eur(force_refresh: bool = False) -> dict:
    """
    Downloads the CFTC Disaggregated Financial Futures COT report
    and returns EUR FX (EURO FX - CHICAGO MERCANTILE EXCHANGE) positioning.

    URL format: https://www.cftc.gov/files/dea/history/fut_fin_txt_{YEAR}.zip

    Uses Asset Manager net position as the primary bias signal:
      Asset Managers = institutional investors (pension funds, mutual funds)
      They are the dominant EUR/USD positioning group.

      net > +50,000 → BULLISH  (institutions net long EUR)
      net < -50,000 → BEARISH  (institutions net short EUR)
      in between    → NEUTRAL

    Also tracks Leveraged Money (hedge funds) as a secondary signal.

    Returns:
        {
            "bias":          str,  BULLISH | BEARISH | NEUTRAL
            "net_asset_mgr": int,  asset manager net contracts
            "net_lev_money": int,  leveraged money (hedge fund) net
            "net_str":       str,  "+370,272" formatted
            "as_of":         str,  "YYYY-MM-DD"
            "source":        str
        }
    Returns empty dict on failure.
    """
    global _cot_cache, _cot_cache_time

    now = datetime.now(timezone.utc)
    if not force_refresh and _cot_cache and _cache_fresh(
        _cot_cache_time, timedelta(hours=_COT_CACHE_HOURS)
    ):
            return _cot_cache

    year = now.year
    url  = f"https://www.cftc.gov/files/dea/history/fut_fin_txt_{year}.zip"
    print("  📥 Fetching COT data from CFTC.gov...")

    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
    except requests.exceptions.Timeout:
        logger.warning("COT fetch timed out")
        return {}
    except requests.exceptions.RequestException as e:
        logger.warning(f"COT fetch failed: {e}")
        return {}

    try:
        with zipfile.ZipFile(io.BytesIO(response.content)) as z:
            fname = z.namelist()[0]
            with z.open(fname) as raw:
                reader = csv.DictReader(io.TextIOWrapper(raw, encoding="utf-8"))
                for row in reader:
                    market = row.get("Market_and_Exchange_Names", "")
                    if "EURO FX" not in market.upper():
                        continue

                    def _int(key: str) -> int:
                        v = row.get(key, "0").strip()
                        return int(v) if v.lstrip("+-").isdigit() else 0

                    am_long  = _int("Asset_Mgr_Positions_Long_All")
                    am_short = _int("Asset_Mgr_Positions_Short_All")
                    lm_long  = _int("Lev_Money_Positions_Long_All")
                    lm_short = _int("Lev_Money_Positions_Short_All")

                    net_am = am_long - am_short
                    net_lm = lm_long - lm_short
                    as_of  = row.get("Report_Date_as_YYYY-MM-DD", "unknown").strip()

                    # Primary bias from Asset Managers (dominant institutional group)
                    if net_am > 50_000:
                        bias = "BULLISH"
                    elif net_am < -50_000:
                        bias = "BEARISH"
                    else:
                        bias = "NEUTRAL"

                    net_str = f"+{net_am:,}" if net_am >= 0 else f"{net_am:,}"
                    lm_str  = f"+{net_lm:,}" if net_lm >= 0 else f"{net_lm:,}"

                    result = {
                        "bias":          bias,
                        "net_asset_mgr": net_am,
                        "net_lev_money": net_lm,
                        "net_str":       net_str,
                        "lm_str":        lm_str,
                        "as_of":         as_of,
                        "source":        f"CFTC Disaggregated Financial Futures — as of {as_of}",
                    }

                    _cot_cache      = result
                    _cot_cache_time = now

                    print(f"  ✅ COT: {bias} | Asset Mgr net: {net_str} | "
                          f"Hedge Funds: {lm_str} (as of {as_of})")
                    return result

    except Exception as e:
        logger.error(f"COT parse error: {e}")
        return {}

    logger.warning("COT: EURO FX row not found in CFTC file")
    return {}


def fetch_next_calendar_event(force_refresh: bool = False) -> dict:
    """
    Fetches the next high-impact USD or EUR event from Forex Factory.
    No API key required.

    Checks this week and next week so Friday runs never hit a gap.
    Filters: country USD or EUR + impact == "High" only.
    """
    global _calendar_cache, _calendar_cache_time

    if not force_refresh and _calendar_cache and _cache_fresh(
        _calendar_cache_time, timedelta(minutes=_CALENDAR_CACHE_MINUTES)
    ):
        return _calendar_cache

    now = datetime.now(timezone.utc)
    candidates: list[dict] = []

    urls = [
        "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
        "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
    ]

    print("  📥 Fetching economic calendar from Forex Factory...")
    for url in urls:
        try:
            response = requests.get(url, timeout=15)
            response.raise_for_status()
            events = response.json()
        except requests.exceptions.RequestException as exc:
            logger.warning(f"Forex Factory calendar fetch failed ({url}): {exc}")
            continue
        except Exception as exc:
            logger.warning(f"Forex Factory calendar parse failed: {exc}")
            continue

        for item in events:
            country = item.get("country", "").strip().upper()
            if country not in {"USD", "EUR"}:
                continue

            if item.get("impact", "").strip() != "High":
                continue

            event_time = _parse_utc(item.get("date"))
            if event_time is None:
                continue
            if event_time < now - timedelta(minutes=30):
                continue

            candidates.append({
                "country": country,
                "event":      item.get("title", "Unknown event").strip(),
                "event_time": event_time,
            })

    if not candidates:
        result = {
            "next_event_name": "CLEAR — no high-impact USD/EUR event in calendar",
            "next_news_event": "CLEAR — no high-impact USD/EUR event in calendar",
            "time_to_event":   None,
            "news_risk":       "CLEAR",
            "source":          "Forex Factory calendar",
        }
        _calendar_cache      = result
        _calendar_cache_time = now
        print("  ✅ Calendar: clear — no high-impact USD/EUR event upcoming")
        return result

    next_event    = min(candidates, key=lambda e: abs((e["event_time"] - now).total_seconds()))
    event_name    = f"{next_event['country']} — {next_event['event']}"
    time_to_event = _humanize_delta(next_event["event_time"], now)
    news_risk     = _classify_news_risk(event_name, time_to_event)

    result = {
        "next_event_name": event_name,
        "next_news_event": event_name,
        "time_to_event":   time_to_event,
        "news_risk":       news_risk,
        "event_time":      next_event["event_time"].isoformat(),
        "source":          "Forex Factory calendar",
    }
    _calendar_cache      = result
    _calendar_cache_time = now
    print(
        f"  ✅ Calendar: {event_name} in {time_to_event} "
        f"({next_event['event_time'].strftime('%Y-%m-%d %H:%M UTC')})"
    )
    return result


def fetch_recent_fx_headline(force_refresh: bool = False) -> dict:
    """
    Fetches the most recent FX-relevant headline.
    Tries Finnhub first (FINNHUB_API_KEY), falls back to NewsAPI (NEWS_API_KEY).
    Returns empty dict when neither key is present.
    """
    global _news_cache, _news_cache_time

    if not force_refresh and _news_cache and _cache_fresh(
        _news_cache_time, timedelta(minutes=_NEWS_CACHE_MINUTES)
    ):
        return _news_cache

    # --- Finnhub (primary) ---
    finnhub_key = os.getenv("FINNHUB_API_KEY", "").strip()
    if finnhub_key:
        try:
            print("  📥 Fetching FX headlines from Finnhub...")
            response = requests.get(
                "https://finnhub.io/api/v1/news",
                params={"category": "forex", "token": finnhub_key},
                timeout=15,
            )
            response.raise_for_status()
            articles = response.json()
            for article in articles:
                title = (article.get("headline") or "").strip()
                if not title:
                    continue
                source_name = (article.get("source") or "Finnhub").strip()
                published_at = datetime.fromtimestamp(
                    article["datetime"], tz=timezone.utc
                ).isoformat() if article.get("datetime") else None
                result = {
                    "headline": title,
                    "published_at": published_at,
                    "source": source_name,
                }
                _news_cache = result
                _news_cache_time = datetime.now(timezone.utc)
                print(f"  ✅ News: {title[:90]}{'...' if len(title) > 90 else ''} ({source_name})")
                return result
        except requests.exceptions.RequestException as exc:
            logger.warning(f"Finnhub news fetch failed: {exc}")
        except Exception as exc:
            logger.warning(f"Finnhub news parse failed: {exc}")

    # --- NewsAPI (fallback) ---
    newsapi_key = os.getenv("NEWS_API_KEY", "").strip()
    if not newsapi_key:
        return {}

    try:
        print("  📥 Fetching FX headlines from NewsAPI...")
        response = requests.get(
            "https://newsapi.org/v2/everything",
            params={
                "apiKey": newsapi_key,
                "language": "en",
                "sortBy": "publishedAt",
                "pageSize": 10,
                "q": '"EUR/USD" OR forex OR "euro dollar" OR ECB OR "Federal Reserve" OR dollar',
                "domains": "reuters.com,bloomberg.com,cnbc.com,marketwatch.com,wsj.com,ft.com",
            },
            timeout=20,
        )
        response.raise_for_status()
        articles = response.json().get("articles", [])
    except requests.exceptions.RequestException as exc:
        logger.warning(f"NewsAPI fetch failed: {exc}")
        return {}
    except Exception as exc:
        logger.warning(f"NewsAPI parse failed: {exc}")
        return {}

    for article in articles:
        title = (article.get("title") or "").strip()
        if not title:
            continue
        source_name = ((article.get("source") or {}).get("name") or "unknown").strip()
        published_at = _parse_utc(article.get("publishedAt"))
        result = {
            "headline": title,
            "published_at": published_at.isoformat() if published_at else None,
            "source": source_name,
        }
        _news_cache = result
        _news_cache_time = datetime.now(timezone.utc)
        print(f"  ✅ News: {title[:90]}{'...' if len(title) > 90 else ''} ({source_name})")
        return result

    return {}


def fetch_risk_sentiment(force_refresh: bool = False) -> dict:
    """
    Uses SPY intraday movement as a lightweight risk sentiment proxy.
    Returns RISK_ON, RISK_OFF, or NEUTRAL.
    """
    global _risk_cache, _risk_cache_time

    now = datetime.now(timezone.utc)
    if not force_refresh and _risk_cache and _cache_fresh(
        _risk_cache_time, timedelta(minutes=_RISK_CACHE_MINUTES)
    ):
        return _risk_cache

    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not installed — run: pip install yfinance")
        return {}

    try:
        print("  📥 Fetching S&P 500 risk sentiment from Yahoo Finance...")
        ticker = yf.Ticker("SPY")
        hist = ticker.history(
            period="2d",
            interval="5m",
            auto_adjust=True,
            prepost=True,
        )
        if hist.empty or len(hist) < 12:
            return {}

        current = round(float(hist["Close"].iloc[-1]), 2)
        ma_1h = round(float(hist["Close"].tail(12).mean()), 2)
        ref_15m = float(hist["Close"].iloc[-4]) if len(hist) >= 4 else float(hist["Close"].iloc[0])
        pct_vs_ma = round((current - ma_1h) / ma_1h * 100, 3) if ma_1h else 0.0
        pct_15m = round((current - ref_15m) / ref_15m * 100, 3) if ref_15m else 0.0

        if pct_vs_ma > 0.05 and pct_15m >= -0.03:
            sentiment = "RISK_ON"
        elif pct_vs_ma < -0.05 and pct_15m <= 0.03:
            sentiment = "RISK_OFF"
        else:
            sentiment = "NEUTRAL"

        result = {
            "risk_sentiment": sentiment,
            "proxy": "SPY",
            "level": current,
            "pct_vs_ma": pct_vs_ma,
            "pct_15m": pct_15m,
            "source": f"yfinance SPY @ {now.strftime('%Y-%m-%d %H:%M UTC')}",
        }
        _risk_cache = result
        _risk_cache_time = now
        print(
            f"  ✅ Risk sentiment: {sentiment} "
            f"(SPY {current}, 15m: {pct_15m:+.2f}%, vs MA: {pct_vs_ma:+.2f}%)"
        )
        return result
    except Exception as exc:
        logger.warning(f"Risk sentiment fetch failed: {exc}")
        return {}


def fetch_retail_sentiment(force_refresh: bool = False) -> dict:
    """
    Fetches EUR/USD retail positioning from OANDA's public position book.
    Uses OANDA_API_KEY — no additional credentials needed.

    Method: compares long vs short position concentration in the ±50-pip
    zone around the current price. Higher concentration = more retail
    positioned there = contrarian signal against that direction.

    Returns: sentiment string + pct_long/pct_short for the local zone.
    """
    global _sentiment_cache, _sentiment_cache_time

    now = datetime.now(timezone.utc)
    if not force_refresh and _sentiment_cache and _cache_fresh(
        _sentiment_cache_time, timedelta(minutes=_SENTIMENT_CACHE_MINUTES)
    ):
        return _sentiment_cache

    api_key = os.getenv("OANDA_API_KEY", "").strip()
    if not api_key:
        return {}

    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        print("  📥 Fetching EUR/USD retail positioning from OANDA position book...")
        response = requests.get(
            "https://api-fxpractice.oanda.com/v3/instruments/EUR_USD/positionBook",
            headers=headers,
            timeout=15,
        )
        response.raise_for_status()
        book     = response.json().get("positionBook", {})
        buckets  = book.get("buckets", [])
        current  = float(book.get("price", 0))

        if not buckets or current == 0:
            return {}

        # Sum long/short concentration within ±50 pips of current price
        pip_range  = 0.0050
        zone_long  = 0.0
        zone_short = 0.0

        for b in buckets:
            price     = float(b.get("price", 0))
            long_pct  = float(b.get("longCountPercent",  0))
            short_pct = float(b.get("shortCountPercent", 0))
            if abs(price - current) <= pip_range:
                zone_long  += long_pct
                zone_short += short_pct

        total = zone_long + zone_short
        if total == 0:
            return {}

        pct_long  = round(zone_long  / total * 100)
        pct_short = round(zone_short / total * 100)

        if pct_short >= 60:
            signal = "contrarian BULLISH"
        elif pct_long >= 60:
            signal = "contrarian BEARISH"
        else:
            signal = "mixed — no clear contrarian edge"

        sentiment_str = f"{pct_short}% SHORT, {pct_long}% LONG ({signal}) — OANDA position book"

        result = {
            "sentiment": sentiment_str,
            "pct_long":  pct_long,
            "pct_short": pct_short,
            "signal":    signal,
            "source":    "OANDA EUR_USD position book",
        }
        _sentiment_cache      = result
        _sentiment_cache_time = now
        print(f"  ✅ Retail sentiment: {sentiment_str}")
        return result

    except requests.exceptions.RequestException as exc:
        logger.warning(f"OANDA position book fetch failed: {exc}")
        return {}
    except Exception as exc:
        logger.warning(f"OANDA position book parse failed: {exc}")
        return {}


# =============================================================================
# MAIN BUILDER — called from oanda_connector._get_fundamental_data()
# =============================================================================

def get_auto_fundamentals(
    eur_usd_daily_trend: str = "NEUTRAL",
    eur_usd_h4_trend:    str = "NEUTRAL",
) -> dict:
    """
    Builds the fundamental override dict for market_data injection.

    Priority for each field:
      1. Manual env var  (user override, highest trust)
      2. Auto-fetched    (yfinance / CFTC / FMP / NewsAPI)
      3. MANUAL_CHECK    (fallback — agent knows data is missing)

    The daily/4H EUR/USD trends are used only as a fallback if DXY fetch fails
    (EUR/USD and DXY are ~57.6% correlated, inversely).
    """
    # ---- DXY ----
    dxy_env   = os.getenv("DXY_DIRECTION", "").strip().upper()
    dxy_level = os.getenv("DXY_LEVEL",     "").strip()

    if dxy_env:
        dxy_direction = dxy_env
        dxy_lvl_str   = dxy_level or "MANUAL_CHECK — export DXY_LEVEL=104.20"
    else:
        dxy = fetch_dxy()
        if dxy:
            dxy_direction = dxy["direction"]
            dxy_lvl_str   = dxy_level or str(dxy["level"])
        else:
            # Last resort: derive from EUR/USD trend
            daily = eur_usd_daily_trend.upper()
            h4    = eur_usd_h4_trend.upper()
            if daily == "BULLISH":
                dxy_direction = "FALLING"
            elif daily == "BEARISH":
                dxy_direction = "RISING"
            else:
                dxy_direction = "NEUTRAL"
            dxy_lvl_str = "MANUAL_CHECK — yfinance unavailable"
            logger.info(f"DXY derived from EUR/USD trend: {dxy_direction}")

    # ---- COT ----
    cot_bias_env = os.getenv("COT_BIAS", "").strip().upper()
    cot_net_env  = os.getenv("COT_NET",  "").strip()

    if cot_bias_env:
        cot_bias = cot_bias_env
        cot_net  = cot_net_env or "manual override"
    else:
        cot = fetch_cot_eur()
        if cot:
            cot_bias = cot["bias"]
            cot_net  = (f"Asset Mgr: {cot['net_str']} | "
                        f"Hedge Funds: {cot['lm_str']} (as of {cot['as_of']})")
        else:
            cot_bias = "MANUAL_CHECK — export COT_BIAS=BULLISH"
            cot_net  = "MANUAL_CHECK — cftc.gov every Friday 3:30PM EST"

    # ---- Calendar ----
    calendar = fetch_next_calendar_event()
    if calendar:
        next_event_name = calendar.get("next_event_name", "")
        next_news_event = calendar.get("next_news_event", next_event_name)
        time_to_event = calendar.get("time_to_event")
        news_risk = calendar.get("news_risk", "LOW")
    else:
        next_event_name = "MANUAL_CHECK — Forex Factory calendar unavailable"
        next_news_event = next_event_name
        time_to_event = None
        news_risk = "HIGH"

    # ---- News ----
    news = fetch_recent_fx_headline()
    if news:
        recent_headline = news["headline"]
    else:
        recent_headline = "MANUAL_CHECK — set NEWS_API_KEY for live headlines"

    # ---- Retail sentiment ----
    sentiment = os.getenv("RETAIL_SENTIMENT", "").strip()
    if not sentiment:
        sentiment_data = fetch_retail_sentiment()
        if sentiment_data:
            sentiment = sentiment_data["sentiment"]
        else:
            sentiment = "MANUAL_CHECK — export RETAIL_SENTIMENT='72% SHORT' (myfxbook.com/community/outlook)"

    # ---- Risk sentiment ----
    risk = fetch_risk_sentiment()
    if risk:
        risk_sentiment = risk["risk_sentiment"]
    else:
        risk_sentiment = "MANUAL_CHECK — SPY proxy unavailable"

    return {
        "dxy_direction":    dxy_direction,
        "dxy_level":        dxy_lvl_str,
        "cot_bias":         cot_bias,
        "cot_net":          cot_net,
        "retail_sentiment": sentiment,
        "next_event_name":  next_event_name,
        "next_news_event":  next_news_event,
        "time_to_event":    time_to_event,
        "news_risk":        news_risk,
        "recent_headline":  recent_headline,
        "risk_sentiment":   risk_sentiment,
    }


# =============================================================================
# STANDALONE TEST
# =============================================================================

if __name__ == "__main__":
    import json
    print("=" * 60)
    print("FUNDAMENTALS FETCHER TEST")
    print("=" * 60)

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
