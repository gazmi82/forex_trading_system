from __future__ import annotations

import csv
import html
import io
import logging
import os
import re
import zipfile
from datetime import datetime, timedelta, timezone

import requests

from app.fundamentals.common import (
    classify_news_risk,
    format_rate_differential,
    humanize_delta,
    parse_utc,
)

logger = logging.getLogger(__name__)


def _fetch_fed_target_range() -> dict | None:
    response = requests.get(
        "https://www.federalreserve.gov/monetarypolicy/openmarket.htm",
        timeout=20,
    )
    response.raise_for_status()

    text = html.unescape(response.text)
    text = re.sub(r"(?i)</(p|div|tr|table|h\d|li|br|section|article|td|th)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()

    match = re.search(
        r"(\d{4})\s+Date Increase Decrease Level \(%\)\s+"
        r"([A-Za-z]+)\s+(\d{1,2})(?:\*+)?\s+"
        r"([0-9.]+|\.\.\.)\s+([0-9.]+|\.\.\.)\s+"
        r"([0-9.]+(?:-[0-9.]+)?)",
        text,
    )
    if not match:
        return None

    year = match.group(1)
    as_of = f"{match.group(2)} {match.group(3)}, {year}"
    level = match.group(6)
    if "-" in level:
        low_str, high_str = level.split("-", 1)
        lower = round(float(low_str), 2)
        upper = round(float(high_str), 2)
    else:
        lower = upper = round(float(level), 2)

    midpoint = round((lower + upper) / 2, 2)
    return {
        "fed_target_lower_rate": lower,
        "fed_target_upper_rate": upper,
        "usd_rate": midpoint,
        "fed_target_lower_rate_as_of": as_of,
        "fed_target_upper_rate_as_of": as_of,
        "usd_as_of": as_of,
    }


def _fetch_ecb_key_rates() -> dict | None:
    response = requests.get(
        "https://data.ecb.europa.eu/key-figures/ecb-interest-rates-and-exchange-rates/key-ecb-interest-rates",
        timeout=20,
    )
    response.raise_for_status()

    text = html.unescape(response.text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)

    patterns = {
        "ecb_main_refi_rate": r"Main refinancing operations\s+(\d{1,2}\s+\w+\s+\d{4})\s+([0-9]+(?:\.[0-9]+)?)\s*%",
        "ecb_marginal_lending_rate": r"Marginal lending facility\s+(\d{1,2}\s+\w+\s+\d{4})\s+([0-9]+(?:\.[0-9]+)?)\s*%",
        "ecb_deposit_rate": r"Deposit facility\s+(\d{1,2}\s+\w+\s+\d{4})\s+([0-9]+(?:\.[0-9]+)?)\s*%",
    }

    result: dict[str, str | float] = {}
    for field, pattern in patterns.items():
        match = re.search(pattern, text, re.IGNORECASE)
        if not match:
            return None
        result[field] = round(float(match.group(2)), 2)
        result[f"{field}_as_of"] = match.group(1)

    return result


def build_policy_rates_snapshot(now: datetime) -> dict:
    try:
        fed_rates = _fetch_fed_target_range()
        ecb_rates = _fetch_ecb_key_rates()
        result = {
            "usd_rate": None,
            "fed_target_lower_rate": None,
            "fed_target_upper_rate": None,
            "eur_rate": None,
            "ecb_main_refi_rate": None,
            "ecb_marginal_lending_rate": None,
            "ecb_deposit_rate": None,
            "fed_target_lower_rate_as_of": None,
            "fed_target_upper_rate_as_of": None,
            "ecb_main_refi_rate_as_of": None,
            "ecb_marginal_lending_rate_as_of": None,
            "ecb_deposit_rate_as_of": None,
            "rate_differential_value": None,
            "rate_differential": None,
            "usd_as_of": None,
            "eur_as_of": None,
            "source": None,
        }

        if fed_rates:
            result.update(fed_rates)
        else:
            logger.warning("Fed target-range page did not return parseable values")

        if ecb_rates:
            result.update(
                {
                    "eur_rate": ecb_rates["ecb_deposit_rate"],
                    "ecb_main_refi_rate": ecb_rates["ecb_main_refi_rate"],
                    "ecb_marginal_lending_rate": ecb_rates["ecb_marginal_lending_rate"],
                    "ecb_deposit_rate": ecb_rates["ecb_deposit_rate"],
                    "ecb_main_refi_rate_as_of": ecb_rates["ecb_main_refi_rate_as_of"],
                    "ecb_marginal_lending_rate_as_of": ecb_rates["ecb_marginal_lending_rate_as_of"],
                    "ecb_deposit_rate_as_of": ecb_rates["ecb_deposit_rate_as_of"],
                    "eur_as_of": ecb_rates["ecb_deposit_rate_as_of"],
                }
            )
        else:
            logger.warning("ECB key-rates page did not return parseable values")

        if result["usd_rate"] is not None and result["eur_rate"] is not None:
            diff = round(result["usd_rate"] - result["eur_rate"], 2)
            result["rate_differential_value"] = diff
            result["rate_differential"] = format_rate_differential(diff)

        source_parts = []
        if fed_rates:
            source_parts.append("Fed open-market page")
        if ecb_rates:
            source_parts.append("ECB key-rates page")
        if not source_parts:
            return {}

        result["source"] = " + ".join(source_parts) + f" @ {now.strftime('%Y-%m-%d %H:%M UTC')}"
        return result
    except requests.exceptions.RequestException as exc:
        logger.warning(f"Policy rate fetch failed: {exc}")
        return {}
    except Exception as exc:
        logger.warning(f"Policy rate parse failed: {exc}")
        return {}


def _build_yfinance_intraday_signal(symbol: str, label: str) -> dict:
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("yfinance not installed — run: pip install yfinance")
        return {}

    try:
        ticker = yf.Ticker(symbol)
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
                logger.warning(f"{label}: insufficient intraday data returned from yfinance")
                return {}

        current = round(float(hist["Close"].iloc[-1]), 2)
        ma_window = min(12, len(hist))
        ma_1h = round(float(hist["Close"].tail(ma_window).mean()), 2)
        ref_15m = float(hist["Close"].iloc[-4]) if len(hist) >= 4 else float(hist["Close"].iloc[0])
        pct_vs_ma = round((current - ma_1h) / ma_1h * 100, 3) if ma_1h else 0.0
        pct_15m = round((current - ref_15m) / ref_15m * 100, 3) if ref_15m else 0.0

        if pct_vs_ma > 0.05 and pct_15m >= -0.03:
            direction = "UP"
        elif pct_vs_ma < -0.05 and pct_15m <= 0.03:
            direction = "DOWN"
        else:
            direction = "FLAT"

        return {
            "level": current,
            "ma_1h": ma_1h,
            "pct_vs_ma": pct_vs_ma,
            "pct_15m": pct_15m,
            "direction": direction,
        }
    except Exception as exc:
        logger.warning(f"{label} fetch failed: {exc}")
        return {}


def build_dxy_snapshot(now: datetime) -> dict:
    signal = _build_yfinance_intraday_signal("DX-Y.NYB", "DXY")
    if not signal:
        return {}

    direction = {
        "UP": "RISING",
        "DOWN": "FALLING",
        "FLAT": "NEUTRAL",
    }[signal["direction"]]
    return {
        **signal,
        "direction": direction,
        "source": f"yfinance DX-Y.NYB @ {now.strftime('%Y-%m-%d %H:%M UTC')}",
    }


def build_risk_sentiment_snapshot(now: datetime) -> dict:
    signal = _build_yfinance_intraday_signal("SPY", "Risk sentiment")
    if not signal:
        return {}

    sentiment = {
        "UP": "RISK_ON",
        "DOWN": "RISK_OFF",
        "FLAT": "NEUTRAL",
    }[signal["direction"]]
    return {
        "risk_sentiment": sentiment,
        "proxy": "SPY",
        "level": signal["level"],
        "pct_vs_ma": signal["pct_vs_ma"],
        "pct_15m": signal["pct_15m"],
        "source": f"yfinance SPY @ {now.strftime('%Y-%m-%d %H:%M UTC')}",
    }


def build_cot_eur_snapshot(now: datetime) -> dict:
    year = now.year
    url = f"https://www.cftc.gov/files/dea/history/fut_fin_txt_{year}.zip"

    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
    except requests.exceptions.Timeout:
        logger.warning("COT fetch timed out")
        return {}
    except requests.exceptions.RequestException as exc:
        logger.warning(f"COT fetch failed: {exc}")
        return {}

    try:
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            filename = archive.namelist()[0]
            with archive.open(filename) as raw_file:
                reader = csv.DictReader(io.TextIOWrapper(raw_file, encoding="utf-8"))
                for row in reader:
                    market = row.get("Market_and_Exchange_Names", "")
                    if "EURO FX" not in market.upper():
                        continue

                    def _int(key: str) -> int:
                        value = row.get(key, "0").strip()
                        return int(value) if value.lstrip("+-").isdigit() else 0

                    am_long = _int("Asset_Mgr_Positions_Long_All")
                    am_short = _int("Asset_Mgr_Positions_Short_All")
                    lm_long = _int("Lev_Money_Positions_Long_All")
                    lm_short = _int("Lev_Money_Positions_Short_All")

                    net_am = am_long - am_short
                    net_lm = lm_long - lm_short
                    as_of = row.get("Report_Date_as_YYYY-MM-DD", "unknown").strip()

                    if net_am > 50_000:
                        bias = "BULLISH"
                    elif net_am < -50_000:
                        bias = "BEARISH"
                    else:
                        bias = "NEUTRAL"

                    net_str = f"+{net_am:,}" if net_am >= 0 else f"{net_am:,}"
                    lm_str = f"+{net_lm:,}" if net_lm >= 0 else f"{net_lm:,}"
                    return {
                        "bias": bias,
                        "net_asset_mgr": net_am,
                        "net_lev_money": net_lm,
                        "net_str": net_str,
                        "lm_str": lm_str,
                        "as_of": as_of,
                        "source": f"CFTC Disaggregated Financial Futures — as of {as_of}",
                    }
    except Exception as exc:
        logger.error(f"COT parse error: {exc}")
        return {}

    logger.warning("COT: EURO FX row not found in CFTC file")
    return {}


def build_calendar_snapshot(now: datetime) -> dict:
    candidates: list[dict] = []
    urls = [
        "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
        "https://nfs.faireconomy.media/ff_calendar_nextweek.json",
    ]

    for url in urls:
        before_count = len(candidates)
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

            event_time = parse_utc(item.get("date"))
            if event_time is None:
                continue
            if event_time < now - timedelta(minutes=30):
                continue

            candidates.append(
                {
                    "country": country,
                    "event": item.get("title", "Unknown event").strip(),
                    "event_time": event_time,
                }
            )

        if len(candidates) > before_count:
            break

    if not candidates:
        return {
            "next_event_name": "CLEAR — no high-impact USD/EUR event in calendar",
            "next_news_event": "CLEAR — no high-impact USD/EUR event in calendar",
            "time_to_event": None,
            "news_risk": "CLEAR",
            "source": "Forex Factory calendar",
        }

    next_event = min(candidates, key=lambda event: abs((event["event_time"] - now).total_seconds()))
    event_name = f"{next_event['country']} — {next_event['event']}"
    time_to_event = humanize_delta(next_event["event_time"], now)
    return {
        "next_event_name": event_name,
        "next_news_event": event_name,
        "time_to_event": time_to_event,
        "news_risk": classify_news_risk(event_name, time_to_event),
        "event_time": next_event["event_time"].isoformat(),
        "source": "Forex Factory calendar",
    }


def build_recent_fx_headline_snapshot() -> dict:
    finnhub_key = os.getenv("FINNHUB_API_KEY", "").strip()
    if finnhub_key:
        try:
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
                published_at = (
                    datetime.fromtimestamp(article["datetime"], tz=timezone.utc).isoformat()
                    if article.get("datetime")
                    else None
                )
                return {
                    "headline": title,
                    "published_at": published_at,
                    "source": source_name,
                }
        except requests.exceptions.RequestException as exc:
            logger.warning(f"Finnhub news fetch failed: {exc}")
        except Exception as exc:
            logger.warning(f"Finnhub news parse failed: {exc}")

    newsapi_key = os.getenv("NEWS_API_KEY", "").strip()
    if not newsapi_key:
        return {}

    try:
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
        published_at = parse_utc(article.get("publishedAt"))
        return {
            "headline": title,
            "published_at": published_at.isoformat() if published_at else None,
            "source": source_name,
        }

    return {}


def build_retail_sentiment_snapshot() -> dict:
    api_key = os.getenv("OANDA_API_KEY", "").strip()
    if not api_key:
        return {}

    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        response = requests.get(
            "https://api-fxpractice.oanda.com/v3/instruments/EUR_USD/positionBook",
            headers=headers,
            timeout=15,
        )
        response.raise_for_status()
        book = response.json().get("positionBook", {})
        buckets = book.get("buckets", [])
        current = float(book.get("price", 0))

        if not buckets or current == 0:
            return {}

        pip_range = 0.0050
        zone_long = 0.0
        zone_short = 0.0
        for bucket in buckets:
            price = float(bucket.get("price", 0))
            long_pct = float(bucket.get("longCountPercent", 0))
            short_pct = float(bucket.get("shortCountPercent", 0))
            if abs(price - current) <= pip_range:
                zone_long += long_pct
                zone_short += short_pct

        total = zone_long + zone_short
        if total == 0:
            return {}

        pct_long = round(zone_long / total * 100)
        pct_short = round(zone_short / total * 100)
        if pct_short >= 60:
            signal = "contrarian BULLISH"
        elif pct_long >= 60:
            signal = "contrarian BEARISH"
        else:
            signal = "mixed — no clear contrarian edge"

        sentiment_str = f"{pct_short}% SHORT, {pct_long}% LONG ({signal}) — OANDA position book"
        return {
            "sentiment": sentiment_str,
            "pct_long": pct_long,
            "pct_short": pct_short,
            "signal": signal,
            "source": "OANDA EUR_USD position book",
        }
    except requests.exceptions.RequestException as exc:
        logger.warning(f"OANDA position book fetch failed: {exc}")
        return {}
    except Exception as exc:
        logger.warning(f"OANDA position book parse failed: {exc}")
        return {}
