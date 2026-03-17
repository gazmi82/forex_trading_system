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
        df = _frame(
            [
                {"open": 1.1490, "high": 1.1495, "low": 1.1488, "close": 1.1492},
                {"open": 1.1498, "high": 1.15003, "low": 1.1496, "close": 1.1499},
                {"open": 1.1501, "high": 1.1502, "low": 1.1499, "close": 1.1500},
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
        rows = []
        for i in range(20):
            low = 1.1000 + (0.002 * i)
            high = low + 0.010
            rows.append(
                {"open": low + 0.002, "high": high, "low": low, "close": low + 0.008}
            )
        df = _frame(rows)

        result = MarketStructureAnalyzer.analyze(df, "4H")

        self.assertEqual(result["trend"], "BULLISH")
        self.assertIn("Bullish 4H structure", result["structure"])


if __name__ == "__main__":
    unittest.main()
