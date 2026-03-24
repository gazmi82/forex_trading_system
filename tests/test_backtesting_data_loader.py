from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from app.backtesting import HistoricalDataLoader


def _frame(start: str, periods: int, freq: str) -> pd.DataFrame:
    index = pd.date_range(start=start, periods=periods, freq=freq, tz="UTC")
    rows = []
    for idx, _ in enumerate(index):
        rows.append(
            {
                "open": 1.1000 + idx * 0.0001,
                "high": 1.1010 + idx * 0.0001,
                "low": 1.0990 + idx * 0.0001,
                "close": 1.1005 + idx * 0.0001,
                "volume": 100 + idx,
            }
        )
    return pd.DataFrame(rows, index=index)


class _FakeHistoricalClient:
    def __init__(self):
        self.calls: list[tuple[str, str, datetime, datetime]] = []

    def get_candles_range(self, instrument: str, granularity: str, *, start: datetime, end: datetime):
        self.calls.append((instrument, granularity, start, end))
        freq = {"H1": "1h", "H4": "4h", "D": "1d"}[granularity]
        delta = {"H1": timedelta(hours=1), "H4": timedelta(hours=4), "D": timedelta(days=1)}[granularity]
        periods = max(int((end - start) / delta), 0)
        return _frame(start.isoformat(), periods, freq)


class HistoricalDataLoaderTests(unittest.TestCase):
    def test_load_candles_writes_cache_and_reuses_it(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client = _FakeHistoricalClient()
            loader = HistoricalDataLoader(client, Path(tmpdir))
            start = datetime(2026, 3, 1, tzinfo=timezone.utc)
            end = datetime(2026, 3, 3, tzinfo=timezone.utc)

            first = loader.load_candles("EUR_USD", "H1", start=start, end=end)
            second = loader.load_candles("EUR_USD", "H1", start=start, end=end)

        self.assertEqual(len(client.calls), 1)
        pd.testing.assert_frame_equal(first, second)

    def test_slice_context_excludes_incomplete_higher_timeframes(self):
        datasets = {
            "H1": _frame("2026-03-24T06:00:00+00:00", 5, "1h"),
            "H4": _frame("2026-03-24T00:00:00+00:00", 3, "4h"),
            "D": _frame("2026-03-22T00:00:00+00:00", 3, "1d"),
        }

        context = HistoricalDataLoader.slice_context(
            datasets,
            as_of=datetime(2026, 3, 24, 10, 0, tzinfo=timezone.utc),
            lookback_bars={"H1": 10, "H4": 10, "D": 10},
        )

        self.assertEqual(context["H1"].index[-1], pd.Timestamp("2026-03-24T09:00:00Z"))
        self.assertEqual(context["H4"].index[-1], pd.Timestamp("2026-03-24T04:00:00Z"))
        self.assertEqual(context["D"].index[-1], pd.Timestamp("2026-03-23T00:00:00Z"))
        self.assertNotIn(pd.Timestamp("2026-03-24T08:00:00Z"), context["H4"].index)
        self.assertNotIn(pd.Timestamp("2026-03-24T00:00:00Z"), context["D"].index)

    def test_load_context_returns_requested_lookbacks_without_lookahead(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client = _FakeHistoricalClient()
            loader = HistoricalDataLoader(client, Path(tmpdir))
            context = loader.load_context(
                "EUR_USD",
                as_of=datetime(2026, 3, 24, 12, 0, tzinfo=timezone.utc),
                lookback_bars={"H1": 3, "H4": 2, "D": 2},
            )

        self.assertEqual(len(context["H1"]), 3)
        self.assertEqual(len(context["H4"]), 2)
        self.assertEqual(len(context["D"]), 2)
        self.assertLessEqual(context["H1"].index[-1] + timedelta(hours=1), pd.Timestamp("2026-03-24T12:00:00Z"))
        self.assertLessEqual(context["H4"].index[-1] + timedelta(hours=4), pd.Timestamp("2026-03-24T12:00:00Z"))
        self.assertLessEqual(context["D"].index[-1] + timedelta(days=1), pd.Timestamp("2026-03-24T12:00:00Z"))


if __name__ == "__main__":
    unittest.main()
