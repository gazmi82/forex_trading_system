# =============================================================================
# main.py — Updated with Live OANDA Data Integration
#
# HOW TO RUN:
#   Test with LIVE OANDA data:
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
#   Check live broker + fundamentals without Claude:
#     python main.py --mode check
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

# Keep third-party model download chatter out of the console.
# Real failures still surface through our own exception handling.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("huggingface_hub").setLevel(logging.WARNING)
logging.getLogger("sentence_transformers").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


def print_signal_runtime_issue(signal: dict):
    """Print Claude/runtime failures explicitly instead of as normal no-trades."""
    reason = signal.get("do_not_trade_reason", "")
    if signal.get("error"):
        print(f"  ❌ Claude API failure: {reason or signal['error']}")
    elif reason.startswith("JSON parse error"):
        print(f"  ❌ Claude response parse failure: {reason}")


def print_live_data_warning(reason: str):
    """Fail closed when live market data is unavailable."""
    print("\n❌ Live EUR/USD market data unavailable.")
    print(f"   Reason: {reason}")
    print("   Action:")
    print("   export OANDA_API_KEY='your-api-key'")
    print("   export OANDA_ACCOUNT_ID='101-001-38764497-001'")
    print("   Then rerun the command.")


def setup_anthropic_client():
    try:
        import anthropic
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            print("❌ ANTHROPIC_API_KEY not set.")
            return None
        return anthropic.Anthropic(api_key=api_key)
    except ImportError:
        print("❌ anthropic not installed. Run: pip install anthropic")
        return None


def setup_oanda(demo_mode: bool = True):
    """Initialize OANDA live data connection if credentials are set."""
    api_key    = os.getenv("OANDA_API_KEY")
    account_id = os.getenv("OANDA_ACCOUNT_ID")

    if not api_key or not account_id:
        missing = []
        if not api_key:
            missing.append("OANDA_API_KEY")
        if not account_id:
            missing.append("OANDA_ACCOUNT_ID")
        print_live_data_warning(f"Missing environment variables: {', '.join(missing)}")
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
        print_live_data_warning(f"OANDA connection failed: {e}")
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
    if not oanda_builder:
        print_live_data_warning("Test mode requires a live OANDA connection")
        return False
    print("Using LIVE OANDA data...")
    market_data = oanda_builder.build_market_data("EUR_USD")
    if agent is None:
        print("\n❌ No Anthropic client available for analysis.")
        print("   Action:")
        print("   export ANTHROPIC_API_KEY='your-api-key'")
        print("   Then rerun the command.")
        return False
    signal = agent.analyze(market_data)
    print_signal_runtime_issue(signal)
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
    return True


def run_live_data_check(oanda_builder=None):
    print("\n" + "="*60)
    print("🩺 LIVE DATA CHECK MODE")
    print("="*60)
    if not oanda_builder:
        print_live_data_warning("Check mode requires a live OANDA connection")
        return False

    print("Using LIVE OANDA data without Claude...")
    market_data = oanda_builder.build_market_data("EUR_USD")
    fundamental = market_data.get("fundamental", {})
    portfolio = market_data.get("portfolio", {})

    print("\n✅ Live market_data snapshot built successfully")
    print(f"  Price:      {market_data.get('price')}")
    print(f"  Spread:     {market_data.get('spread')} pips")
    print(f"  Session:    {fundamental.get('active_session')}")
    print(f"  4H Trend:   {market_data.get('ohlcv', {}).get('h4_trend')}")
    print(f"  Daily Trend:{market_data.get('ohlcv', {}).get('daily_trend')}")
    print(f"  Equity:     ${portfolio.get('equity', 0):,.2f}")
    print(f"  DXY:        {fundamental.get('dxy_direction')} | {fundamental.get('dxy_level')}")
    print(f"  Rates:      {fundamental.get('rate_differential')}")
    print(f"  Calendar:   {fundamental.get('next_news_event')} | {fundamental.get('time_to_event')}")
    print(f"  Headlines:  {fundamental.get('recent_headline')}")
    print(f"  Retail:     {fundamental.get('retail_sentiment')}")

    output_file = Path("logs") / f"live_data_check_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    output_file.parent.mkdir(exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(market_data, f, indent=2)
    print(f"\n✅ Snapshot saved to: {output_file}")
    return True


def run_demo_loop(agent, pipeline, oanda_builder=None, executor=None):  # noqa: C901
    print("\n" + "="*60)
    print("🔄 DEMO LOOP MODE")
    print("="*60)
    if not oanda_builder:
        print_live_data_warning("Demo loop requires a live OANDA connection")
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
                    print_live_data_warning(f"OANDA market-data fetch failed: {e}")
                    time.sleep(60)
                    continue

            # 3. Generate signal
            if agent:
                signal     = agent.analyze(market_data)
                print_signal_runtime_issue(signal)
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
    parser.add_argument("--mode", choices=["ingest","stats","test","demo","check","capital"], default="test")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run --mode test without placing any orders on OANDA")
    args = parser.parse_args()

    print("\n" + "="*60)
    print("🤖 FOREX ANALYST — Option 1 + RAG + Live OANDA Data")
    print("="*60)

    from config import RAG_CONFIG, CHROMA_DIR, DOCUMENTS_DIR, LOGS_DIR, AGENT_CONFIG, TRADING_CONFIG, validate_config
    require_anthropic = args.mode in {"test", "demo"}
    if not validate_config(require_anthropic=require_anthropic):
        sys.exit(1)

    if args.mode == "capital":
        run_capital_test()
        return

    pipeline = None
    if args.mode in {"ingest", "stats", "test", "demo"}:
        from rag_pipeline import RAGPipeline
        pipeline = RAGPipeline(config=RAG_CONFIG, chroma_dir=str(CHROMA_DIR))

    agent  = None
    if require_anthropic:
        client = setup_anthropic_client()
        if client:
            from agent_runner import ForexAnalystAgent
            agent = ForexAnalystAgent(
                rag_pipeline=pipeline,
                anthropic_client=client,
                config=AGENT_CONFIG,
                log_dir=LOGS_DIR
            )
            print("✅ Forex Analyst Agent ready")

    oanda_builder = None
    executor = None
    if args.mode in {"test", "demo", "check"}:
        oanda_builder = setup_oanda(demo_mode=TRADING_CONFIG["demo_mode"])
        if oanda_builder is None:
            sys.exit(1)
        if args.mode in {"test", "demo"}:
            oanda_client = oanda_builder.client
            executor = setup_executor(oanda_client, TRADING_CONFIG, LOGS_DIR)

    if   args.mode == "ingest": run_ingest(pipeline, DOCUMENTS_DIR)
    elif args.mode == "stats":  run_stats(pipeline)
    elif args.mode == "check":
        ok = run_live_data_check(oanda_builder)
        if ok is False:
            sys.exit(1)
    elif args.mode == "test":
        # --dry-run: analyse signal but never place orders
        ok = run_test_analysis(agent, pipeline, oanda_builder,
                               executor=None if args.dry_run else executor)
        if ok is False:
            sys.exit(1)
    elif args.mode == "demo":   run_demo_loop(agent, pipeline, oanda_builder, executor)


if __name__ == "__main__":
    main()
