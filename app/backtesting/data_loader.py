from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Mapping

import pandas as pd


_GRANULARITY_TO_DELTA = {
    "M1": timedelta(minutes=1),
    "M5": timedelta(minutes=5),
    "M15": timedelta(minutes=15),
    "M30": timedelta(minutes=30),
    "H1": timedelta(hours=1),
    "H4": timedelta(hours=4),
    "D": timedelta(days=1),
    "W": timedelta(weeks=1),
}

_DEFAULT_LOOKBACK_BARS = {
    "H1": 200,
    "H4": 200,
    "D": 200,
}


@dataclass(frozen=True)
class CacheKey:
    instrument: str
    granularity: str
    start: datetime
    end: datetime

    def to_filename(self) -> str:
        instrument_slug = self.instrument.replace("/", "_")
        start_slug = self.start.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        end_slug = self.end.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return f"{instrument_slug}_{self.granularity}_{start_slug}_{end_slug}.csv"


class HistoricalDataLoader:
    """
    Fetches and caches historical candle data for backtesting, then slices
    timeframe-aligned context without leaking future candles into the sample.
    """

    MAX_CANDLES_PER_REQUEST = 4500

    def __init__(self, oanda_client, cache_dir: Path | None = None):
        self.client = oanda_client
        self.cache_dir = Path(cache_dir or "backtest_data")
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def load_candles(
        self,
        instrument: str,
        granularity: str,
        *,
        start: datetime,
        end: datetime,
        force_refresh: bool = False,
    ) -> pd.DataFrame:
        granularity = granularity.upper()
        start_utc = _ensure_utc(start)
        end_utc = _ensure_utc(end)
        if start_utc >= end_utc:
            raise ValueError("start must be earlier than end")
        _granularity_delta(granularity)

        cache_key = CacheKey(instrument, granularity, start_utc, end_utc)
        cache_path = self.cache_dir / cache_key.to_filename()
        if cache_path.exists() and not force_refresh:
            return self._read_cache(cache_path)

        frames = []
        for chunk_start, chunk_end in self._chunk_ranges(granularity, start_utc, end_utc):
            frame = self.client.get_candles_range(
                instrument,
                granularity,
                start=chunk_start,
                end=chunk_end,
            )
            if frame is not None and not frame.empty:
                frames.append(_normalise_frame(frame))

        if frames:
            data = pd.concat(frames).sort_index()
            data = data[~data.index.duplicated(keep="last")]
        else:
            data = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
            data.index = pd.DatetimeIndex([], tz=timezone.utc, name="time")

        data.to_csv(cache_path, index_label="time")
        return data

    def load_multi_timeframe(
        self,
        instrument: str,
        *,
        start: datetime,
        end: datetime,
        granularities: Iterable[str] = ("D", "H4", "H1"),
        force_refresh: bool = False,
    ) -> dict[str, pd.DataFrame]:
        return {
            granularity.upper(): self.load_candles(
                instrument,
                granularity.upper(),
                start=start,
                end=end,
                force_refresh=force_refresh,
            )
            for granularity in granularities
        }

    def load_context(
        self,
        instrument: str,
        *,
        as_of: datetime,
        lookback_bars: Mapping[str, int] | None = None,
        force_refresh: bool = False,
    ) -> dict[str, pd.DataFrame]:
        as_of_utc = _ensure_utc(as_of)
        requested = {
            key.upper(): int(value)
            for key, value in (lookback_bars or _DEFAULT_LOOKBACK_BARS).items()
        }
        datasets: dict[str, pd.DataFrame] = {}
        for granularity, bars in requested.items():
            delta = _granularity_delta(granularity)
            start = as_of_utc - (delta * max(bars + 10, bars * 2))
            datasets[granularity] = self.load_candles(
                instrument,
                granularity,
                start=start,
                end=as_of_utc,
                force_refresh=force_refresh,
            )
        return self.slice_context(datasets, as_of=as_of_utc, lookback_bars=requested)

    @staticmethod
    def slice_context(
        datasets: Mapping[str, pd.DataFrame],
        *,
        as_of: datetime,
        lookback_bars: Mapping[str, int] | None = None,
    ) -> dict[str, pd.DataFrame]:
        as_of_utc = _ensure_utc(as_of)
        requested = {
            key.upper(): int(value)
            for key, value in (lookback_bars or _DEFAULT_LOOKBACK_BARS).items()
        }

        sliced: dict[str, pd.DataFrame] = {}
        for granularity, frame in datasets.items():
            granularity = granularity.upper()
            if frame is None or frame.empty:
                sliced[granularity] = _empty_frame()
                continue

            normalised = _normalise_frame(frame)
            delta = _granularity_delta(granularity)
            completed = normalised[(normalised.index + delta) <= as_of_utc]
            lookback = requested.get(granularity, len(completed))
            sliced[granularity] = completed.tail(lookback)

        return sliced

    def _read_cache(self, path: Path) -> pd.DataFrame:
        frame = pd.read_csv(path, parse_dates=["time"], index_col="time")
        return _normalise_frame(frame)

    def _chunk_ranges(
        self,
        granularity: str,
        start: datetime,
        end: datetime,
    ) -> list[tuple[datetime, datetime]]:
        delta = _granularity_delta(granularity)
        max_span = delta * self.MAX_CANDLES_PER_REQUEST
        chunks: list[tuple[datetime, datetime]] = []

        cursor = start
        while cursor < end:
            chunk_end = min(end, cursor + max_span)
            chunks.append((cursor, chunk_end))
            cursor = chunk_end

        return chunks


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _granularity_delta(granularity: str) -> timedelta:
    try:
        return _GRANULARITY_TO_DELTA[granularity.upper()]
    except KeyError as exc:
        raise ValueError(f"Unsupported granularity: {granularity}") from exc


def _normalise_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return _empty_frame()

    normalised = frame.copy()
    normalised.index = pd.DatetimeIndex(
        pd.to_datetime(normalised.index, utc=True),
        tz=timezone.utc,
        name="time",
    )
    normalised.index = normalised.index._with_freq(None)
    return normalised.sort_index()


def _empty_frame() -> pd.DataFrame:
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
    empty.index = pd.DatetimeIndex([], tz=timezone.utc, name="time")
    return empty
