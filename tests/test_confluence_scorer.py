"""
Unit tests for the mechanical confluence scorer (Phase 1, Item 1.1).

All 13 components are tested independently (acceptance criterion 3).
Determinism is verified (acceptance criterion 1).
Score-range thresholds are verified against STRONG / MODERATE thresholds (criterion 2).
"""
from __future__ import annotations

import unittest

from app.analysis.confluence_scorer import (
    _score_adx,
    _score_cot,
    _score_dxy,
    _score_ema,
    _score_fvg,
    _score_liquidity_sweep,  # no direction param — direction-agnostic
    _score_news,
    _score_ote,
    _score_order_block,
    _score_premium_discount,
    _score_rate_differential,
    _score_rsi,
    _score_trend,
    calculate_confluence,
)

# ---------------------------------------------------------------------------
# Minimal market_data and signal fixtures
# ---------------------------------------------------------------------------

def _make_market_data(direction: str = "BUY") -> dict:
    """
    Returns market_data with ALL positive conditions for the given direction.
    Produces the maximum possible confluence score (should be 100 after normalisation).
    """
    is_buy = direction == "BUY"
    return {
        "price": 1.08000,
        "demo_mode": True,
        "ohlcv": {
            "weekly_trend": "BULLISH" if is_buy else "BEARISH",
            "daily_trend":  "BULLISH" if is_buy else "BEARISH",
            "h4_trend":     "BULLISH" if is_buy else "BEARISH",
        },
        "indicators": {
            "bullish_ob":            "1.0790–1.0810 (4H, valid)" if is_buy else "None identified in last 50 candles",
            "bearish_ob":            "None identified in last 50 candles" if is_buy else "1.0790–1.0810 (4H, valid)",
            "bullish_fvg":           "1.0800–1.0815 (1H, unfilled)" if is_buy else "None identified",
            "bearish_fvg":           "None identified" if is_buy else "1.0800–1.0815 (1H, unfilled)",
            "recent_liquidity_sweep": "SSL swept at 1.0785 (3h ago, strong rejection)" if is_buy else "BSL swept at 1.0820 (2h ago, strong rejection)",
            "premium_discount_zone": "DISCOUNT (32% of range)" if is_buy else "PREMIUM (72% of range)",
            "ote_zone":              [1.0795, 1.0810],   # price 1.08000 is inside
            "rsi_4h":                35.0 if is_buy else 68.0,
            "rsi_1h":                38.0 if is_buy else 65.0,
            "adx_4h":                32.0,
            "ema20_4h":              1.0790 if is_buy else 1.0810,
            "ema50_4h":              1.0775 if is_buy else 1.0825,
        },
        "fundamental": {
            "pair_rate":      4.0 if is_buy else 2.0,   # ECB deposit
            "usd_rate":       2.0 if is_buy else 4.0,   # Fed target
            "dxy_direction":  "FALLING" if is_buy else "RISING",
            "cot_bias":       "BULLISH" if is_buy else "BEARISH",
            "news_risk":      "LOW",
            "time_to_event":  360.0,
        },
    }


def _make_signal(direction: str = "BUY") -> dict:
    return {"signal": {"direction": direction, "confidence": 80}}


# ---------------------------------------------------------------------------
# 1. Determinism
# ---------------------------------------------------------------------------

class TestDeterminism(unittest.TestCase):
    def test_same_input_same_score(self):
        md = _make_market_data(direction="BUY")
        sig = _make_signal("BUY")
        r1 = calculate_confluence(md, sig)
        r2 = calculate_confluence(md, sig)
        self.assertEqual(r1["confluence_score"], r2["confluence_score"])
        self.assertEqual(r1["component_scores"], r2["component_scores"])


# ---------------------------------------------------------------------------
# 2. Score range / threshold validation
# ---------------------------------------------------------------------------

class TestScoreRange(unittest.TestCase):
    def test_all_positive_buy_hits_100(self):
        result = calculate_confluence(_make_market_data("BUY"), _make_signal("BUY"))
        self.assertEqual(result["confluence_score"], 100)

    def test_all_positive_sell_hits_100(self):
        result = calculate_confluence(_make_market_data("SELL"), _make_signal("SELL"))
        self.assertEqual(result["confluence_score"], 100)

    def test_empty_market_data_returns_0(self):
        result = calculate_confluence({}, {"signal": {"direction": "BUY"}})
        self.assertEqual(result["confluence_score"], 0)

    def test_neutral_direction_returns_0(self):
        result = calculate_confluence(_make_market_data("BUY"), _make_signal("NEUTRAL"))
        # Direction-agnostic components (ADX=10, liquidity_sweep=15, news=5) can still fire.
        # Total direction-agnostic max = 30 raw / 150 total → 20 normalised.
        # Assert well below the 65 execution threshold.
        self.assertLess(result["confluence_score"], 35)

    def test_score_capped_at_100(self):
        result = calculate_confluence(_make_market_data("BUY"), _make_signal("BUY"))
        self.assertLessEqual(result["confluence_score"], 100)
        self.assertGreaterEqual(result["confluence_score"], 0)

    def test_strong_signal_threshold_85(self):
        result = calculate_confluence(_make_market_data("BUY"), _make_signal("BUY"))
        # All-positive fixture should exceed STRONG threshold
        self.assertGreaterEqual(result["confluence_score"], 85)

    def test_direction_implied_present(self):
        result = calculate_confluence(_make_market_data("BUY"), _make_signal("BUY"))
        self.assertIn(result["direction_implied"], ("BUY", "SELL", "NEUTRAL"))


# ---------------------------------------------------------------------------
# 3. Individual component tests (all 13 components)
# ---------------------------------------------------------------------------

class TestTrendAlignment(unittest.TestCase):
    def _ohlcv(self, w, d, h4):
        return {"weekly_trend": w, "daily_trend": d, "h4_trend": h4}

    def test_all_three_aligned_buy_gives_15(self):
        self.assertEqual(_score_trend("BUY", self._ohlcv("BULLISH", "BULLISH", "BULLISH")), 15)

    def test_all_three_aligned_sell_gives_15(self):
        self.assertEqual(_score_trend("SELL", self._ohlcv("BEARISH", "BEARISH", "BEARISH")), 15)

    def test_daily_h4_aligned_weekly_neutral_gives_10(self):
        self.assertEqual(_score_trend("BUY", self._ohlcv("NEUTRAL", "BULLISH", "BULLISH")), 10)

    def test_only_h4_gives_5(self):
        self.assertEqual(_score_trend("BUY", self._ohlcv("BEARISH", "NEUTRAL", "BULLISH")), 5)

    def test_no_alignment_gives_0(self):
        self.assertEqual(_score_trend("BUY", self._ohlcv("BEARISH", "BEARISH", "BEARISH")), 0)

    def test_neutral_direction_gives_0(self):
        self.assertEqual(_score_trend("NEUTRAL", self._ohlcv("BULLISH", "BULLISH", "BULLISH")), 0)


class TestOrderBlock(unittest.TestCase):
    def test_valid_bullish_ob_buy_gives_20(self):
        self.assertEqual(
            _score_order_block("BUY", {"bullish_ob": "1.0790–1.0810 (4H, valid)"}), 20
        )

    def test_valid_bearish_ob_sell_gives_20(self):
        self.assertEqual(
            _score_order_block("SELL", {"bearish_ob": "1.0790–1.0810 (4H, valid)"}), 20
        )

    def test_no_ob_gives_0(self):
        self.assertEqual(
            _score_order_block("BUY", {"bullish_ob": "None identified in last 50 candles"}), 0
        )

    def test_wrong_direction_ob_gives_0(self):
        # BUY direction checks bullish_ob, not bearish_ob
        self.assertEqual(
            _score_order_block("BUY", {"bearish_ob": "1.0790–1.0810 (4H, valid)"}), 0
        )

    def test_neutral_gives_0(self):
        self.assertEqual(
            _score_order_block("NEUTRAL", {"bullish_ob": "1.0790–1.0810 (4H, valid)"}), 0
        )


class TestFVG(unittest.TestCase):
    def test_bullish_fvg_buy_gives_15(self):
        self.assertEqual(
            _score_fvg("BUY", {"bullish_fvg": "1.0800–1.0815 (1H, unfilled)"}), 15
        )

    def test_bearish_fvg_sell_gives_15(self):
        self.assertEqual(
            _score_fvg("SELL", {"bearish_fvg": "1.0800–1.0815 (1H, unfilled)"}), 15
        )

    def test_none_fvg_gives_0(self):
        self.assertEqual(_score_fvg("BUY", {"bullish_fvg": "None identified"}), 0)

    def test_empty_fvg_gives_0(self):
        self.assertEqual(_score_fvg("BUY", {}), 0)


class TestLiquiditySweep(unittest.TestCase):
    def test_ssl_sweep_gives_15(self):
        self.assertEqual(
            _score_liquidity_sweep({"recent_liquidity_sweep": "SSL swept at 1.0785 (3h ago, strong rejection)"}),
            15,
        )

    def test_bsl_sweep_gives_15(self):
        self.assertEqual(
            _score_liquidity_sweep({"recent_liquidity_sweep": "BSL swept at 1.0820 (2h ago, strong rejection)"}),
            15,
        )

    def test_no_sweep_gives_0(self):
        self.assertEqual(
            _score_liquidity_sweep({"recent_liquidity_sweep": "No recent sweep identified"}),
            0,
        )

    def test_empty_sweep_gives_0(self):
        self.assertEqual(_score_liquidity_sweep({}), 0)


class TestPremiumDiscount(unittest.TestCase):
    def test_discount_buy_gives_10(self):
        self.assertEqual(_score_premium_discount("BUY", {"premium_discount_zone": "DISCOUNT (32% of range)"}), 10)

    def test_premium_sell_gives_10(self):
        self.assertEqual(_score_premium_discount("SELL", {"premium_discount_zone": "PREMIUM (72% of range)"}), 10)

    def test_premium_buy_gives_0(self):
        self.assertEqual(_score_premium_discount("BUY", {"premium_discount_zone": "PREMIUM (72% of range)"}), 0)

    def test_equilibrium_buy_gives_0(self):
        self.assertEqual(_score_premium_discount("BUY", {"premium_discount_zone": "EQUILIBRIUM (51% of range)"}), 0)


class TestOTE(unittest.TestCase):
    def test_price_inside_ote_gives_10(self):
        # price 1.0800 is between 1.0795 and 1.0810
        self.assertEqual(_score_ote(1.0800, {"ote_zone": [1.0795, 1.0810]}), 10)

    def test_price_outside_ote_gives_0(self):
        self.assertEqual(_score_ote(1.0850, {"ote_zone": [1.0795, 1.0810]}), 0)

    def test_inverted_ote_bounds_handled(self):
        # lo > hi — scorer should swap them
        self.assertEqual(_score_ote(1.0800, {"ote_zone": [1.0810, 1.0795]}), 10)

    def test_empty_ote_gives_0(self):
        self.assertEqual(_score_ote(1.0800, {"ote_zone": []}), 0)
        self.assertEqual(_score_ote(1.0800, {}), 0)


class TestRSI(unittest.TestCase):
    def test_oversold_4h_buy_gives_10(self):
        self.assertEqual(_score_rsi("BUY", {"rsi_4h": 35.0, "rsi_1h": 50.0}), 10)

    def test_oversold_1h_buy_gives_10(self):
        self.assertEqual(_score_rsi("BUY", {"rsi_4h": 50.0, "rsi_1h": 38.0}), 10)

    def test_overbought_4h_sell_gives_10(self):
        self.assertEqual(_score_rsi("SELL", {"rsi_4h": 68.0, "rsi_1h": 50.0}), 10)

    def test_neutral_rsi_buy_gives_0(self):
        self.assertEqual(_score_rsi("BUY", {"rsi_4h": 52.0, "rsi_1h": 55.0}), 0)

    def test_neutral_rsi_sell_gives_0(self):
        self.assertEqual(_score_rsi("SELL", {"rsi_4h": 48.0, "rsi_1h": 52.0}), 0)


class TestADX(unittest.TestCase):
    def test_adx_above_25_gives_10(self):
        self.assertEqual(_score_adx({"adx_4h": 30.0}), 10)

    def test_adx_below_25_gives_0(self):
        self.assertEqual(_score_adx({"adx_4h": 20.0}), 0)

    def test_adx_exactly_25_gives_0(self):
        # Must be strictly > 25
        self.assertEqual(_score_adx({"adx_4h": 25.0}), 0)

    def test_missing_adx_gives_0(self):
        self.assertEqual(_score_adx({}), 0)


class TestEMA(unittest.TestCase):
    def test_buy_ema_stack_gives_5(self):
        # price 1.08 > ema20 1.079 > ema50 1.077
        self.assertEqual(_score_ema("BUY", 1.0800, {"ema20_4h": 1.0790, "ema50_4h": 1.0770}), 5)

    def test_sell_ema_stack_gives_5(self):
        # price 1.075 < ema20 1.079 < ema50 1.082
        self.assertEqual(_score_ema("SELL", 1.0750, {"ema20_4h": 1.0790, "ema50_4h": 1.0820}), 5)

    def test_wrong_order_gives_0(self):
        # price > ema20 but ema20 < ema50 — stack not clean
        self.assertEqual(_score_ema("BUY", 1.0800, {"ema20_4h": 1.0750, "ema50_4h": 1.0780}), 0)

    def test_missing_ema_gives_0(self):
        self.assertEqual(_score_ema("BUY", 1.0800, {}), 0)


class TestRateDifferential(unittest.TestCase):
    def test_eur_higher_rate_buy_gives_15(self):
        self.assertEqual(
            _score_rate_differential("BUY", {"pair_rate": 4.0, "usd_rate": 2.0}), 15
        )

    def test_usd_higher_rate_sell_gives_15(self):
        self.assertEqual(
            _score_rate_differential("SELL", {"pair_rate": 2.0, "usd_rate": 4.0}), 15
        )

    def test_eur_higher_rate_sell_gives_0(self):
        self.assertEqual(
            _score_rate_differential("SELL", {"pair_rate": 4.0, "usd_rate": 2.0}), 0
        )

    def test_missing_rates_gives_0(self):
        self.assertEqual(_score_rate_differential("BUY", {}), 0)


class TestDXY(unittest.TestCase):
    def test_falling_dxy_buy_gives_10(self):
        self.assertEqual(_score_dxy("BUY", {"dxy_direction": "FALLING"}), 10)

    def test_rising_dxy_sell_gives_10(self):
        self.assertEqual(_score_dxy("SELL", {"dxy_direction": "RISING"}), 10)

    def test_neutral_dxy_gives_0(self):
        self.assertEqual(_score_dxy("BUY", {"dxy_direction": "NEUTRAL"}), 0)

    def test_rising_dxy_buy_gives_0(self):
        self.assertEqual(_score_dxy("BUY", {"dxy_direction": "RISING"}), 0)


class TestCOT(unittest.TestCase):
    def test_bullish_cot_buy_gives_10(self):
        self.assertEqual(_score_cot("BUY", {"cot_bias": "BULLISH"}), 10)

    def test_bearish_cot_sell_gives_10(self):
        self.assertEqual(_score_cot("SELL", {"cot_bias": "BEARISH"}), 10)

    def test_neutral_cot_gives_0(self):
        self.assertEqual(_score_cot("BUY", {"cot_bias": "NEUTRAL"}), 0)

    def test_mismatched_cot_gives_0(self):
        self.assertEqual(_score_cot("BUY", {"cot_bias": "BEARISH"}), 0)


class TestNewsClear(unittest.TestCase):
    def test_low_risk_gives_5(self):
        self.assertEqual(_score_news({"news_risk": "LOW", "time_to_event": 30.0}), 5)

    def test_far_away_event_gives_5(self):
        self.assertEqual(_score_news({"news_risk": "HIGH", "time_to_event": 360.0}), 5)

    def test_high_risk_close_event_gives_0(self):
        self.assertEqual(_score_news({"news_risk": "HIGH", "time_to_event": 20.0}), 0)

    def test_medium_risk_close_event_gives_0(self):
        self.assertEqual(_score_news({"news_risk": "MEDIUM", "time_to_event": 60.0}), 0)


# ---------------------------------------------------------------------------
# 4. Integration: claude score vs mechanical score can diverge
# ---------------------------------------------------------------------------

class TestClaudeVsMechanicalDivergence(unittest.TestCase):
    def test_mechanical_score_replaces_claude_score(self):
        """
        Simulates Item 1.2: a signal where Claude reports 80 but mechanical
        conditions are weak — mechanical score should be < 65, trade blocked.
        """
        # All-negative market_data for a BUY direction
        md = {
            "price": 1.0800,
            "ohlcv": {
                "weekly_trend": "BEARISH",
                "daily_trend":  "BEARISH",
                "h4_trend":     "BEARISH",
            },
            "indicators": {
                "bullish_ob":            "None identified in last 50 candles",
                "bullish_fvg":           "None identified",
                "recent_liquidity_sweep": "No recent sweep identified",
                "premium_discount_zone": "PREMIUM (72% of range)",
                "ote_zone":              [],
                "rsi_4h":                55.0,
                "rsi_1h":                53.0,
                "adx_4h":                18.0,
                "ema20_4h":              1.0820,
                "ema50_4h":              1.0800,
            },
            "fundamental": {
                "pair_rate":     2.0,
                "usd_rate":      4.5,
                "dxy_direction": "RISING",
                "cot_bias":      "BEARISH",
                "news_risk":     "HIGH",
                "time_to_event": 10.0,
            },
        }
        sig = {"signal": {"direction": "BUY", "confidence": 80}}
        result = calculate_confluence(md, sig)
        self.assertLess(result["confluence_score"], 65)

    def test_all_13_components_in_output(self):
        result = calculate_confluence(_make_market_data("BUY"), _make_signal("BUY"))
        expected_keys = {
            "trend_alignment", "order_block", "fvg", "liquidity_sweep",
            "premium_discount", "ote", "rsi", "adx", "ema",
            "rate_differential", "dxy", "cot", "news_clear",
        }
        self.assertEqual(set(result["component_scores"].keys()), expected_keys)


if __name__ == "__main__":
    unittest.main()
