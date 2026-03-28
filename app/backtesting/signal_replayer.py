from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
import json
from pathlib import Path
import re
from typing import Iterable
from zoneinfo import ZoneInfo

import pandas as pd

from app.analysis.market_analysis import IndicatorCalculator, MarketStructureAnalyzer
from app.backtesting.historical_fundamentals_provider import HistoricalFundamentalsProvider
from app.backtesting.replay_confluence import calculate_confluence
from app.core.config import AGENT_CONFIG, TRADING_CONFIG
from app.core.trade_management import resolve_tp1_price


NY_TZ = ZoneInfo("America/New_York")
SESSION_WINDOWS = (
    ("London Kill Zone", 3),
    ("NY Kill Zone", 8),
    ("London Close", 10),
)
LOOKBACK_BARS = {
    "W": 80,
    "D": 260,
    "H4": 260,
    "H1": 260,
    "M15": 260,
    "M1": 2000,
}


@dataclass(frozen=True)
class ReplayWindow:
    timestamp: datetime
    session: str


@dataclass(frozen=True)
class ReplaySummary:
    instrument: str
    start: str
    end: str
    total_windows: int
    tradable_windows: int
    output_path: str


class SignalReplayEngine:
    """
    Rebuilds historical market_data from local OANDA datasets and emits one
    deterministic replay signal record per kill-zone window without calling Claude.
    """

    def __init__(
        self,
        loader,
        *,
        output_root: Path | None = None,
        fundamentals_provider: HistoricalFundamentalsProvider | None = None,
        min_confidence: int = AGENT_CONFIG["min_confidence"],
        strong_signal: int = AGENT_CONFIG["strong_signal"],
    ):
        self.loader = loader
        self.output_root = Path(output_root or "backtest_results")
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.fundamentals_provider = fundamentals_provider or HistoricalFundamentalsProvider(
            getattr(loader, "cache_dir", Path("backtest_data"))
        )
        self.trading_config = dict(TRADING_CONFIG)
        self.min_confidence = int(min_confidence)
        self.strong_signal = int(strong_signal)

    def replay(
        self,
        instrument: str,
        *,
        start: datetime,
        end: datetime,
        local_only: bool = True,
        output_path: Path | None = None,
    ) -> ReplaySummary:
        """
        Replay every historical kill-zone window in the requested range.

        The normal mode for Phase 2 is local-only replay against frozen CSV
        datasets so backtest results are reproducible and do not depend on live
        OANDA availability.
        """
        start_utc = _ensure_utc(start)
        end_utc = _ensure_utc(end)
        if start_utc >= end_utc:
            raise ValueError("start must be earlier than end")

        datasets = self._load_datasets(
            instrument,
            start=start_utc,
            end=end_utc,
            local_only=local_only,
        )
        windows = list(iter_kill_zone_windows(start_utc, end_utc))

        output_file = Path(output_path or self._default_output_path(instrument, start_utc, end_utc))
        output_file.parent.mkdir(parents=True, exist_ok=True)

        tradable = 0
        with open(output_file, "w", encoding="utf-8") as handle:
            for window in windows:
                signal = self.replay_window(instrument, window, datasets)
                if signal["execution_allowed"]:
                    tradable += 1
                handle.write(json.dumps(signal) + "\n")

        return ReplaySummary(
            instrument=instrument.replace("/", "_").upper(),
            start=_to_utc_z(start_utc),
            end=_to_utc_z(end_utc),
            total_windows=len(windows),
            tradable_windows=tradable,
            output_path=str(output_file),
        )

    def replay_window(
        self,
        instrument: str,
        window: ReplayWindow,
        datasets: dict[str, pd.DataFrame],
    ) -> dict:
        """
        Generate one deterministic backtest signal for a single replay window.

        BUY and SELL are both scored from the same historical
        market snapshot, then the stronger side is emitted with deterministic
        trade levels for later outcome simulation.
        """
        market_data = self._build_market_data(instrument, window, datasets)
        buy_result = calculate_confluence(
            market_data,
            {"signal": {"direction": "BUY"}},
        )
        sell_result = calculate_confluence(
            market_data,
            {"signal": {"direction": "SELL"}},
        )

        direction, replay_result = self._select_direction(buy_result, sell_result)
        strength = _signal_strength(replay_result["confluence_score"], self.min_confidence, self.strong_signal)
        entry_zone, entry_source = self._entry_zone(direction, market_data)
        stop_loss, tp1, tp2, rr = self._trade_levels(direction, entry_zone, market_data)

        execution_allowed = direction != "NEUTRAL" and replay_result["confluence_score"] >= self.min_confidence
        execution_direction = direction if execution_allowed else "NEUTRAL"
        overrides = []
        indicators = market_data.get("indicators", {})
        if direction == "NEUTRAL":
            overrides.append("BLOCKED: No directional edge at this window")
        elif replay_result["confluence_score"] < self.min_confidence:
            overrides.append(
                f"BLOCKED: Confluence score too low "
                f"({replay_result['confluence_score']}/100, minimum {self.min_confidence})"
            )

        risk_reason = "; ".join(overrides) if overrides else ""
        timestamp = _to_utc_z(window.timestamp)
        signal_payload = {
            "direction": direction,
            "confidence": replay_result["confluence_score"],
            "entry_zone": entry_zone,
            "stop_loss": stop_loss,
            "take_profit_1": tp1,
            "take_profit_2": tp2,
            "risk_reward": rr,
        }

        return {
            "pair": market_data["pair"],
            "timestamp": timestamp,
            "session": window.session,
            "analysis_source": "HISTORICAL_REPLAY",
            "historical_data_mode": "PRICE_ONLY",
            "confluence_score": replay_result["confluence_score"],
            "signal_strength": strength,
            "component_scores": replay_result["component_scores"],
            "available_component_points": replay_result.get("available_component_points"),
            "unavailable_components": replay_result.get("unavailable_components", []),
            "direction_implied": replay_result["direction_implied"],
            "entry_zone_source": entry_source,
            "execution_allowed": execution_allowed,
            "execution_direction": execution_direction,
            "do_not_trade_reason": risk_reason,
            "validator_overrides": overrides,
            "signal": signal_payload,
            "technical_analysis": {
                "atr_1h": indicators.get("atr_1h"),
                "adx_14": indicators.get("adx_4h"),
                "rsi_14": indicators.get("rsi_1h"),
                "market_regime": indicators.get("market_regime"),
            },
            "market_data": {
                "price": market_data["price"],
                "spread": market_data["spread"],
                "fetch_time": market_data["fetch_time"],
                "ohlcv": market_data["ohlcv"],
                "fundamental": market_data["fundamental"],
            },
        }

    def _default_output_path(self, instrument: str, start: datetime, end: datetime) -> Path:
        instrument_slug = instrument.replace("/", "_").upper()
        filename = (
            f"{instrument_slug}_replay_{_utc_slug(start)}_{_utc_slug(end)}.jsonl"
        )
        return self.output_root / instrument_slug / filename

    def _load_datasets(
        self,
        instrument: str,
        *,
        start: datetime,
        end: datetime,
        local_only: bool,
    ) -> dict[str, pd.DataFrame]:
        datasets: dict[str, pd.DataFrame] = {}
        for granularity in ("M1", "H1", "H4", "D", "W"):
            local_range = self.loader.find_covering_raw_dataset(
                instrument,
                granularity,
                start=start,
                end=end,
            )
            load_start = local_range.start if local_range is not None else start
            datasets[granularity] = self.loader.load_candles(
                instrument,
                granularity,
                start=load_start,
                end=end,
                allow_remote=not local_only,
            )

        datasets["M15"] = _resample_to_m15(datasets["M1"])
        return datasets

    def _build_market_data(
        self,
        instrument: str,
        window: ReplayWindow,
        datasets: dict[str, pd.DataFrame],
    ) -> dict:
        context = self.loader.slice_context(
            datasets,
            as_of=window.timestamp,
            lookback_bars=LOOKBACK_BARS,
        )

        df_weekly = context["W"]
        df_daily = context["D"]
        df_4h = context["H4"]
        df_1h = context["H1"]
        df_15m = context["M15"]
        df_m1 = context["M1"]

        price = _latest_close(df_m1, fallback=_latest_close(df_15m))
        indicators = self._indicators(df_4h, df_1h, df_daily)
        weekly_struct = self._structure(df_weekly, "Weekly")
        daily_struct = self._structure(df_daily, "Daily")
        h4_struct = self._structure(df_4h, "4H")
        h1_struct = self._structure(df_1h, "1H")
        m15_struct = self._structure(df_15m, "15M")

        ohlcv = {
            "day_open": _bar_open(df_daily, price),
            "week_open": _bar_open(df_weekly, price),
            "month_open": _month_open(df_daily, price),
            "prev_day_high": _previous_bar_value(df_daily, "high", price),
            "prev_day_low": _previous_bar_value(df_daily, "low", price),
            "prev_week_high": _previous_bar_value(df_weekly, "high", price),
            "prev_week_low": _previous_bar_value(df_weekly, "low", price),
            "weekly_structure": weekly_struct["structure"],
            "daily_structure": daily_struct["structure"],
            "h4_structure": h4_struct["structure"],
            "h1_structure": h1_struct["structure"],
            "m15_structure": m15_struct["structure"],
            "weekly_trend": weekly_struct["trend"],
            "daily_trend": daily_struct["trend"],
            "h4_trend": h4_struct["trend"],
            "h1_trend": h1_struct["trend"],
            "m15_trend": m15_struct["trend"],
        }

        fundamental = self.fundamentals_provider.snapshot(window.timestamp, window.session)

        return {
            "pair": instrument.replace("_", "/"),
            "price": price,
            "spread": 0.0,
            "demo_mode": False,
            "ohlcv": ohlcv,
            "indicators": indicators,
            "fundamental": fundamental,
            "portfolio": {
                "balance": 0.0,
                "equity": 0.0,
                "open_trades": 0,
                "open_risk_pct": 0.0,
                "daily_pnl_pct": 0.0,
                "weekly_pnl_pct": 0.0,
                "trades_today": 0,
                "usd_exposure": "NONE",
                "margin_used_pct": 0.0,
            },
            "fetch_time": _to_utc_z(window.timestamp),
        }

    @staticmethod
    def _indicators(df_4h: pd.DataFrame, df_1h: pd.DataFrame, df_daily: pd.DataFrame) -> dict:
        if df_4h.empty or df_1h.empty or df_daily.empty:
            return {}
        return IndicatorCalculator.calculate_all(df_4h, df_1h, df_daily)

    @staticmethod
    def _structure(frame: pd.DataFrame, timeframe: str) -> dict:
        if frame.empty:
            return {"trend": "NEUTRAL", "structure": f"Insufficient data ({timeframe})"}
        return MarketStructureAnalyzer.analyze(frame, timeframe)

    @staticmethod
    def _select_direction(buy_result: dict, sell_result: dict) -> tuple[str, dict]:
        buy_score = int(buy_result.get("confluence_score", 0) or 0)
        sell_score = int(sell_result.get("confluence_score", 0) or 0)
        if buy_score > sell_score:
            return "BUY", buy_result
        if sell_score > buy_score:
            return "SELL", sell_result

        implied = (buy_result.get("direction_implied") or "NEUTRAL").upper()
        if implied == "BUY" and buy_score > 0:
            return "BUY", buy_result
        if implied == "SELL" and sell_score > 0:
            return "SELL", sell_result

        return "NEUTRAL", {
            "confluence_score": max(buy_score, sell_score),
            "direction_implied": implied,
            "component_scores": {},
        }

    @staticmethod
    def _entry_zone(direction: str, market_data: dict) -> tuple[list[float], str]:
        indicators = market_data.get("indicators", {})
        price = float(market_data.get("price", 0) or 0)
        atr = float(indicators.get("atr_4h") or 0)
        fallback_half_width = max(0.0002, atr * 0.05)

        candidates: Iterable[tuple[str, object]]
        if direction == "BUY":
            candidates = (
                ("order_block", indicators.get("bullish_ob")),
                ("fvg", indicators.get("bullish_fvg")),
                ("ote_zone", indicators.get("ote_zone")),
            )
        elif direction == "SELL":
            candidates = (
                ("order_block", indicators.get("bearish_ob")),
                ("fvg", indicators.get("bearish_fvg")),
                ("ote_zone", indicators.get("ote_zone")),
            )
        else:
            zone = [round(price - fallback_half_width, 5), round(price + fallback_half_width, 5)]
            return zone, "neutral_fallback"

        for source, value in candidates:
            zone = _parse_zone(value)
            if zone:
                return zone, source

        if price <= 0:
            return [0.0, 0.0], "missing_price"

        zone = [round(price - fallback_half_width, 5), round(price + fallback_half_width, 5)]
        return zone, "price_fallback"

    def _trade_levels(self, direction: str, entry_zone: list[float], market_data: dict) -> tuple[float, float, float, float]:
        if direction == "NEUTRAL" or len(entry_zone) < 2:
            return 0.0, 0.0, 0.0, 0.0

        indicators = market_data.get("indicators", {})
        atr = float(indicators.get("atr_4h") or 0)
        buffer = max(0.0003, atr * 0.25)

        zone_low, zone_high = sorted(float(value) for value in entry_zone[:2])
        entry_price = round((zone_low + zone_high) / 2, 5)
        if direction == "BUY":
            stop_loss = round(zone_low - buffer, 5)
            risk = max(entry_price - stop_loss, 0.0005)
            tp2 = round(entry_price + (risk * 2), 5)
        else:
            stop_loss = round(zone_high + buffer, 5)
            risk = max(stop_loss - entry_price, 0.0005)
            tp2 = round(entry_price - (risk * 2), 5)

        tp1 = resolve_tp1_price(
            self.trading_config,
            entry_price=entry_price,
            tp2_price=tp2,
        ) or 0.0

        return stop_loss, tp1, tp2, 2.0


def iter_kill_zone_windows(start: datetime, end: datetime) -> Iterable[ReplayWindow]:
    start_utc = _ensure_utc(start)
    end_utc = _ensure_utc(end)
    start_day = start_utc.astimezone(NY_TZ).date() - timedelta(days=1)
    end_day = end_utc.astimezone(NY_TZ).date() + timedelta(days=1)

    day = start_day
    while day <= end_day:
        if day.weekday() < 5:
            for session, hour in SESSION_WINDOWS:
                local_dt = datetime.combine(day, time(hour=hour), tzinfo=NY_TZ)
                window_dt = local_dt.astimezone(timezone.utc)
                if start_utc <= window_dt < end_utc:
                    yield ReplayWindow(timestamp=window_dt, session=session)
        day += timedelta(days=1)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _to_utc_z(value: datetime) -> str:
    return _ensure_utc(value).isoformat().replace("+00:00", "Z")


def _utc_slug(value: datetime) -> str:
    return _ensure_utc(value).strftime("%Y%m%dT%H%M%SZ")


def _signal_strength(score: int, min_confidence: int, strong_signal: int) -> str:
    if score >= strong_signal:
        return "STRONG"
    if score >= min_confidence:
        return "MODERATE"
    if score > 0:
        return "WEAK"
    return "NEUTRAL"


def _resample_to_m15(df_m1: pd.DataFrame) -> pd.DataFrame:
    if df_m1 is None or df_m1.empty:
        return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])

    frame = df_m1.sort_index()
    resampled = frame.resample("15min", label="left", closed="left").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )
    return resampled.dropna(subset=["open", "high", "low", "close"])


def _latest_close(frame: pd.DataFrame, fallback: float = 0.0) -> float:
    if frame is None or frame.empty:
        return round(float(fallback or 0.0), 5)
    return round(float(frame["close"].iloc[-1]), 5)


def _bar_open(frame: pd.DataFrame, fallback: float) -> float:
    if frame is None or frame.empty:
        return round(float(fallback), 5)
    return round(float(frame["open"].iloc[-1]), 5)


def _previous_bar_value(frame: pd.DataFrame, column: str, fallback: float) -> float:
    if frame is None or frame.empty:
        return round(float(fallback), 5)
    if len(frame) >= 2:
        return round(float(frame[column].iloc[-2]), 5)
    return round(float(frame[column].iloc[-1]), 5)


def _month_open(frame: pd.DataFrame, fallback: float) -> float:
    if frame is None or frame.empty:
        return round(float(fallback), 5)
    current_bar = frame.index[-1]
    month_mask = (
        (frame.index.year == current_bar.year)
        & (frame.index.month == current_bar.month)
    )
    month_frame = frame.loc[month_mask]
    if month_frame.empty:
        return round(float(fallback), 5)
    return round(float(month_frame["open"].iloc[0]), 5)


def _parse_zone(value: object) -> list[float]:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        lo = round(float(min(value[0], value[1])), 5)
        hi = round(float(max(value[0], value[1])), 5)
        return [lo, hi]

    if not value:
        return []

    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*[–-]\s*([0-9]+(?:\.[0-9]+)?)", str(value))
    if not match:
        return []

    lo = round(float(min(float(match.group(1)), float(match.group(2)))), 5)
    hi = round(float(max(float(match.group(1)), float(match.group(2)))), 5)
    return [lo, hi]
