from __future__ import annotations

import unittest

from app.core.trade_management import (
    assess_early_momentum_exit,
    resolve_adaptive_time_stop_hours,
)


class TradeManagementTests(unittest.TestCase):
    def test_adaptive_time_stop_returns_base_window_when_disabled(self):
        hours, reasons = resolve_adaptive_time_stop_hours(
            {
                "adaptive_time_stop": False,
                "time_stop_hours": {"London Close": 3, "default": 8},
            },
            session="London Close",
            direction="SELL",
            technical_analysis={"ema_bias": "BEARISH", "market_regime": "HIGH_VOLATILITY"},
            macro_bias={"alignment": "ALIGNED"},
            confluence_score=95,
        )

        self.assertEqual(hours, 3.0)
        self.assertEqual(reasons, [])

    def test_adaptive_time_stop_caps_total_extension(self):
        hours, reasons = resolve_adaptive_time_stop_hours(
            {
                "adaptive_time_stop": True,
                "time_stop_hours": {"London Close": 3, "default": 8},
                "adaptive_time_stop_extensions": {
                    "trend_aligned_hours": 1.0,
                    "macro_aligned_hours": 0.5,
                    "trending_hours": 0.5,
                    "high_volatility_hours": 1.0,
                    "strong_signal_hours": 0.5,
                    "strong_signal_threshold": 85,
                    "max_total_hours": 2.0,
                },
            },
            session="London Close",
            direction="SELL",
            technical_analysis={"ema_bias": "BEARISH", "market_regime": "HIGH_VOLATILITY"},
            macro_bias={"alignment": "ALIGNED"},
            confluence_score=90,
        )

        self.assertEqual(hours, 5.0)
        self.assertEqual(
            reasons,
            ["trend_aligned", "macro_aligned", "high_volatility", "strong_confluence"],
        )

    def test_early_momentum_exit_triggers_when_gap_remains_too_wide(self):
        assessment = assess_early_momentum_exit(
            {
                "early_momentum_exit": True,
                "early_momentum_minutes": 60,
                "early_momentum_max_gap_pips": 15.0,
            },
            direction="SELL",
            entry_price=1.1000,
            tp2_price=1.0940,
            favorable_price=1.0975,
        )

        self.assertTrue(assessment.enabled)
        self.assertTrue(assessment.should_exit)
        self.assertAlmostEqual(assessment.gap_pips, 35.0, places=2)
        self.assertAlmostEqual(assessment.progress_ratio, 0.4167, places=4)

    def test_early_momentum_exit_allows_trade_when_gap_is_tight(self):
        assessment = assess_early_momentum_exit(
            {
                "early_momentum_exit": True,
                "early_momentum_minutes": 60,
                "early_momentum_max_gap_pips": 15.0,
            },
            direction="BUY",
            entry_price=1.1000,
            tp2_price=1.1040,
            favorable_price=1.1032,
        )

        self.assertTrue(assessment.enabled)
        self.assertFalse(assessment.should_exit)
        self.assertAlmostEqual(assessment.gap_pips, 8.0, places=2)
        self.assertAlmostEqual(assessment.progress_ratio, 0.8, places=4)


if __name__ == "__main__":
    unittest.main()
