from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from app.backtesting.data_loader import _ensure_utc
from app.fundamentals.common import (
    classify_news_risk,
    format_rate_differential,
    humanize_delta,
    is_high_impact_event,
    parse_utc,
)
from app.fundamentals.payloads import (
    HISTORICAL_DIRECTION_NEUTRAL,
    HISTORICAL_NEWS_UNAVAILABLE,
    HISTORICAL_UNAVAILABLE,
    build_historical_auto_defaults,
    build_historical_fundamental_snapshot,
)


class HistoricalFundamentalsProvider:
    """
    Resolve historical macro/news context from local datasets.

    The provider is intentionally local-only and deterministic. Each dataset is
    optional; when a file is missing, the replay falls back to explicit
    historical-unavailable sentinels for that slice of the payload.
    """

    USD_RATES_PATH = ("rates", "usd_policy_rates.csv")
    EUR_RATES_PATH = ("rates", "eur_policy_rates.csv")
    DXY_PATH = ("dxy", "dxy.csv")
    COT_PATH = ("cot", "eur_cot.csv")
    CALENDAR_PATH = ("calendar", "events.csv")

    def __init__(self, data_root: Path | None = None):
        self.data_root = Path(data_root or "backtest_data")
        self.fundamentals_root = self.data_root / "fundamentals"
        self._frame_cache: dict[Path, pd.DataFrame | None] = {}

    def snapshot(self, as_of: datetime, session: str) -> dict:
        as_of_utc = _ensure_utc(as_of)
        auto = build_historical_auto_defaults()
        auto.update(self._resolve_rates(as_of_utc))
        auto.update(self._resolve_dxy(as_of_utc))
        auto.update(self._resolve_cot(as_of_utc))
        auto.update(self._resolve_calendar(as_of_utc))
        return build_historical_fundamental_snapshot(session, auto=auto)

    def _resolve_rates(self, as_of: datetime) -> dict[str, Any]:
        usd = self._last_row(self.USD_RATES_PATH, as_of, time_col="effective_at")
        eur = self._last_row(self.EUR_RATES_PATH, as_of, time_col="effective_at")
        if usd is None or eur is None:
            return {}

        lower = _row_float(usd, "fed_target_lower_rate")
        upper = _row_float(usd, "fed_target_upper_rate")
        usd_rate = _row_float(usd, "usd_rate")
        if usd_rate is None and lower is not None and upper is not None:
            usd_rate = round((lower + upper) / 2, 4)

        main_refi = _row_float(eur, "ecb_main_refi_rate")
        marginal = _row_float(eur, "ecb_marginal_lending_rate")
        deposit = _row_float(eur, "ecb_deposit_rate")
        eur_rate = _row_float(eur, "eur_rate")
        if eur_rate is None:
            eur_rate = deposit

        if usd_rate is None or eur_rate is None:
            return {}

        diff = round(usd_rate - eur_rate, 2)
        source = _row_text(usd, "source") or _row_text(eur, "source") or "historical_policy_rates_csv"
        return {
            "usd_rate": usd_rate,
            "fed_target_lower_rate": lower,
            "fed_target_upper_rate": upper,
            "eur_rate": eur_rate,
            "ecb_main_refi_rate": main_refi,
            "ecb_marginal_lending_rate": marginal,
            "ecb_deposit_rate": deposit,
            "rate_differential": format_rate_differential(diff),
            "rates_source": source,
        }

    def _resolve_dxy(self, as_of: datetime) -> dict[str, Any]:
        frame = self._read_csv(self.DXY_PATH, time_col="time")
        if frame is None or frame.empty:
            return {}

        current = _last_frame_row(frame, as_of)
        if current is None:
            return {}

        current_time = current.name.to_pydatetime().astimezone(timezone.utc)
        prior_rows = frame[frame.index < current_time]
        previous = prior_rows.iloc[-1] if not prior_rows.empty else None

        current_level = _row_float(current, "close", "dxy_level", "value")
        previous_level = _row_float(previous, "close", "dxy_level", "value") if previous is not None else None
        if current_level is None:
            return {}

        if previous_level is None:
            direction = HISTORICAL_DIRECTION_NEUTRAL
        elif current_level > previous_level:
            direction = "RISING"
        elif current_level < previous_level:
            direction = "FALLING"
        else:
            direction = HISTORICAL_DIRECTION_NEUTRAL

        return {
            "dxy_direction": direction,
            "dxy_level": f"{current_level:.2f}",
        }

    def _resolve_cot(self, as_of: datetime) -> dict[str, Any]:
        row = self._last_row(self.COT_PATH, as_of, time_col="publication_time")
        if row is None:
            return {}

        cot_net = _row_float(row, "cot_net", "net_positions", "net")
        cot_bias = _row_text(row, "cot_bias")
        if not cot_bias:
            if cot_net is None:
                cot_bias = HISTORICAL_DIRECTION_NEUTRAL
            elif cot_net > 0:
                cot_bias = "BULLISH"
            elif cot_net < 0:
                cot_bias = "BEARISH"
            else:
                cot_bias = HISTORICAL_DIRECTION_NEUTRAL

        return {
            "cot_net": cot_net,
            "cot_bias": cot_bias,
        }

    def _resolve_calendar(self, as_of: datetime) -> dict[str, Any]:
        frame = self._read_csv(self.CALENDAR_PATH, time_col="event_time")
        if frame is None or frame.empty:
            return {}

        relevant = frame[frame.index >= as_of]
        if not relevant.empty:
            for row in relevant.itertuples():
                event_name = str(getattr(row, "event_name", "") or "")
                currency = str(getattr(row, "currency", "") or "").upper()
                raw_importance = getattr(row, "importance", None)
                if currency not in {"USD", "EUR", "EUR/USD", "USD/EUR", "EURUSD"}:
                    continue
                if not is_high_impact_event(event_name, raw_importance):
                    continue

                event_time = row.Index.to_pydatetime().astimezone(timezone.utc)
                time_to_event = humanize_delta(event_time, as_of)
                return {
                    "next_event_name": event_name,
                    "next_news_event": event_name,
                    "time_to_event": time_to_event,
                    "news_risk": classify_news_risk(event_name, time_to_event),
                }

        clear_message = "CLEAR — no high-impact USD/EUR event in local historical calendar"
        return {
            "next_event_name": clear_message,
            "next_news_event": clear_message,
            "time_to_event": None,
            "news_risk": "CLEAR",
            "recent_headline": HISTORICAL_NEWS_UNAVAILABLE,
        }

    def _last_row(self, relative_path: tuple[str, ...], as_of: datetime, *, time_col: str) -> pd.Series | None:
        frame = self._read_csv(relative_path, time_col=time_col)
        if frame is None or frame.empty:
            return None
        return _last_frame_row(frame, as_of)

    def _read_csv(self, relative_path: tuple[str, ...], *, time_col: str) -> pd.DataFrame | None:
        path = self.fundamentals_root.joinpath(*relative_path)
        if path in self._frame_cache:
            return self._frame_cache[path]
        if not path.exists():
            self._frame_cache[path] = None
            return None

        frame = pd.read_csv(path)
        if time_col not in frame.columns:
            raise ValueError(f"{path} must contain a '{time_col}' column")

        parsed = frame[time_col].apply(parse_utc)
        normalised = frame.loc[parsed.notna()].copy()
        normalised.index = pd.DatetimeIndex(parsed[parsed.notna()], tz="UTC")
        normalised = normalised.sort_index()
        self._frame_cache[path] = normalised
        return normalised


def _last_frame_row(frame: pd.DataFrame, as_of: datetime) -> pd.Series | None:
    eligible = frame[frame.index <= as_of]
    if eligible.empty:
        return None
    return eligible.iloc[-1]


def _row_float(row: pd.Series | None, *keys: str) -> float | None:
    if row is None:
        return None
    for key in keys:
        if key not in row.index:
            continue
        value = row.get(key)
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _row_text(row: pd.Series | None, *keys: str) -> str | None:
    if row is None:
        return None
    for key in keys:
        if key not in row.index:
            continue
        value = row.get(key)
        text = str(value or "").strip()
        if text:
            return text
    return None
