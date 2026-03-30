# =============================================================================
# config.py — Central Configuration for Forex Trading System
# =============================================================================

import os
from datetime import date
from pathlib import Path

# =============================================================================
# DIRECTORY STRUCTURE
# =============================================================================

BASE_DIR        = Path(__file__).resolve().parents[2]
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
FINNHUB_API_KEY      = os.getenv("FINNHUB_API_KEY", "")     # Optional: finnhub.io

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

    # Provider resilience
    "claude_retry_attempts": 3,         # Retries for transient provider errors
    "claude_retry_backoff_seconds": 1.5,  # Base backoff for transient Claude failures
    "claude_circuit_failures": 3,       # Open short cooldown after N consecutive failures
    "claude_cooldown_seconds": 120,     # Cooldown window after repeated failures
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
    "tp1_close_percent":    0.50,       # Fraction of position to close at TP1 (0.50 = 50%)
    "tp1_target_fraction_of_tp2": 0.60,  # Place TP1 at 60% of the distance from entry to TP2
    "tp2_trail":            True,       # Trail remaining position after TP1; False = fixed TP2 only
    "trail_atr_multiplier": 1.0,        # ATR(1H) multiplier for the post-TP1 trailing stop
    "early_momentum_exit":  True,       # Exit weak trades that fail to expand toward TP2 soon after entry
    "early_momentum_minutes": 60,       # Evaluate the rule once, 60 minutes after the trade is filled
    "early_momentum_max_gap_pips": 15.0,  # Exit if best favorable price is still > N pips from TP2
    "early_momentum_min_tp2_progress": None,  # Optional extra filter, e.g. 0.50 for 50% progress to TP2
    "backtest_starting_balance": 1000.0,  # Starting equity for sequential replay valuation
    "backtest_spread_pips": 0.8,         # Full EUR/USD spread assumption for replay
    "backtest_slippage_pips": 0.1,       # One-way slippage assumption for replay fills/exits
    "backtest_single_position_mode": True,  # Replay only one active position at a time
    "backtest_enforce_loss_limits": True,   # Apply daily/weekly loss gates in sequential replay
    "adaptive_time_stop":   True,       # Extend the session time stop when the thesis quality supports patience
    "time_stop_hours": {
        "London Kill Zone": 4,
        "NY Kill Zone":     6,
        "London Close":     3,
        "default":          8,
    },
    "adaptive_time_stop_extensions": {
        "trend_aligned_hours": 1.0,
        "macro_aligned_hours": 0.5,
        "trending_hours": 0.5,
        "high_volatility_hours": 1.0,
        "strong_signal_hours": 0.5,
        "strong_signal_threshold": 85,
        "max_total_hours": 2.0,
    },

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
    "min_rr_ratio":         2.0,        # Shared minimum R:R for analysis and execution

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

def validate_config(require_anthropic: bool = True):
    """Run on startup to catch missing config before runtime begins."""
    errors = []
    live_allowed_from = date(2027, 3, 10)

    if require_anthropic and not ANTHROPIC_API_KEY:
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
        print("\n❌ CONFIG ERRORS:")
        for e in errors:
            print(f"   ❌ {e}")
        print("\nSet environment variables before running.\n")
        return False

    print("✅ Config validated successfully")
    print(f"   Mode:     {'DEMO (safe)' if TRADING_CONFIG['demo_mode'] else '🔴 LIVE'}")
    print(f"   Pairs:    {TRADING_CONFIG['active_pairs']}")
    print(f"   Max Risk: {TRADING_CONFIG['max_risk_per_trade']*100}% per trade")

    print("   ℹ️  Live data only — no static market-data fallback is enabled")
    print("      DXY: Yahoo Finance | COT: CFTC | Calendar: Forex Factory")
    print("      Rates: Fed open-market page + ECB key-rates page")
    print("      Retail sentiment: OANDA EUR/USD position book")
    if not FINNHUB_API_KEY and not NEWS_API_KEY:
        print("      Headlines disabled — set FINNHUB_API_KEY or NEWS_API_KEY")
    return True


if __name__ == "__main__":
    validate_config()
