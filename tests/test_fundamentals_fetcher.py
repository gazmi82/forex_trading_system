from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.fundamentals import get_auto_fundamentals as packaged_get_auto_fundamentals
import app.fundamentals.fetcher as fetcher_module
from app.fundamentals.fetcher import get_auto_fundamentals


class FundamentalsFetcherTests(unittest.TestCase):
    def test_app_fundamentals_init_reexports_fetcher_helper(self):
        self.assertIs(packaged_get_auto_fundamentals, get_auto_fundamentals)

    @patch("app.fundamentals.fetcher.fetch_risk_sentiment")
    @patch("app.fundamentals.fetcher.fetch_retail_sentiment")
    @patch("app.fundamentals.fetcher.fetch_recent_fx_headline")
    @patch("app.fundamentals.fetcher.fetch_next_calendar_event")
    @patch("app.fundamentals.fetcher.fetch_cot_eur")
    @patch("app.fundamentals.fetcher.fetch_dxy")
    @patch("app.fundamentals.fetcher.fetch_policy_rates")
    def test_get_auto_fundamentals_maps_live_values(
        self,
        mock_rates,
        mock_dxy,
        mock_cot,
        mock_calendar,
        mock_news,
        mock_retail,
        mock_risk,
    ):
        mock_rates.return_value = {
            "usd_rate": 4.5,
            "fed_target_lower_rate": 4.25,
            "fed_target_upper_rate": 4.75,
            "eur_rate": 2.5,
            "ecb_main_refi_rate": 2.65,
            "ecb_marginal_lending_rate": 2.9,
            "ecb_deposit_rate": 2.5,
            "rate_differential": "+2.00% USD favor supports bearish EUR/USD bias",
            "source": "rates",
        }
        mock_dxy.return_value = {"direction": "RISING", "level": 104.2}
        mock_cot.return_value = {"bias": "BULLISH", "net_str": "+12,000", "lm_str": "-4,000", "as_of": "2026-03-17"}
        mock_calendar.return_value = {
            "next_event_name": "USD — CPI",
            "next_news_event": "USD — CPI",
            "time_to_event": "25 minutes",
            "news_risk": "HIGH",
        }
        mock_news.return_value = {"headline": "EUR/USD headline"}
        mock_retail.return_value = {"sentiment": "62% SHORT, 38% LONG"}
        mock_risk.return_value = {"risk_sentiment": "RISK_OFF"}

        result = get_auto_fundamentals()

        self.assertEqual(result["usd_rate"], 4.5)
        self.assertEqual(result["eur_rate"], 2.5)
        self.assertEqual(result["dxy_direction"], "RISING")
        self.assertEqual(result["cot_bias"], "BULLISH")
        self.assertEqual(result["next_news_event"], "USD — CPI")
        self.assertEqual(result["news_risk"], "HIGH")
        self.assertEqual(result["recent_headline"], "EUR/USD headline")
        self.assertEqual(result["retail_sentiment"], "62% SHORT, 38% LONG")
        self.assertEqual(result["risk_sentiment"], "RISK_OFF")

    @patch("app.fundamentals.fetcher.fetch_risk_sentiment", return_value={})
    @patch("app.fundamentals.fetcher.fetch_retail_sentiment", return_value={})
    @patch("app.fundamentals.fetcher.fetch_recent_fx_headline", return_value={})
    @patch("app.fundamentals.fetcher.fetch_next_calendar_event", return_value={})
    @patch("app.fundamentals.fetcher.fetch_cot_eur", return_value={})
    @patch("app.fundamentals.fetcher.fetch_dxy", return_value={})
    @patch("app.fundamentals.fetcher.fetch_policy_rates", return_value={})
    def test_get_auto_fundamentals_falls_back_to_neutral_values(self, *_mocks):
        # March 20 fix: empty feeds now return safe neutral values instead of
        # MANUAL_CHECK strings, so Claude never receives unhandled sentinel tokens.
        result = get_auto_fundamentals()

        self.assertIsNone(result["usd_rate"])
        self.assertEqual(result["rate_differential"], "N/A (live Fed/ECB data unavailable)")
        self.assertEqual(result["dxy_direction"], "NEUTRAL")
        self.assertEqual(result["cot_bias"], "NEUTRAL")
        self.assertEqual(result["next_news_event"], "MANUAL_CHECK")   # calendar still uses MANUAL_CHECK
        self.assertEqual(result["news_risk"], "HIGH")
        self.assertEqual(result["recent_headline"], "None available")
        self.assertEqual(result["retail_sentiment"], "NEUTRAL")
        self.assertEqual(result["risk_sentiment"], "NEUTRAL")

    @patch("app.fundamentals.providers.requests.get")
    def test_calendar_fetch_uses_cache_on_immediate_repeat(self, mock_get):
        original_cache = fetcher_module._calendar_cache
        original_cache_time = fetcher_module._calendar_cache_time
        try:
            fetcher_module._calendar_cache = {}
            fetcher_module._calendar_cache_time = None

            class MockResponse:
                def raise_for_status(self):
                    return None

                def json(self):
                    return [
                        {
                            "country": "USD",
                            "impact": "High",
                            "title": "CPI",
                            "date": "2030-03-18T15:00:00Z",
                        }
                    ]

            mock_get.return_value = MockResponse()

            first = fetcher_module.fetch_next_calendar_event(force_refresh=False)
            second = fetcher_module.fetch_next_calendar_event(force_refresh=False)

            self.assertEqual(first["next_event_name"], "USD — CPI")
            self.assertEqual(second["next_event_name"], "USD — CPI")
            self.assertEqual(mock_get.call_count, 2)
        finally:
            fetcher_module._calendar_cache = original_cache
            fetcher_module._calendar_cache_time = original_cache_time

    def test_calendar_cached_event_recomputes_time_to_event_and_risk(self):
        original_cache = fetcher_module._calendar_cache
        original_cache_time = fetcher_module._calendar_cache_time
        try:
            base_now = datetime(2030, 3, 18, 12, 0, tzinfo=timezone.utc)
            fetcher_module._calendar_cache = {
                "next_event_name": "USD — CPI",
                "next_news_event": "USD — CPI",
                "time_to_event": "8 hours",
                "news_risk": "LOW",
                "event_time": (base_now + timedelta(hours=2)).isoformat(),
                "upcoming_events": [
                    {
                        "country": "USD",
                        "event": "CPI",
                        "event_time": (base_now + timedelta(hours=2)).isoformat(),
                    }
                ],
            }
            fetcher_module._calendar_cache_time = base_now

            class _FixedDateTime(datetime):
                @classmethod
                def now(cls, tz=None):
                    return base_now if tz is None else base_now.astimezone(tz)

            with patch("app.fundamentals.fetcher.datetime", _FixedDateTime):
                result = fetcher_module.fetch_next_calendar_event(force_refresh=False)

            self.assertEqual(result["time_to_event"], "2 hours")
            self.assertEqual(result["news_risk"], "MEDIUM")
        finally:
            fetcher_module._calendar_cache = original_cache
            fetcher_module._calendar_cache_time = original_cache_time

    def test_calendar_cache_advances_to_next_cached_event_without_refetch(self):
        original_cache = fetcher_module._calendar_cache
        original_cache_time = fetcher_module._calendar_cache_time
        try:
            base_now = datetime(2030, 3, 18, 12, 0, tzinfo=timezone.utc)
            fetcher_module._calendar_cache = {
                "next_event_name": "USD — CPI",
                "next_news_event": "USD — CPI",
                "time_to_event": "15 minutes",
                "news_risk": "HIGH",
                "event_time": (base_now + timedelta(minutes=15)).isoformat(),
                "upcoming_events": [
                    {
                        "country": "USD",
                        "event": "CPI",
                        "event_time": (base_now - timedelta(minutes=45)).isoformat(),
                    },
                    {
                        "country": "EUR",
                        "event": "ECB Presser",
                        "event_time": (base_now + timedelta(hours=1)).isoformat(),
                    },
                ],
            }
            fetcher_module._calendar_cache_time = base_now

            class _FixedDateTime(datetime):
                @classmethod
                def now(cls, tz=None):
                    return base_now if tz is None else base_now.astimezone(tz)

            with patch("app.fundamentals.fetcher.datetime", _FixedDateTime):
                result = fetcher_module.fetch_next_calendar_event(force_refresh=False)

            self.assertEqual(result["next_event_name"], "EUR — ECB Presser")
        finally:
            fetcher_module._calendar_cache = original_cache
            fetcher_module._calendar_cache_time = original_cache_time

    @patch("app.fundamentals.fetcher.build_calendar_snapshot")
    def test_calendar_cache_refreshes_on_new_fixed_slot(self, mock_build_calendar):
        original_cache = fetcher_module._calendar_cache
        original_cache_time = fetcher_module._calendar_cache_time
        try:
            base_now = datetime(2030, 3, 18, 12, 1, tzinfo=timezone.utc)
            fetcher_module._calendar_cache = {
                "next_event_name": "USD — CPI",
                "next_news_event": "USD — CPI",
                "time_to_event": "5 hours",
                "news_risk": "LOW",
                "event_time": (base_now + timedelta(hours=5)).isoformat(),
                "upcoming_events": [
                    {
                        "country": "USD",
                        "event": "CPI",
                        "event_time": (base_now + timedelta(hours=5)).isoformat(),
                    }
                ],
            }
            fetcher_module._calendar_cache_time = datetime(2030, 3, 18, 5, 59, tzinfo=timezone.utc)
            mock_build_calendar.return_value = {
                "next_event_name": "EUR — ECB Presser",
                "next_news_event": "EUR — ECB Presser",
                "time_to_event": "1 hour",
                "news_risk": "MEDIUM",
                "event_time": (base_now + timedelta(hours=1)).isoformat(),
                "upcoming_events": [
                    {
                        "country": "EUR",
                        "event": "ECB Presser",
                        "event_time": (base_now + timedelta(hours=1)).isoformat(),
                    }
                ],
            }

            class _FixedDateTime(datetime):
                @classmethod
                def now(cls, tz=None):
                    return base_now if tz is None else base_now.astimezone(tz)

            with patch("app.fundamentals.fetcher.datetime", _FixedDateTime):
                result = fetcher_module.fetch_next_calendar_event(force_refresh=False)

            self.assertEqual(result["next_event_name"], "EUR — ECB Presser")
            mock_build_calendar.assert_called_once()
        finally:
            fetcher_module._calendar_cache = original_cache
            fetcher_module._calendar_cache_time = original_cache_time


if __name__ == "__main__":
    unittest.main()
