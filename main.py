# =============================================================================
# main.py — Updated with Live OANDA Data Integration
#
# HOW TO RUN:
#   Demo with example data (no credentials needed):
#     python main.py --mode test
#
#   Demo with LIVE OANDA data:
#     export OANDA_API_KEY="your-api-key"
#     export OANDA_ACCOUNT_ID="101-001-38764497-001"
#     python main.py --mode test
#     python main.py --mode demo
#
#   Ingest documents:
#     python main.py --mode ingest
#
#   Show knowledge base stats:
#     python main.py --mode stats
#
#   Test Capital.com connection only (legacy/backup):
#     python main.py --mode capital
# =============================================================================

import os
import sys
import json
import logging
import argparse
from pathlib import Path
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)


def setup_anthropic_client():
    try:
        import anthropic
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            print("⚠️  ANTHROPIC_API_KEY not set.")
            return None
        return anthropic.Anthropic(api_key=api_key)
    except ImportError:
        print("⚠️  anthropic not installed. Run: pip install anthropic")
        return None


def get_example_market_data() -> dict:
    """Static example data — used when OANDA is not connected."""
    return {
        "pair":       "EUR/USD",
        "price":      1.08560,
        "demo_mode":  True,
        "ohlcv": {
            "day_open":          1.08400,
            "week_open":         1.07900,
            "month_open":        1.07500,
            "prev_day_high":     1.08720,
            "prev_day_low":      1.08150,
            "prev_week_high":    1.09100,
            "prev_week_low":     1.07600,
            "weekly_structure":  "HH + HL — Bullish",
            "daily_structure":   "HL forming — Bullish pullback",
            "h4_structure":      "MSS occurred — Bullish",
            "h1_structure":      "Consolidating at OB zone",
            "m15_structure":     "Pullback inside bullish OTE",
            "weekly_trend":      "BULLISH",
            "daily_trend":       "BULLISH",
            "h4_trend":          "BULLISH",
            "h1_trend":          "NEUTRAL",
            "m15_trend":         "BULLISH",
        },
        "indicators": {
            "ema20_4h":                  1.08340,
            "ema50_4h":                  1.08100,
            "ema200_daily":              1.07200,
            "rsi_4h":                    48.5,
            "rsi_1h":                    42.3,
            "adx_4h":                    27.8,
            "atr_4h":                    0.00420,
            "market_regime":             "TRENDING",
            "bullish_ob":                "1.0845-1.0855 (4H, valid)",
            "bearish_ob":                "1.0920-1.0932 (4H, valid)",
            "bullish_fvg":               "1.0848-1.0861 (1H, unfilled)",
            "bearish_fvg":               "None identified",
            "recent_liquidity_sweep":    "SSL swept at 1.0815 (2h ago)",
            "premium_discount_zone":     "DISCOUNT (42% of weekly range)",
            "ote_zone":                  [1.0822, 1.0834],
            "resistance_levels":         [1.0920, 1.0980, 1.1050],
            "support_levels":            [1.0820, 1.0760, 1.0700],
            "round_numbers":             [1.0900, 1.0800],
        },
        "fundamental": {
            "usd_rate":          4.50,
            "pair_rate":         3.65,
            "rate_differential": "+0.85% USD favor",
            "dxy_direction":     "FALLING",
            "dxy_level":         103.45,
            "cot_net":           "+12,450 contracts (Commercials net long EUR)",
            "cot_bias":          "BULLISH EUR",
            "retail_sentiment":  "68% SHORT (contrarian bullish signal)",
            "risk_sentiment":    "RISK_ON",
            "next_event_name":   "ECB President Speech",
            "next_news_event":   "ECB President Speech",
            "time_to_event":     "3 hours 20 minutes",
            "news_risk":         "MEDIUM",
            "recent_headline":   "EUR PMI beats expectations at 52.3 vs 50.1",
            "active_session":    "NY Kill Zone",
            "kill_zone_active":  "YES — NY Kill Zone (8-10 AM EST)",
            "trade_window_active": True,
        },
        "portfolio": {
            "equity":            2150,
            "open_trades":       0,
            "open_risk_pct":     0.0,
            "daily_pnl_pct":     0.0,
            "trades_today":      0,
            "usd_exposure":      "NONE",
        }
    }


def setup_oanda(demo_mode: bool = True):
    """Initialize OANDA live data connection if credentials are set."""
    api_key    = os.getenv("OANDA_API_KEY")
    account_id = os.getenv("OANDA_ACCOUNT_ID")

    if not api_key or not account_id:
        print("⚠️  OANDA credentials not set — using example data.")
        print("   To connect live: export OANDA_API_KEY='...' OANDA_ACCOUNT_ID='101-001-38764497-001'\n")
        return None

    try:
        from oanda_connector import OANDAClient, MarketDataBuilder
        mode_label = "demo (practice)" if demo_mode else "LIVE"
        print(f"\n🔌 Connecting to OANDA {mode_label} account...")
        client  = OANDAClient(api_key, account_id, practice=demo_mode)
        builder = MarketDataBuilder(client)
        print(f"✅ OANDA live data connected (account: {account_id})\n")
        return builder
    except Exception as e:
        print(f"⚠️  OANDA connection failed: {e}")
        print("   Falling back to example data\n")
        return None


def setup_executor(oanda_client, trading_config, log_dir):
    """Initialize TradeExecutor if OANDA client is available."""
    if oanda_client is None:
        return None
    try:
        from trade_executor import TradeExecutor
        executor = TradeExecutor(oanda_client, trading_config, log_dir)
        print("✅ Trade Executor ready — orders will be placed on OANDA demo\n")
        return executor
    except Exception as e:
        print(f"⚠️  Trade Executor init failed: {e}\n")
        return None


def setup_capital():
    """Initialize Capital.com live data connection (legacy backup)."""
    api_key    = os.getenv("CAPITAL_API_KEY")
    identifier = os.getenv("CAPITAL_IDENTIFIER")
    password   = os.getenv("CAPITAL_PASSWORD")

    if not api_key or not identifier or not password:
        return None

    try:
        from capital_connector import CapitalClient, MarketDataBuilder
        print("\n🔌 Connecting to Capital.com...")
        client  = CapitalClient(api_key, identifier, password, demo=True)
        builder = MarketDataBuilder(client)
        print("✅ Capital.com live data connected\n")
        return builder
    except Exception as e:
        print(f"⚠️  Capital.com connection failed: {e}")
        print("   Falling back to example data\n")
        return None


def run_ingest(pipeline, documents_dir):
    print("\n" + "="*60)
    print("📚 DOCUMENT INGESTION MODE")
    print("="*60)
    response = input("\nProceed with ingestion? (y/n): ").strip().lower()
    if response != "y":
        print("Ingestion cancelled.")
        return
    pipeline.ingest_all_documents(documents_dir)
    pipeline.print_stats()


def run_stats(pipeline):
    pipeline.print_stats()
    results = pipeline.search("London Kill Zone Order Block EUR/USD", top_k=3)
    if results:
        print(f"\nFound {len(results)} relevant chunks:")
        for r in results:
            print(f"\n  📄 {r['source']} | {r['similarity']*100:.0f}% match")
            print(f"     {r['text'][:150]}...")


def run_capital_test():
    print("\n" + "="*60)
    print("🔌 CAPITAL.COM CONNECTION TEST")
    print("="*60)
    api_key    = os.getenv("CAPITAL_API_KEY")
    identifier = os.getenv("CAPITAL_IDENTIFIER")
    password   = os.getenv("CAPITAL_PASSWORD")
    if not api_key or not identifier or not password:
        print("\n⚠️  Set credentials:")
        print("   export CAPITAL_API_KEY='your-api-key'")
        print("   export CAPITAL_IDENTIFIER='your-email@example.com'")
        print("   export CAPITAL_PASSWORD='your-password'")
        print("\nGet API key: Settings → API integrations → Add new key")
        return
    from capital_connector import CapitalClient, MarketDataBuilder
    client  = CapitalClient(api_key, identifier, password, demo=True)
    builder = MarketDataBuilder(client)
    data    = builder.build_market_data("EURUSD")
    print(f"\n✅ LIVE EUR/USD DATA:")
    print(f"  Price:      {data['price']}")
    print(f"  Spread:     {data['spread']} pips")
    print(f"  4H Trend:   {data['ohlcv']['h4_trend']}")
    print(f"  RSI 4H:     {data['indicators']['rsi_4h']}")
    print(f"  ADX 4H:     {data['indicators']['adx_4h']}")
    print(f"  Regime:     {data['indicators']['market_regime']}")
    print(f"  Session:    {data['fundamental']['active_session']}")
    print(f"  Account:    ${data['portfolio']['equity']:,.2f}")


def run_test_analysis(agent, pipeline, oanda_builder=None, executor=None):
    print("\n" + "="*60)
    print("🤖 TEST ANALYSIS MODE")
    print("="*60)
    if oanda_builder:
        print("Using LIVE OANDA data...")
        market_data = oanda_builder.build_market_data("EUR_USD")
    else:
        print("Using example data (set OANDA credentials for live data)")
        market_data = get_example_market_data()
    if agent is None:
        print("\n⚠️  No Anthropic API key")
        return
    signal = agent.analyze(market_data)
    print("\n" + "="*60)
    print("📊 FULL SIGNAL:")
    print("="*60)
    print(json.dumps(signal, indent=2))
    output_file = Path("logs") / f"test_signal_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    output_file.parent.mkdir(exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(signal, f, indent=2)
    print(f"\n✅ Signal saved to: {output_file}")

    # Execute if signal qualifies and executor is available
    if executor and signal.get("signal", {}).get("direction") != "NEUTRAL":
        print("\n🔄 Attempting order execution...")
        exec_result = executor.execute_signal(signal)
        if exec_result["executed"]:
            print(f"✅ Executed: {exec_result}")
        else:
            print(f"⏸  Not executed: {exec_result['reason']}")


def run_demo_loop(agent, pipeline, oanda_builder=None, executor=None):  # noqa: C901
    print("\n" + "="*60)
    print("🔄 DEMO LOOP MODE")
    print("="*60)
    if not oanda_builder:
        print("❌ Demo loop requires live OANDA data. Set credentials first:")
        print("   export OANDA_API_KEY='your-token'")
        print("   export OANDA_ACCOUNT_ID='101-001-38764497-001'")
        return
    print("✅ Using LIVE OANDA data — real EUR/USD prices")
    if executor:
        print("✅ Trade Executor active — will place orders on OANDA demo")
    else:
        print("⚠️  No executor — signals logged only (no orders placed)")
    print("\nRunning every 30 minutes. Press Ctrl+C to stop.\n")
    import time
    analysis_count = 0
    while True:
        try:
            analysis_count += 1
            now = datetime.utcnow().strftime("%H:%M:%S")
            print(f"\n[{now}] Analysis #{analysis_count}")

            # 1. Monitor open trades first (TP1, time stop, etc.)
            if executor:
                actions = executor.monitor_open_trades()
                if actions:
                    for a in actions:
                        print(f"  📋 {a}")
                # Feed closed-trade outcomes back into agent memory
                if agent:
                    for closed in executor.drain_closed_trades():
                        agent.record_trade_outcome(closed)

            # 2. Get market data
            if oanda_builder:
                try:
                    market_data = oanda_builder.build_market_data("EUR_USD")
                except Exception as e:
                    print(f"⚠️  OANDA error: {e} — skipping analysis (no fallback in demo loop)")
                    time.sleep(60)
                    continue
            else:
                market_data = get_example_market_data()

            # 3. Generate signal
            if agent:
                signal     = agent.analyze(market_data)
                direction  = signal.get("signal", {}).get("direction", "NEUTRAL")
                confidence = signal.get("signal", {}).get("confidence", 0)
                score      = signal.get("confluence_score", 0)
                price      = market_data.get("price", "N/A")
                session    = market_data.get("fundamental", {}).get("active_session", "")
                print(f"  Price:   {price}")
                print(f"  Session: {session}")
                print(f"  Signal:  {direction} | {confidence}% | Score: {score}/100")

                log_file = Path("logs") / f"signal_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
                with open(log_file, "w") as f:
                    json.dump(signal, f, indent=2)

                # 4. Execute if qualifies
                if executor and direction != "NEUTRAL":
                    exec_result = executor.execute_signal(signal)
                    if not exec_result["executed"]:
                        print(f"  ⏸  Not executed: {exec_result['reason']}")

            print(f"\n  Next analysis in 30 minutes...")
            time.sleep(1800)
        except KeyboardInterrupt:
            print(f"\n\n⏹  Demo loop stopped. Total: {analysis_count} analyses")
            break
        except Exception as e:
            logger.error(f"Loop error: {e}")
            time.sleep(60)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["ingest","stats","test","demo","capital"], default="test")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run --mode test without placing any orders on OANDA")
    args = parser.parse_args()

    print("\n" + "="*60)
    print("🤖 FOREX ANALYST — Option 1 + RAG + Live OANDA Data")
    print("="*60)

    from config import RAG_CONFIG, CHROMA_DIR, DOCUMENTS_DIR, LOGS_DIR, AGENT_CONFIG, TRADING_CONFIG, validate_config
    if not validate_config():
        sys.exit(1)

    if args.mode == "capital":
        run_capital_test()
        return

    from rag_pipeline import RAGPipeline
    pipeline = RAGPipeline(config=RAG_CONFIG, chroma_dir=str(CHROMA_DIR))

    client = setup_anthropic_client()
    agent  = None
    if client:
        from agent_runner import ForexAnalystAgent
        agent = ForexAnalystAgent(
            rag_pipeline=pipeline,
            anthropic_client=client,
            config=AGENT_CONFIG,
            log_dir=LOGS_DIR
        )
        print("✅ Forex Analyst Agent ready")

    oanda_builder = setup_oanda(demo_mode=TRADING_CONFIG["demo_mode"])

    # Setup executor (requires live OANDA connection)
    oanda_client  = oanda_builder.client if oanda_builder else None
    executor      = setup_executor(oanda_client, TRADING_CONFIG, LOGS_DIR)

    if   args.mode == "ingest": run_ingest(pipeline, DOCUMENTS_DIR)
    elif args.mode == "stats":  run_stats(pipeline)
    elif args.mode == "test":
        # --dry-run: analyse signal but never place orders
        run_test_analysis(agent, pipeline, oanda_builder,
                          executor=None if args.dry_run else executor)
    elif args.mode == "demo":   run_demo_loop(agent, pipeline, oanda_builder, executor)


if __name__ == "__main__":
    main()
