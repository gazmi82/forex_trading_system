"""
run_threshold_comparison.py
----------------------------
Runs the full replay → simulation → report pipeline for thresholds 50, 55, 65
against the 2024-2025 EUR_USD local CSV datasets.

Usage (from project root, venv activated):
    python run_threshold_comparison.py

No live APIs are called. local_only=True throughout.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap — must be run from the project root so app.* imports resolve.
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.backtesting import (
    BacktestReportGenerator,
    HistoricalDataLoader,
    OutcomeSimulator,
    SignalReplayEngine,
)

INSTRUMENT   = "EUR_USD"
START        = datetime(2024, 1, 1, tzinfo=timezone.utc)
END          = datetime(2026, 1, 1, tzinfo=timezone.utc)   # covers all of 2024+2025
DATA_DIR     = PROJECT_ROOT / "backtest_data"
OUTPUT_ROOT  = PROJECT_ROOT / "backtest_results" / "threshold_comparison"
THRESHOLDS   = [50, 55, 65]
STARTING_BAL = 1_000.0   # USD
RISK_PCT     = 0.01       # 1% per trade (matches TRADING_CONFIG)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def read_jsonl(path: Path) -> list[dict]:
    rows = []
    if not path.exists():
        return rows
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def split_by_year(trades: list[dict]) -> dict[int, list[dict]]:
    buckets: dict[int, list[dict]] = {}
    for t in trades:
        date_str = t.get("date") or t.get("closed_at") or ""
        try:
            year = int(date_str[:4])
        except (ValueError, TypeError):
            year = 0
        buckets.setdefault(year, []).append(t)
    return buckets


def compute_dollar_stats(trades: list[dict], starting_balance: float) -> dict:
    """
    Compound P&L: each trade risks 1% of current equity.
    pnl_r is the R outcome (e.g. +2.0, -1.0, +0.5).
    Dollar gain per trade = equity * 0.01 * pnl_r.
    """
    equity = starting_balance
    peak   = starting_balance
    max_dd_usd = 0.0

    for t in trades:
        risk_usd  = equity * RISK_PCT
        pnl_r     = float(t.get("pnl_r", 0) or 0)
        pnl_usd   = risk_usd * pnl_r
        equity   += pnl_usd
        peak      = max(peak, equity)
        max_dd_usd = max(max_dd_usd, peak - equity)

    net_pnl = equity - starting_balance
    return {
        "final_balance": round(equity, 2),
        "net_pnl_usd": round(net_pnl, 2),
        "max_drawdown_usd": round(max_dd_usd, 2),
        "max_drawdown_pct": round(max_dd_usd / starting_balance * 100, 2),
    }


def trade_stats(trades: list[dict]) -> dict:
    pnls      = [float(t.get("pnl_r", 0) or 0) for t in trades]
    total     = len(pnls)
    wins      = sum(1 for p in pnls if p > 0)
    losses    = sum(1 for p in pnls if p < 0)
    breakeven = sum(1 for p in pnls if p == 0)
    gross_p   = sum(p for p in pnls if p > 0)
    gross_l   = abs(sum(p for p in pnls if p < 0))
    pf        = round(gross_p / gross_l, 3) if gross_l else None
    wr        = round(wins / total * 100, 1) if total else 0.0
    net_r     = round(sum(pnls), 3)
    exp_r     = round(sum(pnls) / total, 4) if total else 0.0

    # drawdown in R (order-preserving cumulative)
    peak_r   = 0.0
    run_r    = 0.0
    max_dd_r = 0.0
    for p in pnls:
        run_r   += p
        peak_r   = max(peak_r, run_r)
        max_dd_r = max(max_dd_r, peak_r - run_r)

    return {
        "total_trades": total,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate_pct": wr,
        "net_pnl_r": net_r,
        "expectancy_r": exp_r,
        "max_drawdown_r": round(max_dd_r, 3),
        "profit_factor": pf,
    }


def signals_meta(signals: list[dict]) -> dict:
    scores      = [int(s.get("confluence_score", 0) or 0) for s in signals]
    non_neutral = [s for s in signals if str(s.get("direction_implied", "")) != "NEUTRAL"
                   or int(s.get("confluence_score", 0) or 0) > 0]
    neutral_cnt = sum(1 for s in signals if s.get("execution_direction", "NEUTRAL") == "NEUTRAL")
    tradable    = sum(1 for s in signals if s.get("execution_allowed", False))
    return {
        "total_windows":        len(signals),
        "tradable_windows":     tradable,
        "neutral_windows":      neutral_cnt,
        "non_neutral_windows":  len(signals) - neutral_cnt,
        "highest_score":        max(scores) if scores else 0,
        "avg_score":            round(sum(scores) / len(scores), 1) if scores else 0.0,
    }


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def run_for_threshold(threshold: int) -> dict:
    tag        = f"t{threshold}"
    out_dir    = OUTPUT_ROOT / tag
    out_dir.mkdir(parents=True, exist_ok=True)

    loader    = HistoricalDataLoader(None, cache_dir=DATA_DIR)
    replayer  = SignalReplayEngine(
        loader,
        output_root=out_dir,
        min_confidence=threshold,
        strong_signal=85,
    )
    simulator = OutcomeSimulator(loader, output_root=out_dir)
    reporter  = BacktestReportGenerator(output_root=out_dir)

    print(f"\n[threshold={threshold}] Running replay ...")
    replay_summary = replayer.replay(
        INSTRUMENT,
        start=START,
        end=END,
        local_only=True,
    )
    print(f"  Windows: {replay_summary.total_windows}  |  Tradable: {replay_summary.tradable_windows}")
    print(f"  Output:  {replay_summary.output_path}")

    signals = read_jsonl(Path(replay_summary.output_path))
    smeta   = signals_meta(signals)

    print(f"[threshold={threshold}] Running outcome simulation ...")
    sim_summary = simulator.simulate(
        Path(replay_summary.output_path),
        instrument=INSTRUMENT,
        local_only=True,
    )
    print(f"  Signals seen: {sim_summary.signals_seen}  |  Tradable: {sim_summary.tradable_signals}")
    print(f"  Filled trades: {sim_summary.filled_trades}  |  No-fill: {sim_summary.no_fill_signals}")

    trades_path = Path(sim_summary.output_path)
    trades      = read_jsonl(trades_path)

    print(f"[threshold={threshold}] Generating report ...")
    report_path = out_dir / f"report_t{threshold}.json"
    rep_summary = reporter.generate(trades_path, output_path=report_path)
    with open(report_path, encoding="utf-8") as fh:
        full_report = json.load(fh)

    # --- year split ---
    by_year = split_by_year(trades)
    year_stats: dict[int, dict] = {}
    running_bal = STARTING_BAL
    for year in sorted(by_year.keys()):
        yr_trades  = by_year[year]
        ts         = trade_stats(yr_trades)
        ds         = compute_dollar_stats(yr_trades, running_bal)
        running_bal = ds["final_balance"]
        year_stats[year] = {**ts, **ds}

    # --- full period ---
    full_ts = trade_stats(trades)
    full_ds = compute_dollar_stats(trades, STARTING_BAL)

    return {
        "threshold": threshold,
        "signals_meta": smeta,
        "full_period": {**full_ts, **full_ds},
        "year_split": year_stats,
        "sim_summary": {
            "signals_seen": sim_summary.signals_seen,
            "tradable_signals": sim_summary.tradable_signals,
            "filled_trades": sim_summary.filled_trades,
            "no_fill_signals": sim_summary.no_fill_signals,
        },
        "tp1_trail_included": True,   # OutcomeSimulator always evaluates TP1 + trailing stop
        "report_path": str(report_path),
        "profit_factor_from_report": full_report.get("profit_factor"),
        "max_drawdown_r_from_report": full_report.get("max_drawdown_r"),
    }


def print_table(results: list[dict]):
    DIVIDER = "=" * 90
    print("\n\n" + DIVIDER)
    print("  CONFLUENCE THRESHOLD COMPARISON — EUR/USD 2024-2025")
    print(DIVIDER)

    hdr = f"{'Metric':<38}{'T=50':>16}{'T=55':>16}{'T=65':>16}"
    print(hdr)
    print("-" * 90)

    def row(label, key, fmt="{}", year=None):
        vals = []
        for r in results:
            src = r["year_split"].get(year, {}) if year else r["full_period"]
            v   = src.get(key, "N/A")
            vals.append(fmt.format(v) if v != "N/A" else "N/A")
        print(f"  {label:<36}" + "".join(f"{v:>16}" for v in vals))

    def srow(label, key, fmt="{}"):
        vals = []
        for r in results:
            v = r["signals_meta"].get(key, "N/A")
            vals.append(fmt.format(v) if v != "N/A" else "N/A")
        print(f"  {label:<36}" + "".join(f"{v:>16}" for v in vals))

    print("  SIGNAL ANALYSIS")
    print("  " + "-" * 86)
    srow("Total windows analyzed", "total_windows")
    srow("Tradable (passed threshold)", "tradable_windows")
    srow("Neutral (blocked)", "neutral_windows")
    srow("Non-neutral windows", "non_neutral_windows")
    srow("Highest confluence score", "highest_score")
    srow("Avg confluence score", "avg_score")

    print()
    print("  FULL PERIOD (2024 + 2025)")
    print("  " + "-" * 86)
    row("Possible trade opens",     "total_trades")
    row("Filled (closed) trades",   "total_trades")
    row("Wins",                     "wins")
    row("Losses",                   "losses")
    row("Breakevens",               "breakeven")
    row("Win rate",                 "win_rate_pct", "{:.1f}%")
    row("Net P&L (R)",              "net_pnl_r", "{:+.3f}R")
    row("Expectancy per trade (R)", "expectancy_r", "{:+.4f}R")
    row("Max drawdown (R)",         "max_drawdown_r", "{:.3f}R")
    row("Starting balance",         None)   # manual
    for r in results:
        pass  # will do manually below
    # Starting balance line manually
    sb_vals = [f"${STARTING_BAL:,.0f}" for _ in results]
    print(f"  {'Starting balance':<36}" + "".join(f"{v:>16}" for v in sb_vals))
    row("Final balance",            "final_balance", "${:,.2f}")
    row("Net P&L (USD, compounded)","net_pnl_usd", "${:+,.2f}")
    row("Max drawdown (USD)",       "max_drawdown_usd", "${:.2f}")
    row("Max drawdown (%)",         "max_drawdown_pct", "{:.2f}%")

    # profit factor from report
    print()
    pf_vals = [
        (f"{r['full_period']['profit_factor']:.3f}" if r["full_period"]["profit_factor"] else "N/A")
        for r in results
    ]
    print(f"  {'Profit factor':<36}" + "".join(f"{v:>16}" for v in pf_vals))
    dd_r_vals = [f"{r['full_period']['max_drawdown_r']:.3f}R" for r in results]
    print(f"  {'Max drawdown (R)':<36}" + "".join(f"{v:>16}" for v in dd_r_vals))
    tp1_vals = ["YES (TP1+trail)" for _ in results]
    print(f"  {'TP1/breakeven/trail included':<36}" + "".join(f"{v:>16}" for v in tp1_vals))

    # No-fill
    nf_vals = [str(r["sim_summary"]["no_fill_signals"]) for r in results]
    print(f"  {'No-fill signals (no M1 touch)':<36}" + "".join(f"{v:>16}" for v in nf_vals))

    # Year splits
    for year in [2024, 2025]:
        print()
        print(f"  YEAR {year}")
        print("  " + "-" * 86)
        row(f"{year} trades", "total_trades", year=year)
        row(f"{year} wins",   "wins",         year=year)
        row(f"{year} losses", "losses",       year=year)
        row(f"{year} breakevens", "breakeven", year=year)
        row(f"{year} win rate", "win_rate_pct", "{:.1f}%", year=year)
        row(f"{year} net P&L (R)", "net_pnl_r", "{:+.3f}R", year=year)
        row(f"{year} final balance", "final_balance", "${:,.2f}", year=year)
        row(f"{year} net P&L (USD)", "net_pnl_usd", "${:+,.2f}", year=year)
        row(f"{year} max drawdown", "max_drawdown_usd", "${:.2f}", year=year)

    print("\n" + DIVIDER)
    print("  METHOD USED")
    print(DIVIDER)
    print("  Replay engine:    app/backtesting/signal_replayer.py :: SignalReplayEngine")
    print("  Scorer:           app/backtesting/replay_confluence.py :: calculate_confluence")
    print("  Simulator:        app/backtesting/outcome_simulator.py :: OutcomeSimulator")
    print("  Reporter:         app/backtesting/report.py :: BacktestReportGenerator")
    print("  Data loader:      app/backtesting/data_loader.py :: HistoricalDataLoader")
    print("  Fundamentals:     app/backtesting/historical_fundamentals_provider.py")
    print(f"  Price data:       backtest_data/raw/EUR_USD/ (2024-01-01 → 2026-01-01)")
    print(f"  Fundamentals CSV: NONE present (rate/DXY/COT/calendar CSVs missing)")

    print()
    print("  ASSUMPTIONS / LIMITS")
    print(DIVIDER)
    print("  1. Fundamental components (rate_differential, DXY, COT, news_clear)")
    print("     are UNAVAILABLE — no CSVs in backtest_data/fundamentals/.")
    print("     Score is normalized against available components only (technical + ICT).")
    print("     This inflates % scores vs. a full fundamental stack.")
    print()
    print("  2. Scores computed against available points only:")
    print("     Max points if all technical ICT available ≈ 100 pts (trend 15+OB 20+")
    print("     FVG 15+sweep 15+PD 10+OTE 10+RSI 10+ADX 10+EMA 5 = 110 pts max)")
    print("     Normalised to 100 → thresholds 50/55/65 behave differently vs live.")
    print()
    print("  3. Position sizing uses compounding 1% risk per trade on a $1000 balance.")
    print("     pnl_r from simulator → dollar_gain = equity × 0.01 × pnl_r.")
    print()
    print("  4. Entry fill requires M1 candle range to touch the entry_zone midpoint.")
    print("     Signals where price never reaches the zone = no-fill = excluded.")
    print()
    print("  5. Kill-zone windows: London (3 AM EST), NY (8 AM EST), London Close (10 AM).")
    print("     3 windows per trading day × ~520 trading days = theoretical max windows.")
    print()
    print("  6. Same-candle tie-breaking: protective stop wins over TP on same candle.")
    print()
    print("  7. TP1 closes 50% at +1R, moves SL to entry (breakeven), then ATR-based")
    print("     trailing stop on remaining 50% until TP2 (+2R) or time/trail-stop exit.")
    print()
    print("  8. Time stop: closes if still below −0.5R after session-based holding limit.")
    print()
    print("  COMMANDS EQUIVALENT TO THIS SCRIPT:")
    print("  For default threshold (65), same as:")
    print("    python main.py --mode backtest --replay-data-dir backtest_data \\")
    print("      --backtest-output-dir backtest_results/threshold_comparison/t65 \\")
    print("      --start 2024-01-01 --end 2026-01-01")
    print(DIVIDER + "\n")


if __name__ == "__main__":
    print(f"Starting threshold comparison run at {datetime.now(timezone.utc).isoformat()}Z")
    print(f"Data root:    {DATA_DIR}")
    print(f"Output root:  {OUTPUT_ROOT}")
    print(f"Range:        {START.date()} → {END.date()}")
    print(f"Thresholds:   {THRESHOLDS}")

    results = []
    for t in THRESHOLDS:
        r = run_for_threshold(t)
        results.append(r)

    # Save full results JSON
    results_path = OUTPUT_ROOT / "comparison_results.json"
    with open(results_path, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)
    print(f"\nFull results saved → {results_path}")

    print_table(results)
    print(f"Run completed at {datetime.now(timezone.utc).isoformat()}Z")
