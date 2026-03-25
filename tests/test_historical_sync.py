from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from app.backtesting import HistoricalDataLoader, HistoricalDatasetExporter


def _frame(start: datetime, periods: int, freq: str) -> pd.DataFrame:
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
        deltas = {
            "M1": timedelta(minutes=1),
            "H1": timedelta(hours=1),
            "H4": timedelta(hours=4),
            "D": timedelta(days=1),
            "W": timedelta(weeks=1),
        }
        freqs = {"M1": "1min", "H1": "1h", "H4": "4h", "D": "1d", "W": "1W"}
        delta = deltas[granularity]
        periods = max(int((end - start) / delta), 0)
        return _frame(start, periods, freqs[granularity])


class HistoricalSyncTests(unittest.TestCase):
    def test_export_bundle_writes_csvs_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            loader = HistoricalDataLoader(_FakeHistoricalClient(), base)
            exporter = HistoricalDatasetExporter(loader)
            exported = exporter.export_bundle(
                "EUR_USD",
                start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                end=datetime(2024, 1, 2, tzinfo=timezone.utc),
                granularities=("M1", "H1"),
                force_refresh=True,
            )

            self.assertEqual([item.granularity for item in exported], ["M1", "H1"])
            for item in exported:
                self.assertTrue(Path(item.path).exists())
                self.assertGreater(item.rows, 0)

            manifest_path = base / "meta" / "EUR_USD" / "EUR_USD_history_manifest.json"
            self.assertTrue(manifest_path.exists())
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["instrument"], "EUR_USD")
            self.assertEqual(len(manifest["datasets"]), 2)

    def test_load_candles_prefers_raw_dataset_covering_range(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            client = _FakeHistoricalClient()
            loader = HistoricalDataLoader(client, base)
            raw_dir = base / "raw" / "EUR_USD"
            raw_dir.mkdir(parents=True)

            dataset_path = raw_dir / "EUR_USD_H1_M_20260301T000000Z_20260303T000000Z.csv"
            frame = _frame(datetime(2026, 3, 1, tzinfo=timezone.utc), 48, "1h")
            frame.to_csv(dataset_path, index_label="time")

            sliced = loader.load_candles(
                "EUR_USD",
                "H1",
                start=datetime(2026, 3, 1, 12, tzinfo=timezone.utc),
                end=datetime(2026, 3, 1, 18, tzinfo=timezone.utc),
            )

            self.assertEqual(len(client.calls), 0)
            self.assertEqual(len(sliced), 6)
            self.assertEqual(sliced.index[0], pd.Timestamp("2026-03-01T12:00:00Z"))
            self.assertEqual(sliced.index[-1], pd.Timestamp("2026-03-01T17:00:00Z"))


if __name__ == "__main__":
    unittest.main()
