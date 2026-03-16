# Forex Analyst Agent — Option 1 + Option 2 Full Integration

## What This System Does

Combines two layers of intelligence:

**Option 1 — System Prompt (Permanent Knowledge)**
Deep expertise baked into the agent's identity. Covers ICT concepts,
market sessions, risk rules, COT analysis, fundamentals, and psychology.
This is always active and never changes at runtime.

**Option 2 — RAG Pipeline (Dynamic Knowledge)**
Your books, research papers, and ICT transcripts converted into a
searchable vector database. At every analysis, the system automatically
finds and injects the most relevant knowledge passages into Claude's context.

---

## File Structure

```
trading_system/
├── main.py               ← Entry point — run this
├── config.py             ← All settings (pairs, risk, RAG config)
├── rag_pipeline.py       ← RAG: ingest PDFs, embed, store, retrieve
├── pdf_to_markdown.py    ← Convert PDFs into cleaned Markdown for RAG
├── agent_runner.py       ← Agent: Option 1 + Option 2 + Claude API
├── fundamentals_fetcher.py ← Live fundamentals: DXY, COT, calendar, headlines
├── requirements.txt      ← All dependencies
│
├── documents/            ← DROP YOUR FILES HERE
│   ├── books/            ← Kathy Lien, Mark Douglas PDFs
│   ├── ict/              ← ICT transcripts (TXT files)
│   ├── research/         ← BIS, Fed, SSRN papers (PDFs)
│   ├── cot/              ← COT notes
│   └── journal/          ← Your manual trading journal entries
│
├── chroma_db/            ← Vector database (auto-created, do not edit)
├── logs/                 ← Trade logs, agent decisions (auto-created)
├── journal/              ← Reserved top-level journal directory
└── feedback/             ← Auto-generated markdown trade review notes
```

---

## Setup (5 Steps)

### Step 1 — Install Dependencies
```bash
pip install -r requirements.txt
```

### Step 2 — Set API Keys
```bash
# Required
export ANTHROPIC_API_KEY="your_anthropic_key_here"

# Required for live broker data / demo execution
export OANDA_API_KEY="your_oanda_key_here"
export OANDA_ACCOUNT_ID="your_account_id_here"

# Optional, but recommended for live fundamentals
export FINNHUB_API_KEY="your_finnhub_key_here"
export NEWS_API_KEY="your_newsapi_key_here"
```

Or create a `.env` file:
```
ANTHROPIC_API_KEY=your_anthropic_key_here
OANDA_API_KEY=your_oanda_key_here
OANDA_ACCOUNT_ID=your_account_id_here
FINNHUB_API_KEY=your_finnhub_key_here
NEWS_API_KEY=your_newsapi_key_here
```

Notes:
- USD target range is fetched from the official Fed open-market page.
- EUR policy rates are fetched from the official ECB key-rates page with no key required.
- The bot exposes ECB main refi, marginal lending, and deposit rates; the deposit rate is used for the EUR benchmark in `rate_differential`.
- DXY is auto-fetched intraday from Yahoo Finance.
- COT is auto-fetched from CFTC.gov, but it is still a weekly report, not real-time.
- Retail sentiment is sourced only from the OANDA EUR/USD position book.
- The system fails closed when live broker data is unavailable; it does not synthesize example market data.

### Step 3 — Add Your Documents
Drop source documents into the appropriate folders:

```
documents/books/
  ├── kathy_lien_day_trading_currency_market.pdf
  └── mark_douglas_trading_in_the_zone.pdf

documents/ict/
  └── ict_mentorship_transcripts.txt   ← Copy from YouTube auto-captions

documents/research/
  ├── bis_fx_market_structure.pdf      ← bis.org/research (FREE)
  ├── fed_currency_research.pdf        ← federalreserve.gov (FREE)
  └── ssrn_eur_usd_momentum.pdf        ← ssrn.com (FREE)
```

### Step 4 — Convert PDFs to Markdown
Recommended for cleaner RAG input:
```bash
# Convert all PDFs under documents/* into same-folder .md files
python pdf_to_markdown.py --all

# Use OCR fallback for weak/scanned PDFs
python pdf_to_markdown.py --all --ocr-fallback
```

Notes:
- Converted `.md` files are written next to the source PDF.
- Ingest now prefers a same-stem `.md` over the raw `.pdf`, so you can keep both without double-indexing.

### Step 5 — Ingest Documents into RAG
```bash
python main.py --mode ingest
```
This embeds all your documents locally. ONE-TIME cost ~$0 (uses free local model).

### Step 6 — Run Analysis
```bash
# Test single analysis
python main.py --mode test

# Test single analysis with no order placement
python main.py --mode test --dry-run

# Show knowledge base stats
python main.py --mode stats

# Run continuous demo loop (every 30 min)
python main.py --mode demo
```

### Step 7 — Run the REST API
First frontend version is exposed through FastAPI:

```bash
uvicorn api_server:app --reload
```

If the frontend is deployed outside localhost, allow extra origins with:

```bash
export FRONTEND_ORIGINS="https://your-frontend.example.com,https://another.example.com"
```

The deployed frontend `https://style-whisperer-87.lovable.app` is allowed by default. Add any additional frontend hosts through `FRONTEND_ORIGINS`.

Key first-version endpoints:

```text
GET  /api/health
GET  /api/live/snapshot
GET  /api/market/candles
GET  /api/status/scheduler
GET  /api/diagnostics/feeds
GET  /api/signals/latest
GET  /api/trades/open
GET  /api/trades/closed
GET  /api/trades/history
GET  /api/decisions/latest
GET  /api/dashboard/summary
```

Notes:
- `/api/live/snapshot?refresh=true` builds a fresh OANDA + fundamentals snapshot.
- `/api/market/candles?pair=EUR_USD&granularity=M15&count=200` returns frontend-ready OHLCV candles.
- `/api/status/scheduler` uses the same Monday-Friday and kill-zone gate as the runtime.
- `/api/signals/latest` returns the latest saved `signal_*.json`.
- `/api/dashboard/summary` is the easiest first frontend endpoint because it combines scheduler, live snapshot, diagnostics, latest signal, and open trades.

### Step 8 — Publish the API Over HTTPS
The API already exposes the frontend contract. It is now deployed publicly on Render at:

```text
https://forex-trading-system.onrender.com
```

For local startup or manual verification:

```bash
python3 api_server.py
```

Production env vars:

```text
APP_ENV=production
PUBLIC_API_BASE_URL=https://forex-trading-system.onrender.com
FRONTEND_ORIGINS=https://style-whisperer-87.lovable.app
API_TRUSTED_HOSTS=forex-trading-system.onrender.com
```

Frontend integration uses:

```text
VITE_API_BASE_URL=https://forex-trading-system.onrender.com
```

Important:
- local frontend development may still use `http://127.0.0.1:8000`
- the hosted Lovable frontend must not use `127.0.0.1`
- if the hosted frontend console shows requests to `127.0.0.1`, the frontend env var was not applied or the app was not redeployed

This repo includes [`render.yaml`](/Users/gazmirsulcaj/forex_trading_system/render.yaml) for Render deployment and [`PUBLIC_API_DEPLOYMENT.md`](/Users/gazmirsulcaj/forex_trading_system/PUBLIC_API_DEPLOYMENT.md) for backend deployment checks. The frontend public API handoff has been exported locally to `/Users/gazmirsulcaj/Downloads/FRONTEND_PUBLIC_API_INTEGRATION.md`.

---

## How It Works at Runtime

```
Every 30 minutes:

1. RAG RETRIEVAL (Option 2)
   System builds 6-7 targeted queries based on market conditions:
   - "EUR/USD trading strategy order blocks fair value gaps"
   - "London Kill Zone institutional order flow"
   - "Risk management stop loss placement professional"
   - etc.
   
   Searches your vector database → returns most relevant passages
   from YOUR books and research papers

2. PROMPT ASSEMBLY
   Combines:
   - Option 1: Full system prompt (permanent rules)
   - Option 2: Top RAG passages (dynamic expertise)  
   - Live market data (price, indicators, account state)
   - Live fundamentals:
     - DXY intraday signal via Yahoo Finance
     - next high-impact US / Euro Area event via Forex Factory
     - latest FX headline via Finnhub or NewsAPI when configured
     - weekly COT positioning via CFTC.gov
   - Agent feedback memory (last 15 trade outcomes)

3. CLAUDE API CALL
   Single call to claude-sonnet with the full context.
   Claude outputs structured JSON trade signal.

4. VALIDATION
   Hard-coded Python safety rules check the signal:
   - Daily loss limit not exceeded?
   - Portfolio heat within limits?
   - Not within news blackout window?
   - Confidence meets minimum threshold?
   - Risk:Reward meets minimum 2:0?

5. LOGGING
   Everything logged to:
   - logs/agent_decisions.jsonl (full analysis log)
   - logs/trades.csv (trade outcomes)
   - logs/closed_trades.jsonl (structured closed-trade records)
   - logs/signal_*.json / logs/test_signal_*.json (signal payloads, including Claude failures)

6. FEEDBACK LOOP
   After each trade closes, outcome is stored back into RAG.
   Agent builds a memory of its own successes and failures.
   A readable markdown trade review is also written to:
   - feedback/feedback_YYYYMMDD_HHMMSS_pair_session_outcome.md
```

---

## Free Document Sources

Start with these — all completely free and legal:

| Source | URL | What to Download |
|--------|-----|-----------------|
| BIS | bis.org/research | FX market papers |
| Federal Reserve | federalreserve.gov/pubs | Currency research |
| ECB | ecb.europa.eu/pub | EUR/USD analysis |
| IMF | imf.org/en/Publications | Global FX papers |
| SSRN | ssrn.com | Search "forex momentum" |
| ArXiv | arxiv.org | Search "algorithmic forex" |

---

## Cost Estimate

| Item | Cost |
|------|------|
| Embedding 10 books locally | $0 (sentence-transformers is FREE) |
| ChromaDB vector store | $0 (runs locally) |
| Claude API (~30 analyses/day) | ~$15–20/month |
| OANDA demo account | $0 |
| Forex Factory calendar feed | $0 |
| NewsAPI | optional |
| **Total monthly** | **~$15–20/month** |

---

## Demo Period Rules

The system is configured for 12-month demo trading:

- `demo_mode = True` in config.py (never change this until month 12)
- All trades are simulated — no real money
- Full logging active from day 1
- Feedback loop builds agent memory over time

After 12 months of consistent profitability on demo:
1. Review all metrics in `logs/performance.csv`
2. Run Monte Carlo simulation
3. Only then change `demo_mode = False`
4. Start with 10% of intended live capital

---

## Live Fundamentals

The current fundamentals stack is mixed live + delayed:

- `DXY`: intraday auto-fetch from Yahoo Finance (`DX-Y.NYB`)
- `Calendar`: live next high-impact US / Euro Area event from Forex Factory
- `Headlines`: live FX headline from Finnhub or NewsAPI when configured
- `COT`: auto-fetch from CFTC.gov, but updated weekly
- `Retail sentiment`: sourced only from the OANDA EUR/USD position book

This means the system now has live broker data plus partially live fundamentals, but it is not yet a fully streaming macro stack.

Deferred infrastructure work:
- Economic calendar, news feed, and USD/EUR policy-rate fetching should later be migrated to solid documented provider endpoints.
- Public JSON feeds and webpage scraping are acceptable for now, but they are not the long-term target for production-grade reliability.

---

## Adding ICT Transcripts (Free)

1. Go to any ICT YouTube video
2. Click the "..." menu → "Open transcript"
3. Copy all text → save as `.txt` file
4. Drop into `documents/ict/`
5. Run `python main.py --mode ingest`

The agent will now reference ICT's exact words when relevant.
