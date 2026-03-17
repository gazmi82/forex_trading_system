# Forex Trading System Frontend Integration Agent

## Purpose

This document is the frontend integration brief for a Lovable-generated website/app that will sit on top of the current backend REST API.

The frontend stack must be:

- React
- TypeScript
- Redux
- Axios
- React Query
- Chart.js

The frontend should be built as an operator dashboard, not as a marketing website.

The tone should feel:

- serious
- clear
- operational
- data-first
- trustworthy

Avoid generic fintech fluff, hero sections, or fake demo cards. This is a live trading-ops interface for one instrument, `EUR/USD`, backed by real broker and macro data.

---

## Story Behind The Project

This project started as a terminal-based forex analyst and demo execution system focused on `EUR/USD`.

The goal was never to build a generic “AI trading bot.” The goal was to build a disciplined decision engine that combines:

- live OANDA market data
- macro and fundamental context
- a local RAG knowledge base built from books, research papers, and trading notes
- strict session rules inspired by ICT kill zones
- hard risk controls that can block bad trades even if the model produces an opinion

The project has gradually been refactored to remove fake or static fallbacks. The current philosophy is:

- use live data whenever possible
- fail closed when live data is unavailable
- never invent market state for UI convenience
- surface missing data explicitly so the operator can take action

This matters for the frontend. The UI must not “smooth over” unavailable data. If the backend says a feed is unavailable, the frontend must show that clearly rather than guessing or filling the gap with placeholders that look real.

The system currently focuses on:

- one pair: `EUR/USD`
- one broker path: `OANDA`
- one primary live dashboard use case: analysis, diagnostics, and monitoring

The system also has a scheduler. New entry analysis is not supposed to run all day. It is only allowed:

- Monday to Friday
- during the allowed trade windows enforced by the backend

The frontend should make those windows obvious.

---

## Product Goal

Build a frontend that lets an operator answer these questions immediately:

1. Is the live data stack healthy right now?
2. What is the current market state for `EUR/USD`?
3. Are we inside an allowed entry-analysis window?
4. What was the last signal and why was it blocked or accepted?
5. Are there open or recently closed trades?
6. Which feeds are healthy and which are degraded?
7. Is the system blocked because of session timing, news risk, or Claude API/billing issues?

This first version should optimize for visibility and reliability, not for manual execution controls.

---

## Non-Negotiable Backend Truths

The frontend must respect these rules:

### 1. No fake data

The backend was explicitly refactored to remove static market-data fallbacks.

If a feed is missing, show it as:

- unavailable
- degraded
- needs attention

Do not fabricate values.

### 2. Analysis is session-gated

Entry analysis is only allowed:

- Monday to Friday
- during the backend-defined allowed trade windows

The backend exposes this through scheduler state. The frontend must trust the backend instead of recalculating from the browser clock.

### 3. Monitoring and analysis are different

- Monitoring open trades has no Claude token cost.
- Entry analysis uses Claude and can fail because of API/billing limits.

The UI should distinguish:

- live market monitoring
- AI analysis

### 4. A neutral fallback after Claude failure is not a real signal

If Anthropic fails, the backend logs a fallback payload like:

- `signal.direction = NEUTRAL`
- `confidence = 0`
- `validator_overrides = ["BLOCKED: Claude API unavailable"]`

The frontend must display this as an error/failure state, not as a legitimate neutral market opinion.

### 5. This is a live-only ops dashboard

The app should look like a control room for a running system, not like a brochure.

---

## Backend API Overview

Current backend file:

- [api_server.py](/Users/gazmirsulcaj/forex_trading_system/api_server.py)

Framework:

- FastAPI

Current version:

- `0.1.0`

Local dev server:

```bash
uvicorn api_server:app --reload
```

Typical local base URL:

```text
http://127.0.0.1:8000
```

Current CORS allowlist:

- `http://localhost:3000`
- `http://127.0.0.1:3000`
- `http://localhost:5173`
- `http://127.0.0.1:5173`

In addition:

- `https://*.lovable.app` is allowed by default
- extra deployed frontend origins can be added with `FRONTEND_ORIGINS`

Example:

```bash
export FRONTEND_ORIGINS="https://your-frontend.example.com,https://another.example.com"
```

There is currently no frontend auth layer in the API. Treat this as an internal/operator dashboard.

---

## Frontend Architecture

### Use React Query for server state

Use React Query for:

- live snapshot
- scheduler status
- feed diagnostics
- latest signal
- open trades
- closed trades
- trade history
- latest decisions
- dashboard summary

These are server-owned resources with polling behavior.

### Use Redux for app/UI state

Use Redux for:

- layout state
- selected panels/tabs
- active chart timeframe
- operator preferences
- manual refresh intent
- selected trade row
- selected log row
- filter state
- toast/notification state

Do not store the actual API data as the primary source in Redux if React Query already owns it.

### Use Axios as the HTTP transport

Create one shared Axios client with:

- `baseURL`
- request timeout
- response error normalization

### Use Chart.js for visualization

Use Chart.js for:

- price and trend summary visualizations
- feed-health overview
- equity and trade history charts
- signal-confidence or confluence trend charts

Do not use Chart.js for things the backend does not currently provide, such as full broker candle history, unless a new endpoint is added later.

---

## Recommended Frontend Information Architecture

### Screen 1: Operations Dashboard

Top-level operational snapshot:

- current `EUR/USD` price
- spread
- active session
- scheduler state
- next allowed entry window
- latest signal status
- feed health summary
- account equity
- open trades count

### Screen 2: Live Market Snapshot

Detailed live market state from `/api/live/snapshot`:

- price
- OHLCV-derived structure
- trend stack
- indicators
- fundamental block
- portfolio block

### Screen 3: Signal Center

Show:

- latest signal file
- signal direction
- confidence
- validator overrides
- do-not-trade reason
- Claude failure state if present

### Screen 4: Trades

Show:

- open trades
- closed trades
- history table from CSV

### Screen 5: Diagnostics

Show:

- feed availability
- last known values
- scheduler timing
- health checks
- decision log entries

---

## API Endpoints

## `GET /api/health`

### Purpose

High-level service status and environment readiness.

### Response

```ts
type HealthResponse = {
  status: string;
  service: string;
  utc_time: string;
  oanda_configured: boolean;
  anthropic_configured: boolean;
  log_files: {
    signals: number;
    test_signals: number;
    live_snapshots: number;
  };
};
```

### Frontend use

Use this for:

- app boot health check
- status badge in the header
- warning banner if OANDA or Anthropic is not configured

### Suggested React Query config

- `staleTime: 60_000`
- `refetchInterval: 60_000`

---

## `GET /api/live/snapshot`

### Query params

- `refresh: boolean = true`
- `persist: boolean = false`

### Purpose

Get the full market snapshot used by the backend runtime.

If `refresh=true`, the backend builds a fresh live snapshot from OANDA and the live feed stack.

If `refresh=false`, the backend can serve the latest saved live snapshot from logs, falling back to a fresh one if needed.

### Real payload shape

The payload is large. A real example currently contains:

```ts
type LiveSnapshot = {
  pair: string;
  price: number;
  spread: number;
  demo_mode: boolean;
  ohlcv: {
    day_open: number;
    week_open: number;
    month_open: number;
    prev_day_high: number;
    prev_day_low: number;
    prev_week_high: number;
    prev_week_low: number;
    weekly_structure: string;
    daily_structure: string;
    h4_structure: string;
    h1_structure: string;
    m15_structure: string;
    weekly_trend: string;
    daily_trend: string;
    h4_trend: string;
    h1_trend: string;
    m15_trend: string;
  };
  indicators: {
    ema20_4h: number;
    ema50_4h: number;
    ema200_daily: number;
    rsi_4h: number;
    rsi_1h: number;
    adx_4h: number;
    atr_4h: number;
    market_regime: string;
    resistance_levels: number[];
    support_levels: number[];
    round_numbers: number[];
    premium_discount_zone: string;
    bullish_ob: string;
    bearish_ob: string;
    bullish_fvg: string;
    bearish_fvg: string;
    recent_liquidity_sweep: string;
    ote_zone: [number, number];
  };
  fundamental: {
    usd_rate: number | null;
    fed_target_lower_rate: number | null;
    fed_target_upper_rate: number | null;
    pair_rate: number | null;
    ecb_main_refi_rate: number | null;
    ecb_marginal_lending_rate: number | null;
    ecb_deposit_rate: number | null;
    rate_differential: string | null;
    dxy_direction: string | null;
    dxy_level: string | null;
    cot_net: string | null;
    cot_bias: string | null;
    retail_sentiment: string | null;
    risk_sentiment: string | null;
    rates_source: string | null;
    next_event_name: string | null;
    next_news_event: string | null;
    time_to_event: string | null;
    news_risk: string | null;
    recent_headline: string | null;
    active_session: string;
    kill_zone_active: string;
    trade_window_active: boolean;
  };
  portfolio: {
    equity: number;
    open_trades: number;
    open_risk_pct: number;
    daily_pnl_pct: number;
    trades_today: number;
    usd_exposure: string;
    margin_used_pct: number;
  };
  fetch_time: string;
};
```

### Frontend use

This is the core data source for:

- market snapshot screen
- dashboard hero numbers
- trend stack cards
- macro and feed panels

### Suggested React Query config

- use a dedicated hook like `useLiveSnapshot`
- in the dashboard, prefer `refresh=false` for passive reads if another route already refreshes
- when the operator presses refresh, call with `refresh=true`

### Suggested polling

- if this page is visible: every `60s` to `120s`
- if hidden/background: disable or slow polling

### Error states

This endpoint can return:

- `503` if OANDA is unavailable
- `502` if live snapshot build fails

Render these as live-data failures, not empty states.

---

## `GET /api/market/candles`

### Query params

- `pair: "EUR_USD" = "EUR_USD"`
- `granularity: "M1" | "M5" | "M15" | "M30" | "H1" | "H4" | "D" | "W" = "M15"`
- `count: number = 200`

### Purpose

Return real OANDA OHLCV candles for the frontend chart.

### Response

```ts
type MarketCandlesResponse = {
  pair: "EUR_USD";
  granularity: "M1" | "M5" | "M15" | "M30" | "H1" | "H4" | "D" | "W";
  count: number;
  candles: Array<{
    time: string;
    open: number;
    high: number;
    low: number;
    close: number;
    volume: number;
  }>;
};
```

### Frontend use

Use this endpoint for the EUR/USD chart panel.

Recommended default:

- `granularity=M15`
- `count=200`

Useful presets:

- intraday detail: `M5`, `M15`
- session context: `H1`
- higher timeframe structure: `H4`, `D`

### Chart.js note

If you want a true candlestick chart, Chart.js alone is not enough. Use:

- `chart.js`
- plus the financial chart plugin, typically `chartjs-chart-financial`

If you want to stay strict with only base Chart.js, render:

- a line chart using `close`
- and optional overlay bands or markers for support/resistance

### Suggested query keys

```ts
["market-candles", pair, granularity, count];
```

### Suggested polling

- visible chart: every `30s` to `60s` for `M5` or `M15`
- higher timeframes: every `60s` to `180s`

### Example frontend transform

For financial chart plugins:

```ts
const data = response.candles.map((c) => ({
  x: new Date(c.time),
  o: c.open,
  h: c.high,
  l: c.low,
  c: c.close,
}));
```

---

## `GET /api/status/scheduler`

### Query params

- `refresh: boolean = true`

### Purpose

Expose the exact backend scheduler state that determines whether entry analysis is allowed right now.

### Response

```ts
type SchedulerStatusResponse = {
  utc_time: string;
  new_york_time: string;
  weekday: string;
  session: string;
  analysis_allowed_now: boolean;
  schedule_reason: string;
  next_poll_seconds: number;
  next_entry_window_start_ny: string | null;
  trade_window_active: boolean;
};
```

### Important frontend rule

Do not recalculate scheduler logic in the browser.

Use this endpoint as the source of truth for:

- current session
- whether entry analysis is allowed
- next allowed window
- next backend poll interval

### Real backend behavior

The backend currently enforces:

- Monday to Friday only
- allowed entry windows only
- outside allowed windows: monitor-only behavior

The UI should present:

- `Analysis Active`
- `Outside Allowed Trade Window`
- `Weekend Block`

based on `analysis_allowed_now` and `schedule_reason`.

### Suggested React Query config

- `staleTime: 10_000`
- `refetchInterval: 30_000`

---

## `GET /api/diagnostics/feeds`

### Query params

- `refresh: boolean = false`

### Purpose

Give a normalized feed-health view for the frontend.

### Response

```ts
type FeedDiagnosticsResponse = {
  utc_time: string;
  diagnostics: {
    oanda_market_data: {
      available: boolean;
      value: number | null;
    };
    rates: {
      available: boolean;
      value: string | null;
      source: string | null;
    };
    dxy: {
      available: boolean;
      value: string | null;
    };
    cot: {
      available: boolean;
      value: string | null;
    };
    calendar: {
      available: boolean;
      value: string | null;
      time_to_event: string | null;
    };
    headlines: {
      available: boolean;
      value: string | null;
    };
    retail_sentiment: {
      available: boolean;
      value: string | null;
    };
    risk_sentiment: {
      available: boolean;
      value: string | null;
    };
  };
};
```

### Frontend use

This should drive a feed-status panel with:

- green = available
- amber = degraded
- red = unavailable

### Suggested React Query config

- `staleTime: 30_000`
- `refetchInterval: 60_000`

---

## `GET /api/signals/latest`

### Query params

- `kind: "signal" | "test_signal" = "signal"`

### Purpose

Return the latest saved signal log envelope.

### Response

```ts
type LogEnvelope<T = Record<string, unknown>> = {
  filename: string;
  modified_at: string;
  recorded_at: string | null;
  age_seconds: number | null;
  is_stale: boolean;
  status: "OK" | "FAILED" | "STALE" | "STALE_FAILED";
  data: T;
};
```

A real failure payload currently looks like:

```ts
type LatestSignalFailure = {
  error: string;
  signal: {
    direction: "NEUTRAL";
    confidence: 0;
  };
  do_not_trade_reason: string;
  demo_mode: boolean;
  validator_overrides: string[];
};
```

### Frontend rule

If `status` is `FAILED` or `STALE_FAILED`, render:

- `Claude Analysis Failed`

Do not render this as a normal neutral signal card.

If `is_stale` is `true`, visibly label the signal as stale and use `recorded_at`
as the analysis time, not the dashboard fetch time.

### Suggested React Query config

- `staleTime: 10_000`
- `refetchInterval: 30_000`

### Empty state

If no logs exist, the backend returns `404`. Show:

- `No signal logs yet`

---

## `GET /api/trades/open`

### Purpose

Return the current open-trade tracker state from `open_trades.json`.

### Response

```ts
type OpenTradesResponse = {
  count: number;
  items: OpenTradeItem[];
};

type OpenTradeItem = {
  order_id?: string | null;
  trade_id?: string | null;
  instrument: string;
  direction: string;
  units: number;
  entry_price: number;
  stop_loss: number;
  tp1: number;
  tp2: number;
  risk_reward: number;
  tp1_hit: boolean;
  open_time: string;
  confluence: number;
  confidence: number;
  session: string;
};
```

### Frontend use

Display as:

- open trades table
- open trades count card
- trade detail drawer

### Suggested React Query config

- `staleTime: 10_000`
- `refetchInterval: 30_000`

---

## `GET /api/trades/closed`

### Query params

- `limit: number = 20`

### Purpose

Return recently closed trades from `closed_trades.jsonl`.

### Response

```ts
type ClosedTradesResponse = {
  count: number;
  items: ClosedTradeItem[];
};

type ClosedTradeItem = {
  date: string;
  pair: string;
  direction: string;
  entry_price: number;
  stop_loss: number;
  take_profit: number;
  lot_size: number;
  outcome: "WIN" | "LOSS" | "BREAKEVEN";
  pnl_r: number;
  pnl_usd: number;
  duration_hours: number | string;
  session: string;
  confluence_score: number;
  close_reason: string;
};
```

### Frontend use

Use for:

- recent outcomes
- trade outcome table
- R-multiple summary cards

### Suggested React Query config

- `staleTime: 30_000`
- `refetchInterval: 60_000`

---

## `GET /api/trades/history`

### Query params

- `limit: number = 50`

### Purpose

Return trade history rows from `trades.csv`.

### Response

```ts
type TradeHistoryResponse = {
  count: number;
  items: Record<string, string>[];
};
```

The CSV is not strongly typed yet, so the frontend should treat this endpoint as a log table.

### Frontend use

Use this for:

- audit log table
- export/download later

Do not build critical business logic on this endpoint yet.

---

## `GET /api/decisions/latest`

### Query params

- `limit: number = 20`

### Purpose

Return the latest agent decision log records from `agent_decisions.jsonl`.

### Response

```ts
type DecisionsResponse = {
  count: number;
  items: Record<string, unknown>[];
};
```

### Frontend use

Use this for:

- reasoning timeline
- diagnostics page
- latest decision history panel

---

## `GET /api/dashboard/summary`

### Query params

- `refresh_live: boolean = false`

### Purpose

Provide one consolidated payload for the dashboard screen.

### Response

```ts
type DashboardSummaryResponse = {
  utc_time: string;
  scheduler: SchedulerStatusResponse;
  live_snapshot: LiveSnapshot;
  feed_diagnostics: FeedDiagnosticsResponse["diagnostics"];
  latest_signal: LogEnvelope | null;
  open_trades: OpenTradesResponse;
};
```

### Frontend use

This should power the main dashboard route.

Recommended usage:

- use this endpoint for the home/dashboard page
- use the specialized endpoints for detail pages

### Suggested React Query config

- `staleTime: 15_000`
- `refetchInterval: 30_000`

---

## `POST /api/signals/log-test-failure`

### Purpose

Persist a signal-like payload to disk using the same backend log format as runtime signals.

### Request body

Any signal-shaped JSON payload.

### Response

```ts
type LogTestFailureResponse = {
  logged_to: string;
};
```

### Frontend use

This endpoint is not for the normal operator dashboard.

Use it only for:

- frontend testing
- QA tools
- simulation utilities

---

## Recommended TypeScript Models

Create a dedicated file:

```text
src/types/api.ts
```

Include at least:

```ts
export interface HealthResponse {
  status: string;
  service: string;
  utc_time: string;
  oanda_configured: boolean;
  anthropic_configured: boolean;
  log_files: {
    signals: number;
    test_signals: number;
    live_snapshots: number;
  };
}

export interface SchedulerStatusResponse {
  utc_time: string;
  new_york_time: string;
  weekday: string;
  session: string;
  analysis_allowed_now: boolean;
  schedule_reason: string;
  next_poll_seconds: number;
  next_entry_window_start_ny: string | null;
  trade_window_active: boolean;
}

export interface LogEnvelope<T = Record<string, unknown>> {
  filename: string;
  modified_at: string;
  data: T;
}
```

Then add focused types for:

- `LiveSnapshot`
- `FeedDiagnosticsResponse`
- `OpenTradeItem`
- `ClosedTradeItem`
- `DashboardSummaryResponse`

Keep the first implementation pragmatic. Do not try to model every nested field from every backend response before the UI needs it.

---

## Axios Integration

Create:

```text
src/lib/apiClient.ts
```

Suggested implementation:

```ts
import axios from "axios";

export const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000",
  timeout: 15000,
});

apiClient.interceptors.response.use(
  (response) => response,
  (error) => {
    const message =
      error?.response?.data?.detail || error?.message || "Unknown API error";

    return Promise.reject({
      ...error,
      normalizedMessage: message,
    });
  },
);
```

Create one API module per domain:

- `src/api/healthApi.ts`
- `src/api/snapshotApi.ts`
- `src/api/schedulerApi.ts`
- `src/api/signalsApi.ts`
- `src/api/tradesApi.ts`
- `src/api/diagnosticsApi.ts`
- `src/api/dashboardApi.ts`

---

## React Query Strategy

Suggested query keys:

```ts
["health"][("dashboard-summary", refreshLive)][
  ("live-snapshot", refresh, persist)
][("scheduler-status", refresh)][("feed-diagnostics", refresh)][
  ("latest-signal", kind)
]["open-trades"][("closed-trades", limit)][("trade-history", limit)][
  ("latest-decisions", limit)
];
```

Suggested hooks:

- `useHealth()`
- `useDashboardSummary()`
- `useLiveSnapshot()`
- `useSchedulerStatus()`
- `useFeedDiagnostics()`
- `useLatestSignal()`
- `useOpenTrades()`
- `useClosedTrades()`
- `useTradeHistory()`
- `useLatestDecisions()`

### Polling recommendations

Use the backend scheduler as guidance, but the frontend can still poll safely on its own cadence.

Suggested frontend polling:

- Dashboard summary: every `30s`
- Scheduler status: every `30s`
- Live snapshot detail page: every `60s`
- Open trades: every `30s`
- Closed trades: every `60s`
- Feed diagnostics: every `60s`
- Health: every `60s`

Pause or slow polling when the tab is hidden.

---

## Redux State Design

Create slices like:

- `layoutSlice`
- `preferencesSlice`
- `uiFiltersSlice`
- `notificationsSlice`

Suggested Redux state:

```ts
type RootUiState = {
  layout: {
    sidebarOpen: boolean;
    activeRoute: string;
    selectedPanel: string | null;
  };
  preferences: {
    theme: "light" | "dark";
    autoRefreshEnabled: boolean;
    compactTables: boolean;
  };
  uiFilters: {
    tradesLimit: number;
    decisionsLimit: number;
    signalKind: "signal" | "test_signal";
  };
  notifications: {
    items: Array<{
      id: string;
      type: "info" | "warning" | "error" | "success";
      message: string;
    }>;
  };
};
```

Do not duplicate React Query server data into Redux unless there is a strong reason.

---

## Chart.js Recommendations

### 1. Feed Health Doughnut or Horizontal Bar

Use `/api/diagnostics/feeds` to visualize:

- available feeds
- degraded feeds
- unavailable feeds

### 2. Signal History Trend

When enough signal logs are available, chart:

- confidence over time
- confluence score over time

The backend does not currently expose a dedicated historical signal endpoint, so this may require a later backend extension or client-side composition from logged files.

### 3. Closed Trade Outcome Chart

Use `/api/trades/closed` to show:

- wins vs losses vs breakeven
- PnL in USD
- PnL in R

### 4. Trade History Timeline

Use `/api/trades/history` for a simple timeline or event chart.

### 5. Scheduler Timeline

Use `/api/status/scheduler` to visually show:

- current session
- active/inactive window
- next entry window start

Avoid fake candlestick charts unless a candle-history API is added later.

---

## UI/UX Behavior Rules

### Rule 1: Show system state before interpretation

The dashboard header should always make these visible:

- OANDA connectivity
- current session
- analysis allowed now or not
- latest signal status

### Rule 2: Differentiate availability from value

Bad pattern:

- `Calendar: N/A`

Better pattern:

- `Calendar feed unavailable`
- `Headlines not configured`
- `Claude unavailable due to billing`

### Rule 3: Treat Claude failure as a first-class UI state

If the latest signal contains:

- `error`
- `BLOCKED: Claude API unavailable`

show a prominent error state card:

- title: `Claude analysis unavailable`
- subtitle: use `do_not_trade_reason`

### Rule 4: Use serious visual hierarchy

Recommended visual order:

1. scheduler state
2. live price and spread
3. latest signal status
4. feed health
5. trends and macro state
6. open trades

### Rule 5: The UI should teach the operator why no trade happened

Common reasons:

- outside allowed window
- weekend block
- news blackout
- low confidence
- Claude unavailable

These should be readable in one glance.

---

## Suggested Frontend Page Structure

```text
src/
  api/
  app/
  components/
  features/
    dashboard/
    diagnostics/
    market/
    scheduler/
    signals/
    trades/
  hooks/
  lib/
  store/
  types/
```

Suggested top-level routes:

- `/`
- `/market`
- `/signals`
- `/trades`
- `/diagnostics`

Recommended shared components:

- `StatusBadge`
- `MetricCard`
- `FeedHealthCard`
- `SchedulerCard`
- `SignalStatusCard`
- `TradeTable`
- `DiagnosticsTable`
- `EmptyState`
- `ApiErrorBanner`

---

## Example Feature Mapping

### Dashboard route

Data source:

- `/api/dashboard/summary`

Widgets:

- Scheduler card
- Live price card
- Spread card
- Latest signal card
- Feed diagnostics strip
- Open trades summary

### Market route

Data sources:

- `/api/live/snapshot`
- `/api/diagnostics/feeds`

Widgets:

- Trend stack
- Indicator cards
- Macro block
- Risk block
- Positioning block

### Signals route

Data sources:

- `/api/signals/latest`
- `/api/decisions/latest`

Widgets:

- Latest signal detail
- Validation overrides
- Claude/runtime error card
- Decision log table

### Trades route

Data sources:

- `/api/trades/open`
- `/api/trades/closed`
- `/api/trades/history`

Widgets:

- Open trades table
- Closed trades table
- Outcome chart
- Trade history audit table

### Diagnostics route

Data sources:

- `/api/health`
- `/api/status/scheduler`
- `/api/diagnostics/feeds`

Widgets:

- Service health
- Feed health
- Scheduler state
- Log counts

---

## Error Handling Strategy

### Backend 404

Meaning:

- no logs yet
- no saved signal yet

Frontend action:

- show empty state, not red failure

### Backend 502 / 503

Meaning:

- live snapshot failed
- OANDA unavailable

Frontend action:

- show red error card
- preserve last successful data if already loaded
- clearly label stale data

### Claude failure in signal payload

Meaning:

- backend reached analysis step
- Anthropic failed

Frontend action:

- show `analysis failed` state
- do not render it as valid market guidance

---

## Lovable Build Instructions

Build the frontend as a dense, modern operations dashboard for a single-instrument trading system.

Important design direction:

- Do not create a landing page.
- Do not create signup or pricing sections.
- Do not create fake historical performance widgets.
- Do not invent charts that are not backed by the current API.
- Prefer compact cards, tables, diagnostic strips, and scheduler visibility.
- Make the latest system state visible above the fold.

The first screen should immediately show:

- current session
- whether analysis is allowed now
- next entry window
- latest signal status
- live EUR/USD price
- feed availability

The frontend should feel like a control panel used by one operator watching a live system.

---

## Future Work Notes

These are important but not part of the first frontend build:

- economic calendar should later move to a more solid documented endpoint
- news feed should later move to a more solid documented endpoint
- USD and EUR rates should later move to more solid documented endpoints

At the moment, some fundamental data is still derived from webpage scraping or public JSON-style sources. The frontend should treat the backend as the contract, but keep the diagnostics panel visible because those feeds can degrade.

---

## Final Build Priority

If you must prioritize, build in this order:

1. Dashboard
2. Scheduler state
3. Latest signal card
4. Feed diagnostics
5. Open/closed trades tables
6. Diagnostics screen
7. Charts and trend visuals

This order matches the operational value of the backend.
