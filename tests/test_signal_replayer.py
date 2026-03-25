from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from app.backtesting import HistoricalDataLoader, SignalReplayEngine
from app.backtesting.signal_replayer import iter_kill_zone_windows


def _wave_frame(start: str, periods: int, freq: str, *, drift: float) -> pd.DataFrame:
    index = pd.date_range(start=start, periods=periods, freq=freq, tz="UTC")
    rows = []
    base = 1.0700
    for idx, _ in enumerate(index):
        wave = ((idx % 8) - 3.5) * 0.00008
        close = base + (idx * drift) + wave
        open_ = close - 0.00006
        high = max(open_, close) + 0.00018
        low = min(open_, close) - 0.00018
        rows.append(
            {
                "open": round(open_, 5),
                "high": round(high, 5),
                "low": round(low, 5),
                "close": round(close, 5),
                "volume": 100 + (idx % 20),
            }
        )
    return pd.DataFrame(rows, index=index)


def _write_raw_dataset(base: Path, instrument: str, granularity: str, frame: pd.DataFrame):
    instrument_slug = instrument.replace("/", "_").upper()
    dataset_dir = base / "raw" / instrument_slug
    dataset_dir.mkdir(parents=True, exist_ok=True)
    start_slug = frame.index[0].strftime("%Y%m%dT%H%M%SZ")
    end_slug = (frame.index[-1] + _granularity_delta(granularity)).strftime("%Y%m%dT%H%M%SZ")
    path = dataset_dir / f"{instrument_slug}_{granularity}_M_{start_slug}_{end_slug}.csv"
    frame.to_csv(path, index_label="time")


def _granularity_delta(granularity: str) -> pd.Timedelta:
    mapping = {
        "M1": pd.Timedelta(minutes=1),
        "H1": pd.Timedelta(hours=1),
        "H4": pd.Timedelta(hours=4),
        "D": pd.Timedelta(days=1),
        "W": pd.Timedelta(weeks=1),
    }
    return mapping[granularity]


class _RemoteForbiddenClient:
    def __init__(self):
        self.calls = 0

    def get_candles_range(self, *args, **kwargs):
        self.calls += 1
        raise AssertionError("Replay should not fetch remote data when local datasets exist")


class SignalReplayTests(unittest.TestCase):
    def test_iter_kill_zone_windows_uses_new_york_schedule(self):
        windows = list(
            iter_kill_zone_windows(
                datetime(2026, 3, 25, 0, 0, tzinfo=timezone.utc),
                datetime(2026, 3, 26, 0, 0, tzinfo=timezone.utc),
            )
        )

        self.assertEqual(
            [(window.session, window.timestamp.isoformat()) for window in windows],
            [
                ("London Kill Zone", "2026-03-25T07:00:00+00:00"),
                ("NY Kill Zone", "2026-03-25T12:00:00+00:00"),
                ("London Close", "2026-03-25T14:00:00+00:00"),
            ],
        )

    def test_replay_uses_local_raw_datasets_and_writes_one_record_per_window(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            instrument = "EUR_USD"
            _write_raw_dataset(base, instrument, "M1", _wave_frame("2026-03-23T00:00:00Z", 4 * 24 * 60, "1min", drift=0.000004))
            _write_raw_dataset(base, instrument, "H1", _wave_frame("2026-03-01T00:00:00Z", 30 * 24, "1h", drift=0.00005))
            _write_raw_dataset(base, instrument, "H4", _wave_frame("2025-10-01T00:00:00Z", 1200, "4h", drift=0.00003))
            _write_raw_dataset(base, instrument, "D", _wave_frame("2025-01-01T00:00:00Z", 450, "1d", drift=0.0004))
            _write_raw_dataset(base, instrument, "W", _wave_frame("2024-01-05T22:00:00Z", 120, "1W-FRI", drift=0.0012))

            client = _RemoteForbiddenClient()
            loader = HistoricalDataLoader(client, base)
            output_root = base / "results"
            replayer = SignalReplayEngine(loader, output_root=output_root)

            summary = replayer.replay(
                instrument,
                start=datetime(2026, 3, 25, 0, 0, tzinfo=timezone.utc),
                end=datetime(2026, 3, 26, 0, 0, tzinfo=timezone.utc),
                local_only=True,
            )

            self.assertEqual(client.calls, 0)
            self.assertEqual(summary.total_windows, 3)
            output_path = Path(summary.output_path)
            self.assertTrue(output_path.exists())

            rows = [
                json.loads(line)
                for line in output_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(rows), 3)
            self.assertEqual([row["session"] for row in rows], ["London Kill Zone", "NY Kill Zone", "London Close"])
            self.assertTrue(all(row["analysis_source"] == "MECHANICAL_REPLAY" for row in rows))
            self.assertTrue(all("signal" in row for row in rows))
            self.assertTrue(all("entry_zone" in row["signal"] for row in rows))
            self.assertTrue(all("take_profit_1" in row["signal"] for row in rows))
            self.assertTrue(all("take_profit_2" in row["signal"] for row in rows))
            self.assertTrue(all("mechanical_confluence_score" in row for row in rows))
            self.assertTrue(all("technical_analysis" in row for row in rows))
            self.assertTrue(all("atr_1h" in row["technical_analysis"] for row in rows))


if __name__ == "__main__":
    unittest.main()
