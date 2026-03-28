from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.backtesting.historical_fundamentals_provider import HistoricalFundamentalsProvider
from app.fundamentals.payloads import (
    HISTORICAL_NEWS_UNAVAILABLE,
    HISTORICAL_UNAVAILABLE,
    build_historical_fundamental_snapshot,
)


def _write_csv(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip() + "\n", encoding="utf-8")


class HistoricalFundamentalsProviderTests(unittest.TestCase):
    def test_missing_datasets_fall_back_to_historical_unavailable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            provider = HistoricalFundamentalsProvider(Path(tmpdir))
            snapshot = provider.snapshot(
                datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc),
                "NY Kill Zone",
            )

        self.assertEqual(
            snapshot,
            build_historical_fundamental_snapshot("NY Kill Zone"),
        )

    def test_provider_resolves_latest_known_historical_snapshot(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir) / "fundamentals"
            _write_csv(
                base / "rates" / "usd_policy_rates.csv",
                """
                effective_at,fed_target_lower_rate,fed_target_upper_rate,usd_rate,source
                2024-01-01T00:00:00Z,5.25,5.50,5.375,FOMC archive
                """,
            )
            _write_csv(
                base / "rates" / "eur_policy_rates.csv",
                """
                effective_at,ecb_main_refi_rate,ecb_marginal_lending_rate,ecb_deposit_rate,eur_rate,source
                2024-01-01T00:00:00Z,4.50,4.75,4.00,4.00,ECB archive
                """,
            )
            _write_csv(
                base / "dxy" / "dxy.csv",
                """
                time,close
                2024-01-14T00:00:00Z,101.24
                2024-01-15T00:00:00Z,100.90
                """,
            )
            _write_csv(
                base / "cot" / "eur_cot.csv",
                """
                publication_time,cot_net
                2024-01-12T20:30:00Z,-12345
                """,
            )
            _write_csv(
                base / "calendar" / "events.csv",
                """
                event_time,currency,event_name,importance
                2024-01-15T13:30:00Z,USD,CPI,high
                2024-01-16T13:15:00Z,EUR,ECB Rate Decision,high
                """,
            )

            provider = HistoricalFundamentalsProvider(Path(tmpdir))
            snapshot = provider.snapshot(
                datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc),
                "NY Kill Zone",
            )

        self.assertEqual(snapshot["usd_rate"], 5.375)
        self.assertEqual(snapshot["pair_rate"], 4.0)
        self.assertEqual(snapshot["rates_source"], "FOMC archive")
        self.assertTrue(str(snapshot["rate_differential"]).startswith("+1.38%"))
        self.assertEqual(snapshot["dxy_direction"], "FALLING")
        self.assertEqual(snapshot["dxy_level"], "100.90")
        self.assertEqual(snapshot["cot_net"], -12345.0)
        self.assertEqual(snapshot["cot_bias"], "BEARISH")
        self.assertEqual(snapshot["next_event_name"], "CPI")
        self.assertEqual(snapshot["next_news_event"], "CPI")
        self.assertEqual(snapshot["news_risk"], "MEDIUM")
        self.assertEqual(snapshot["active_session"], "NY Kill Zone")
        self.assertEqual(snapshot["kill_zone_active"], "YES — NY Kill Zone replay")
        self.assertTrue(snapshot["trade_window_active"])

    def test_provider_marks_clear_when_no_future_calendar_event_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir) / "fundamentals"
            _write_csv(
                base / "calendar" / "events.csv",
                """
                event_time,currency,event_name,importance
                2024-01-10T13:30:00Z,USD,CPI,high
                """,
            )

            provider = HistoricalFundamentalsProvider(Path(tmpdir))
            snapshot = provider.snapshot(
                datetime(2024, 1, 15, 12, 0, tzinfo=timezone.utc),
                "London Kill Zone",
            )

        self.assertTrue(snapshot["next_event_name"].startswith("CLEAR"))
        self.assertEqual(snapshot["news_risk"], "CLEAR")
        self.assertIsNone(snapshot["time_to_event"])
        self.assertEqual(snapshot["recent_headline"], HISTORICAL_NEWS_UNAVAILABLE)
