# =============================================================================
# config.py — Central Configuration for Forex Trading System
# =============================================================================

import os
from datetime import date
from pathlib import Path

# =============================================================================
# DIRECTORY STRUCTURE
# =============================================================================

BASE_DIR        = Path(__file__).parent
DOCUMENTS_DIR   = BASE_DIR / "documents"        # Drop your PDFs/TXTs here
CHROMA_DIR      = BASE_DIR / "chroma_db"        # Vector database (auto-created)
LOGS_DIR        = BASE_DIR / "logs"             # Trade logs, agent logs
JOURNAL_DIR     = BASE_DIR / "journal"          # Trade journal entries
FEEDBACK_DIR    = BASE_DIR / "feedback"         # Post-trade feedback memory

# Auto-create all directories
for d in [DOCUMENTS_DIR, CHROMA_DIR, LOGS_DIR, JOURNAL_DIR, FEEDBACK_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# =============================================================================
# DOCUMENT SUBDIRECTORIES (organise your books by category)
# =============================================================================

BOOKS_DIR       = DOCUMENTS_DIR / "books"       # PDFs: Kathy Lien, Mark Douglas
RESEARCH_DIR    = DOCUMENTS_DIR / "research"    # BIS, Fed, SSRN papers
ICT_DIR         = DOCUMENTS_DIR / "ict"         # ICT transcripts, notes
COT_DIR         = DOCUMENTS_DIR / "cot"         # COT report data/notes
JOURNAL_DOCS    = DOCUMENTS_DIR / "journal"     # Your own trade journal

for d in [BOOKS_DIR, RESEARCH_DIR, ICT_DIR, COT_DIR, JOURNAL_DOCS]:
    d.mkdir(parents=True, exist_ok=True)

# =============================================================================
# API KEYS — Set these as environment variables, never hardcode
# =============================================================================

ANTHROPIC_API_KEY    = os.getenv("ANTHROPIC_API_KEY", "")
NEWS_API_KEY         = os.getenv("NEWS_API_KEY", "")        # Optional: newsapi.org
FMP_API_KEY          = os.getenv("FMP_API_KEY", "")         # Optional: financialmodelingprep.com

# Manual fundamental overrides — only needed when you want to override auto data
# Sources:
#   DXY:       tradingview.com → search "DXY"
#   COT:       cftc.gov → Commitments of Traders → Legacy Futures Only → EUR
#   Sentiment: myfxbook.com/community/outlook → EUR/USD
DXY_DIRECTION        = os.getenv("DXY_DIRECTION", "")      # RISING | FALLING | NEUTRAL
DXY_LEVEL            = os.getenv("DXY_LEVEL", "")          # e.g. "104.20"
COT_BIAS             = os.getenv("COT_BIAS", "")           # BULLISH | BEARISH | NEUTRAL
COT_NET              = os.getenv("COT_NET", "")            # e.g. "+18500 (net EUR contracts)"
RETAIL_SENTIMENT     = os.getenv("RETAIL_SENTIMENT", "")   # e.g. "72% SHORT"

# =============================================================================
# RAG PIPELINE SETTINGS
# =============================================================================

RAG_CONFIG = {
    # Embedding model — runs locally, completely FREE
    # Downloads ~90MB on first run
    "embedding_model":      "all-MiniLM-L6-v2",

    # Chunking settings
    "chunk_size":           600,        # Words per chunk (optimal for trading knowledge)
    "chunk_overlap":        100,        # Overlap between chunks (preserves context)

    # Retrieval settings
    "top_k_results":        5,          # How many chunks to retrieve per query
    "similarity_threshold": 0.3,        # Minimum similarity score (0-1)

    # Collection names in ChromaDB (one per knowledge category)
    "collections": {
        "books":            "trading_books",
        "research":         "research_papers",
        "ict":              "ict_knowledge",
        "cot":              "cot_analysis",
        "journal":          "trade_journal",
        "feedback":         "agent_feedback",
    },

    # Document metadata tags (used for filtered retrieval)
    "source_tags": {
        "books":            ["mark_douglas", "kathy_lien", "al_brooks", "murphy"],
        "research":         ["bis", "fed", "ecb", "imf", "ssrn", "arxiv"],
        "ict":              ["ict", "smart_money", "order_blocks", "liquidity"],
        "cot":              ["cot", "cftc", "positioning", "commercials"],
        "journal":          ["personal_journal", "own_trades"],
        "feedback":         ["agent_feedback", "trade_review"],
    }
}

# =============================================================================
# AGENT SETTINGS
# =============================================================================

AGENT_CONFIG = {
    # Claude model to use
    "model":                "claude-sonnet-4-20250514",

    # Token limits
    "max_tokens":           2000,
    "system_prompt_tokens": 4000,       # Approx size of system prompt
    "rag_context_tokens":   3000,       # Max tokens for RAG chunks injected

    # Analysis frequency
    "analysis_interval_min": 30,        # Run analysis every N minutes

    # Confidence thresholds
    "min_confidence":       65,         # Below this → NEUTRAL signal
    "strong_signal":        85,         # Above this → full position size

    # Memory settings
    "feedback_memory_limit": 15,        # Last N trade feedbacks injected
    "journal_memory_limit":  10,        # Last N journal entries injected
}

# =============================================================================
# TRADING SETTINGS
# =============================================================================

TRADING_CONFIG = {
    # Pairs to analyse (start with just EUR/USD)
    "active_pairs":         ["EUR_USD"],

    # Risk settings
    "max_risk_per_trade":   0.01,       # 1% of equity per trade
    "max_portfolio_risk":   0.03,       # 3% total open risk
    "max_daily_loss":       0.02,       # 2% → stop all trading for the day
    "max_weekly_loss":      0.05,       # 5% → emergency shutdown

    # Position management
    "tp1_close_percent":    0.50,       # Close 50% at TP1
    "tp2_trail":            True,       # Trail remaining position to TP2
    "time_stop_hours":      8,          # Close if -0.5R after N hours
    "min_rr_ratio":         2.0,        # Minimum Risk:Reward to take trade

    # Session filter (EST times)
    "trade_sessions": {
        "london_kill_zone": ("03:00", "04:00"),
        "ny_kill_zone":     ("08:00", "10:00"),
        "london_close":     ("10:00", "12:00"),
    },

    # News blackout (minutes before high-impact event)
    "news_blackout_minutes": 30,

    # Signal thresholds (mirrored from AGENT_CONFIG for executor access)
    "min_confidence":       65,         # Below this → never execute
    "min_rr_ratio":         2.0,        # Below this → never execute

    # Demo mode
    "demo_mode":            True,       # ALWAYS start True, change after 12 months
}

# =============================================================================
# LOGGING SETTINGS
# =============================================================================

LOG_CONFIG = {
    "log_level":            "INFO",
    "log_to_file":          True,
    "log_to_console":       True,
    "trade_log_file":       str(LOGS_DIR / "trades.csv"),
    "agent_log_file":       str(LOGS_DIR / "agent_decisions.jsonl"),
    "error_log_file":       str(LOGS_DIR / "errors.log"),
    "performance_file":     str(LOGS_DIR / "performance.csv"),
}

# =============================================================================
# SAFETY CHECKS
# =============================================================================

def validate_config():
    """Run on startup to catch missing config before trading begins."""
    errors = []
    live_allowed_from = date(2027, 3, 10)

    if not ANTHROPIC_API_KEY:
        errors.append("ANTHROPIC_API_KEY not set in environment variables")

    if TRADING_CONFIG["demo_mode"] is False:
        if date.today() < live_allowed_from:
            errors.append("demo_mode must remain True until March 10, 2027 per CLAUDE.md")
        oanda_key = os.getenv("OANDA_API_KEY", "")
        oanda_id  = os.getenv("OANDA_ACCOUNT_ID", "")
        if not oanda_key:
            errors.append("OANDA_API_KEY required for live trading")
        if not oanda_id:
            errors.append("OANDA_ACCOUNT_ID required for live trading")

    if errors:
        print("\n⚠️  CONFIG ERRORS:")
        for e in errors:
            print(f"   ❌ {e}")
        print("\nSet environment variables before running.\n")
        return False

    print("✅ Config validated successfully")
    print(f"   Mode:     {'DEMO (safe)' if TRADING_CONFIG['demo_mode'] else '🔴 LIVE'}")
    print(f"   Pairs:    {TRADING_CONFIG['active_pairs']}")
    print(f"   Max Risk: {TRADING_CONFIG['max_risk_per_trade']*100}% per trade")

    # Show how fundamentals will be sourced at runtime
    dxy = DXY_DIRECTION or None
    cot = COT_BIAS      or None
    sen = RETAIL_SENTIMENT or None
    if dxy or cot or sen:
        print(f"   DXY:      {dxy or '—'}  |  COT: {cot or '—'}  |  Sentiment: {sen or '—'}")
    else:
        print("   ℹ️  Manual overrides not set — auto-fetch will be used for DXY, COT, and calendar")
        print("      Retail sentiment is still manual; live headlines require NEWS_API_KEY")
        if not FMP_API_KEY:
            print("      FMP calendar feed is disabled; set FMP_API_KEY for live macro events")
    return True


if __name__ == "__main__":
    validate_config()
