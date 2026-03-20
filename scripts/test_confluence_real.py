"""
Real-case scenario test for the mechanical confluence scorer.

Uses actual logged market_data from live_data_check_*.json files
and the corresponding signal files to show exactly what the scorer
produces against real EUR/USD conditions.

Run from the project root:
    python scripts/test_confluence_real.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow running from project root without install
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.analysis.confluence_scorer import calculate_confluence

LOGS_DIR = Path(__file__).resolve().parents[1] / "logs"

_COMPONENT_MAX = {
    "trend_alignment":   15,
    "order_block":       20,
    "fvg":               15,
    "liquidity_sweep":   15,
    "premium_discount":  10,
    "ote":               10,
    "rsi":               10,
    "adx":               10,
    "ema":                5,
    "rate_differential": 15,
    "dxy":               10,
    "cot":               10,
    "news_clear":         5,
}


def _bar(score: int, max_score: int, width: int = 20) -> str:
    filled = round(score / max_score * width) if max_score else 0
    return "█" * filled + "░" * (width - filled)


def _print_result(label: str, market_data: dict, signal: dict):
    result = calculate_confluence(market_data, signal)
    score       = result["confluence_score"]
    implied     = result["direction_implied"]
    components  = result["component_scores"]
    direction   = (signal.get("signal") or {}).get("direction", "NEUTRAL")

    threshold_label = (
        "STRONG (≥85)"   if score >= 85 else
        "MODERATE (65-84)" if score >= 65 else
        "BLOCKED (<65)"
    )

    print(f"\n{'='*60}")
    print(f"  {label}")
    print(f"{'='*60}")
    print(f"  Signal direction  : {direction}")
    print(f"  Mechanical score  : {score}/100  →  {threshold_label}")
    print(f"  Direction implied : {implied}")
    print(f"  Price             : {market_data.get('price')}")
    print(f"  Session           : {market_data.get('fundamental', {}).get('active_session', 'N/A')}")
    print()
    print(f"  {'Component':<22} {'Score':>5} / {'Max':>3}  {'Bar':<22}")
    print(f"  {'-'*56}")
    for key, max_pts in _COMPONENT_MAX.items():
        pts  = components.get(key, 0)
        bar  = _bar(pts, max_pts)
        tick = "✓" if pts > 0 else "✗"
        print(f"  {tick} {key:<20} {pts:>5} / {max_pts:>3}  {bar}")
    print(f"  {'-'*56}")
    total_raw = sum(components.values())
    print(f"  {'TOTAL (raw)':<22} {total_raw:>5} / 150")
    print(f"  {'NORMALISED (÷150×100)':<22} {score:>5} / 100")


def _load_json(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def run():
    # -------------------------------------------------------------------------
    # Case 1: March 17 live data — signal direction NEUTRAL (conflicting MTF)
    # -------------------------------------------------------------------------
    md_march17 = _load_json(LOGS_DIR / "live_data_check_20260317_152613.json")
    sig_neutral = {"signal": {"direction": "NEUTRAL", "confidence": 40}}
    _print_result("CASE 1 — March 17 live data  |  Direction: NEUTRAL", md_march17, sig_neutral)

    # -------------------------------------------------------------------------
    # Case 2: Same data — ask the scorer to evaluate a hypothetical SELL
    # (Weekly=BEARISH, Daily=BEARISH vs H4=BULLISH — conflicting)
    # -------------------------------------------------------------------------
    sig_sell = {"signal": {"direction": "SELL", "confidence": 65}}
    _print_result("CASE 2 — March 17 live data  |  Direction: SELL (hypothetical)", md_march17, sig_sell)

    # -------------------------------------------------------------------------
    # Case 3: Same data — ask the scorer to evaluate a hypothetical BUY
    # (price in PREMIUM, weekly/daily BEARISH — should score very low)
    # -------------------------------------------------------------------------
    sig_buy = {"signal": {"direction": "BUY", "confidence": 65}}
    _print_result("CASE 3 — March 17 live data  |  Direction: BUY (hypothetical)", md_march17, sig_buy)

    # -------------------------------------------------------------------------
    # Case 4: Latest signal file (March 20) — Claude scored it 40
    # We wire what the March 20 signal reported back through the scorer
    # using the March 17 live market_data as the best available proxy.
    # -------------------------------------------------------------------------
    sig_march20 = _load_json(LOGS_DIR / "signal_20260320_120017.json")
    _print_result(
        f"CASE 4 — March 20 signal   |  Claude score: {sig_march20.get('confluence_score', 'N/A')}  |  Direction: {sig_march20.get('signal', {}).get('direction')}",
        md_march17,
        sig_march20,
    )

    # -------------------------------------------------------------------------
    # Summary: direction_implied vs Claude direction
    # -------------------------------------------------------------------------
    print(f"\n{'='*60}")
    print("  CALIBRATION SNAPSHOT (Claude score vs Mechanical score)")
    print(f"{'='*60}")
    print(f"  {'Case':<10} {'Direction':<10} {'Mech':>6}  {'Status'}")
    print(f"  {'-'*50}")
    cases = [
        ("NEUTRAL", sig_neutral),
        ("SELL",    sig_sell),
        ("BUY",     sig_buy),
        ("Mar-20",  sig_march20),
    ]
    for label, sig in cases:
        r = calculate_confluence(md_march17, sig)
        s = r["confluence_score"]
        d = (sig.get("signal") or {}).get("direction", "NEUTRAL")
        status = "TRADE" if s >= 65 else "BLOCKED"
        print(f"  {label:<10} {d:<10} {s:>6}  {status}")
    print()


if __name__ == "__main__":
    run()
