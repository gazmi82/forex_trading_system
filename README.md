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
├── agent_runner.py       ← Agent: Option 1 + Option 2 + Claude API
├── fundamentals_fetcher.py ← Live fundamentals: DXY, COT, calendar, headlines
├── requirements.txt      ← All dependencies
│
├── documents/            ← DROP YOUR FILES HERE
│   ├── books/            ← Kathy Lien, Mark Douglas PDFs
│   ├── ict/              ← ICT transcripts (TXT files)
│   ├── research/         ← BIS, Fed, SSRN papers (PDFs)
│   ├── cot/              ← COT notes
│   └── journal/          ← Your trading journal entries
│
├── chroma_db/            ← Vector database (auto-created, do not edit)
├── logs/                 ← Trade logs, agent decisions (auto-created)
└── journal/              ← Feedback memory (auto-created)
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
export NEWS_API_KEY="your_newsapi_key_here"
export FMP_API_KEY="your_fmp_key_here"

# Optional manual overrides
export RETAIL_SENTIMENT="72% SHORT"
export USD_RATE="4.50"
export EUR_RATE="3.65"
```

Or create a `.env` file:
```
ANTHROPIC_API_KEY=your_anthropic_key_here
OANDA_API_KEY=your_oanda_key_here
OANDA_ACCOUNT_ID=your_account_id_here
NEWS_API_KEY=your_newsapi_key_here
FMP_API_KEY=your_fmp_key_here
RETAIL_SENTIMENT=72% SHORT
USD_RATE=4.50
EUR_RATE=3.65
```

Notes:
- `FMP_API_KEY` enables the live economic calendar feed.
- DXY is auto-fetched intraday from Yahoo Finance.
- COT is auto-fetched from CFTC.gov, but it is still a weekly report, not real-time.
- Retail sentiment is still manual unless you add your own provider integration.

### Step 3 — Add Your Documents
Drop PDF files into the appropriate folders:

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

### Step 4 — Ingest Documents into RAG
```bash
python main.py --mode ingest
```
This embeds all your documents locally. ONE-TIME cost ~$0 (uses free local model).

### Step 5 — Run Analysis
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
     - next high-impact US / Euro Area event via Financial Modeling Prep
     - latest FX headline via NewsAPI when configured
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

6. FEEDBACK LOOP
   After each trade closes, outcome is stored back into RAG.
   Agent builds a memory of its own successes and failures.
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
| FMP economic calendar (free tier) | $0 |
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
- `Calendar`: live next high-impact US / Euro Area event from Financial Modeling Prep
- `Headlines`: live FX headline from NewsAPI when `NEWS_API_KEY` is set
- `COT`: auto-fetch from CFTC.gov, but updated weekly
- `Retail sentiment`: still manual via `RETAIL_SENTIMENT`

This means the system now has live broker data plus partially live fundamentals, but it is not yet a fully streaming macro stack.

---

## Adding ICT Transcripts (Free)

1. Go to any ICT YouTube video
2. Click the "..." menu → "Open transcript"
3. Copy all text → save as `.txt` file
4. Drop into `documents/ict/`
5. Run `python main.py --mode ingest`

The agent will now reference ICT's exact words when relevant.
