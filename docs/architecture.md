# Architecture

This project is organized around one simple runtime flow:

1. Build market context from broker and fundamentals feeds.
2. Ask the analysis layer to produce a trade proposal.
3. Validate the proposal against deterministic runtime guards.
4. Execute only if the proposal survives those guards.
5. Persist logs, timelines, and post-trade feedback for review.

## Package Map

```text
app/
├── analysis/
│   ├── agent.py              # Orchestrates the analysis pipeline
│   ├── prompt.py             # Static Claude system prompt
│   ├── message_builder.py    # Builds the live-market user prompt
│   ├── signal_pipeline.py    # JSON parsing and execution gating
│   ├── decision_logging.py   # Analysis and score-calibration logs
│   ├── confluence_scorer.py  # Deterministic confluence model
│   ├── market_analysis.py    # Structural / indicator calculations
│   ├── scheduler.py          # Session windows and polling cadence
│   └── trade_feedback.py     # Post-trade review and feedback notes
├── api/
│   ├── server.py             # FastAPI routes
│   ├── models.py             # API response schemas
│   ├── log_queries.py        # Read-side log helpers for the API
│   ├── frontend_contract.py  # Machine-readable frontend semantics
│   └── live_snapshot_service.py # Cached live snapshot refresh service
├── brokers/
│   └── oanda.py              # Broker market/account integration
├── backtesting/
│   ├── data_loader.py        # Historical candle loading and timeframe alignment
│   ├── signal_replayer.py    # Deterministic replay engine
│   ├── replay_confluence.py  # Replay-specific confluence normalization
│   ├── outcome_simulator.py  # Historical trade lifecycle simulation
│   ├── report.py             # Backtest performance reporting
│   └── historical_fundamentals_provider.py # Local macro/news datasets for replay
├── cli/
│   └── main.py               # CLI runtime modes and loop entrypoint
├── core/
│   ├── config.py             # Runtime configuration
│   ├── text_utils.py         # Formatting / normalization helpers
│   └── trade_management.py   # Shared trade-management rules
├── execution/
│   ├── trade_executor.py     # Order placement and monitoring
│   └── trade_journal.py      # Trade state, timelines, and close records
├── fundamentals/
│   ├── fetcher.py            # Cached fundamentals aggregation and freshness policy
│   ├── providers.py          # Source-specific fetch logic
│   └── common.py             # Shared parsing and cache helpers
├── logs/
│   └── signal_logs.py        # Signal log persistence and metadata
└── rag/
    └── pipeline.py           # Retrieval-augmented knowledge pipeline
```

## Runtime Responsibilities

### `analysis`
- The only place that should decide what the market setup means.
- `agent.py` is intentionally thin: retrieve context, build prompt, call Claude, validate, log.
- Deterministic checks live outside the prompt in `signal_pipeline.py` and `confluence_scorer.py`.

### `execution`
- Turns a valid proposal into broker actions.
- Owns TP1, first-hour early-momentum exits for stalled trades, trailing stop, close handling, and trade timelines.
- Should not reinterpret market context. It should trust validated signal fields.

### `api`
- Read-oriented surface for the frontend.
- Should compose data from logs and live snapshots, not embed trading logic.
- Returns the latest available cached snapshot and refreshes upstream data asynchronously when requested.

### `fundamentals` and `brokers`
- External data acquisition only.
- Keep source-specific parsing here so the analysis and execution layers stay focused on trading logic.
- `fundamentals/fetcher.py` owns freshness policy. The economic-calendar feed currently refreshes on fixed 6-hour UTC slots and advances through cached upcoming events locally between refreshes.
- `brokers/oanda.py` owns broker-derived portfolio state such as stop-based open risk when stop-loss data is present.

### `backtesting`
- Replays the deterministic stack without live API calls.
- Uses local CSV-backed macro/news datasets through `historical_fundamentals_provider.py`.
- Should stay close to live execution behavior for TP1, early exits, trailing, and time-stop logic so replay remains comparable to runtime behavior.

### `logs` and `trade_feedback`
- Preserve what happened and why.
- Signal logs capture each analysis.
- Trade timelines connect entry analysis, loop updates, and close events in one JSON file per trade.
- Feedback turns closed trades into review material and RAG memory.

## Design Rules

- Keep orchestration files thin.
- Prefer pure helper modules for parsing, scoring, and classification.
- Put deterministic runtime guards in code, not in the LLM prompt.
- Preserve analysis output separately from execution decisions.
- Treat logs as a first-class interface because the API and review flow depend on them.
