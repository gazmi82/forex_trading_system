from __future__ import annotations

import unittest
from unittest.mock import patch

from fundamentals_fetcher import get_auto_fundamentals


class FundamentalsFetcherTests(unittest.TestCase):
    @patch("fundamentals_fetcher.fetch_risk_sentiment")
    @patch("fundamentals_fetcher.fetch_retail_sentiment")
    @patch("fundamentals_fetcher.fetch_recent_fx_headline")
    @patch("fundamentals_fetcher.fetch_next_calendar_event")
    @patch("fundamentals_fetcher.fetch_cot_eur")
    @patch("fundamentals_fetcher.fetch_dxy")
    @patch("fundamentals_fetcher.fetch_policy_rates")
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

    @patch("fundamentals_fetcher.fetch_risk_sentiment", return_value={})
    @patch("fundamentals_fetcher.fetch_retail_sentiment", return_value={})
    @patch("fundamentals_fetcher.fetch_recent_fx_headline", return_value={})
    @patch("fundamentals_fetcher.fetch_next_calendar_event", return_value={})
    @patch("fundamentals_fetcher.fetch_cot_eur", return_value={})
    @patch("fundamentals_fetcher.fetch_dxy", return_value={})
    @patch("fundamentals_fetcher.fetch_policy_rates", return_value={})
    def test_get_auto_fundamentals_falls_back_to_manual_check(self, *_mocks):
        result = get_auto_fundamentals()

        self.assertIsNone(result["usd_rate"])
        self.assertTrue(result["rate_differential"].startswith("MANUAL_CHECK"))
        self.assertTrue(result["dxy_direction"].startswith("MANUAL_CHECK"))
        self.assertTrue(result["cot_bias"].startswith("MANUAL_CHECK"))
        self.assertTrue(result["next_news_event"].startswith("MANUAL_CHECK"))
        self.assertEqual(result["news_risk"], "HIGH")
        self.assertTrue(result["recent_headline"].startswith("MANUAL_CHECK"))
        self.assertTrue(result["retail_sentiment"].startswith("MANUAL_CHECK"))
        self.assertTrue(result["risk_sentiment"].startswith("MANUAL_CHECK"))


if __name__ == "__main__":
    unittest.main()
