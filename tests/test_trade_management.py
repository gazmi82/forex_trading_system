from __future__ import annotations

import unittest

from app.core.trade_management import resolve_adaptive_time_stop_hours


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
            mechanical_score=95,
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
            mechanical_score=90,
        )

        self.assertEqual(hours, 5.0)
        self.assertEqual(
            reasons,
            ["trend_aligned", "macro_aligned", "high_volatility", "strong_confluence"],
        )


if __name__ == "__main__":
    unittest.main()
