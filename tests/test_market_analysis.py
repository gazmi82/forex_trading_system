from __future__ import annotations

import unittest

import pandas as pd

from app.analysis.market_analysis import IndicatorCalculator, MarketStructureAnalyzer
from app.brokers.oanda import (
    IndicatorCalculator as PackagedOandaIndicatorCalculator,
    MarketStructureAnalyzer as PackagedOandaMarketStructureAnalyzer,
)
from market_analysis import (
    IndicatorCalculator as RootIndicatorCalculator,
    MarketStructureAnalyzer as RootMarketStructureAnalyzer,
)
from oanda_connector import (
    IndicatorCalculator as OandaIndicatorCalculator,
    MarketStructureAnalyzer as OandaMarketStructureAnalyzer,
)


def _frame(rows: list[dict[str, float]]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df["time"] = pd.date_range("2026-03-01", periods=len(df), freq="h")
    return df.set_index("time")


def _frame_from_highs_lows(highs: list[float], lows: list[float]) -> pd.DataFrame:
    rows = []
    for high, low in zip(highs, lows, strict=True):
        spread = high - low
        rows.append(
            {
                "open": round(low + (spread * 0.35), 5),
                "high": high,
                "low": low,
                "close": round(low + (spread * 0.7), 5),
            }
        )
    return _frame(rows)


class MarketAnalysisTests(unittest.TestCase):
    def test_root_market_analysis_reexports_shared_analysis_classes(self):
        self.assertIs(RootIndicatorCalculator, IndicatorCalculator)
        self.assertIs(RootMarketStructureAnalyzer, MarketStructureAnalyzer)

    def test_oanda_connector_reexports_shared_analysis_classes(self):
        self.assertIs(OandaIndicatorCalculator, IndicatorCalculator)
        self.assertIs(OandaMarketStructureAnalyzer, MarketStructureAnalyzer)
        self.assertIs(PackagedOandaIndicatorCalculator, IndicatorCalculator)
        self.assertIs(PackagedOandaMarketStructureAnalyzer, MarketStructureAnalyzer)

    def test_find_bullish_fvg_returns_latest_gap(self):
        # Bullish FVG requires: c1.high < c3.low (gap exists) AND
        # c2 (impulse candle) low >= c1.high (impulse never breached back into the gap).
        # March 20 fix tightened the second condition to remove false positives.
        df = _frame(
            [
                {"open": 1.1490, "high": 1.1495, "low": 1.1488, "close": 1.1492},
                {"open": 1.1498, "high": 1.15003, "low": 1.1496, "close": 1.1499},
                # c2 (impulse): low must be >= c1.high (1.15003) to pass validation
                {"open": 1.1502, "high": 1.1512, "low": 1.15005, "close": 1.1510},
                {"open": 1.1509, "high": 1.1511, "low": 1.1508, "close": 1.1510},
                {"open": 1.1510, "high": 1.1512, "low": 1.1509, "close": 1.1511},
            ]
        )

        result = IndicatorCalculator._find_fvg(df, "bullish")

        self.assertEqual(result, "1.15003–1.1508 (1H, unfilled)")

    def test_ote_zone_returns_bullish_retracement_band(self):
        rows = []
        for i in range(20):
            low = 1.1000 + (0.005 * i)
            high = low + 0.005
            close = high if i == 19 else low + 0.003
            rows.append(
                {"open": low + 0.001, "high": high, "low": low, "close": close}
            )
        rows[0]["low"] = 1.1000
        rows[-1]["high"] = 1.2000
        rows[-1]["close"] = 1.1900

        df = _frame(rows)

        result = IndicatorCalculator._ote_zone(df)

        self.assertEqual(result, [1.121, 1.138])

    def test_market_structure_analyzer_detects_bullish_structure(self):
        df = _frame_from_highs_lows(
            highs=[
                1.1010, 1.1030, 1.1060, 1.1100, 1.1080,
                1.1065, 1.1075, 1.1105, 1.1145, 1.1180,
                1.1165, 1.1140, 1.1155, 1.1170, 1.1160,
            ],
            lows=[
                1.0990, 1.1005, 1.1025, 1.1055, 1.1040,
                1.1030, 1.1045, 1.1070, 1.1110, 1.1145,
                1.1130, 1.1120, 1.1135, 1.1150, 1.1140,
            ],
        )

        result = MarketStructureAnalyzer.analyze(df, "4H")

        self.assertEqual(result["trend"], "BULLISH")
        self.assertIn("Bullish 4H structure", result["structure"])

    def test_market_structure_analyzer_detects_bearish_structure(self):
        df = _frame_from_highs_lows(
            highs=[
                1.1210, 1.1195, 1.1170, 1.1200, 1.1180, 1.1160,
                1.1145, 1.1125, 1.1090, 1.1130, 1.1110, 1.1090,
                1.1075, 1.1080, 1.1065, 1.1055,
            ],
            lows=[
                1.1185, 1.1165, 1.1145, 1.1155, 1.1140, 1.1130,
                1.1145, 1.1150, 1.1135, 1.1120, 1.1095, 1.1060,
                1.1075, 1.1080, 1.1065, 1.1050,
            ],
        )

        result = MarketStructureAnalyzer.analyze(df, "1H")

        self.assertEqual(result["trend"], "BEARISH")
        self.assertIn("Bearish 1H structure", result["structure"])

    def test_market_structure_analyzer_detects_contracting_range(self):
        df = _frame_from_highs_lows(
            highs=[
                1.1010, 1.1040, 1.1070, 1.1100, 1.1080, 1.1060,
                1.1075, 1.1065, 1.1055, 1.1080, 1.1065, 1.1045,
                1.1050, 1.1040, 1.1035, 1.1030,
            ],
            lows=[
                1.0985, 1.1000, 1.1025, 1.1040, 1.1025, 1.1000,
                1.1015, 1.1025, 1.1035, 1.1040, 1.1030, 1.1020,
                1.1035, 1.1045, 1.1040, 1.1035,
            ],
        )

        result = MarketStructureAnalyzer.analyze(df, "15M")

        self.assertEqual(result["trend"], "NEUTRAL")
        self.assertIn("Contracting range 15M", result["structure"])

    def test_market_structure_analyzer_detects_expanding_range(self):
        df = _frame_from_highs_lows(
            highs=[
                1.1010, 1.1030, 1.1060, 1.1100, 1.1080,
                1.1065, 1.1075, 1.1105, 1.1145, 1.1180,
                1.1165, 1.1140, 1.1155, 1.1170, 1.1160,
            ],
            lows=[
                1.0990, 1.1005, 1.1025, 1.1055, 1.1040,
                1.1030, 1.1045, 1.1070, 1.1110, 1.1145,
                1.1130, 1.1000, 1.1015, 1.1030, 1.1020,
            ],
        )

        result = MarketStructureAnalyzer.analyze(df, "Daily")

        self.assertEqual(result["trend"], "NEUTRAL")
        self.assertIn("Expanding range Daily", result["structure"])

    def test_market_structure_analyzer_ignores_unconfirmed_terminal_wick(self):
        df = _frame_from_highs_lows(
            highs=[
                1.1010, 1.1030, 1.1060, 1.1100, 1.1080,
                1.1065, 1.1075, 1.1105, 1.1145, 1.1180,
                1.1165, 1.1140, 1.1155, 1.1170, 1.1160,
                1.1175, 1.1185,
            ],
            lows=[
                1.0990, 1.1005, 1.1025, 1.1055, 1.1040,
                1.1030, 1.1045, 1.1070, 1.1110, 1.1145,
                1.1130, 1.1120, 1.1135, 1.1150, 1.1140,
                1.1145, 1.1080,
            ],
        )

        result = MarketStructureAnalyzer.analyze(df, "4H")

        self.assertEqual(result["trend"], "BULLISH")
        self.assertIn("Bullish 4H structure", result["structure"])


if __name__ == "__main__":
    unittest.main()
