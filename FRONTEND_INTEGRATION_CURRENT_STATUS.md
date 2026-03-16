# Frontend Integration Brief

## Project context

This project is a Python backend for a forex trading system focused on `EUR/USD`.
The frontend is a separate React/Vite app that reads backend state through REST APIs.

The backend already provides:

- live market snapshot data
- scheduler status
- feed diagnostics
- latest signal log
- trade history endpoints
- EUR/USD candle data for charts

The backend runs locally with:

```bash
uvicorn api_server:app --reload
```

By default that serves the API at:

```text
http://127.0.0.1:8000
```

## Important current architecture rule

The frontend must treat the Python backend as the source of truth.

Do not recreate backend trading logic in React.
The frontend should display backend state, not re-derive:

- active session
- kill zone / trade window state
- signal validity
- feed availability
- trade state

## Current backend endpoints

Main endpoints exposed by `api_server.py`:

- `GET /api/health`
- `GET /api/live/snapshot`
- `GET /api/market/candles`
- `GET /api/status/scheduler`
- `GET /api/diagnostics/feeds`
- `GET /api/signals/latest`
- `GET /api/trades/open`
- `GET /api/trades/closed`
- `GET /api/trades/history`
- `GET /api/decisions/latest`
- `GET /api/dashboard/summary`

## Recommended frontend data usage

### Dashboard page

Use:

- `/api/dashboard/summary?refresh_live=true`

This is the easiest combined endpoint because it returns:

- scheduler
- live snapshot
- feed diagnostics
- latest signal
- open trades

### Scheduler card

Do not use stale dashboard data as the only scheduler source.

Use:

- `/api/status/scheduler?refresh=true`

Reason:
- the scheduler card must show fresh New York time
- the session must match the current backend state
- this avoids stale mixed state like `London Session (Sunday)`

Recommended polling:

- refetch every `30s`

### Market page

Use:

- `/api/live/snapshot?refresh=true&persist=false`
- `/api/diagnostics/feeds`
- `/api/market/candles?pair=EUR_USD&granularity=M15&count=200`

### Signals page

Use:

- `/api/signals/latest?kind=signal`
- `/api/decisions/latest?limit=20`

### Trades page

Use:

- `/api/trades/open`
- `/api/trades/closed`
- `/api/trades/history`

## Candle chart integration

The candle endpoint returns:

```json
{
  "pair": "EUR_USD",
  "granularity": "M15",
  "count": 200,
  "candles": [
    {
      "time": "2026-03-13T14:30:00+00:00",
      "open": 1.1461,
      "high": 1.1465,
      "low": 1.1458,
      "close": 1.1462,
      "volume": 1234
    }
  ]
}
```

### Important chart requirement

If you want a true candlestick chart, the frontend must include:

- `chart.js`
- `react-chartjs-2`
- `chartjs-chart-financial`
- `chartjs-adapter-date-fns`

### Important bug already identified

The current frontend kept rendering a line chart because:

- `chartjs-chart-financial` was missing from `package.json`
- the chart component used a dynamic import and silent fallback to line mode

### Correct chart implementation rule

- register financial chart controllers statically
- do not silently swallow plugin load failure
- remount the chart when switching `line` <-> `candle`

Expected candle point shape:

```ts
{
  x: new Date(c.time),
  o: c.open,
  h: c.high,
  l: c.low,
  c: c.close,
}
```

## Networking rules

### Local development

For a local frontend running on your machine, this is acceptable:

```text
VITE_API_BASE_URL=http://127.0.0.1:8000
```

### Hosted Lovable preview

Do not use `127.0.0.1` in hosted preview.

Reason:
- the preview runs remotely
- `127.0.0.1` points to the remote environment itself, not your Mac

For hosted preview, use:

```text
VITE_API_BASE_URL=https://your-public-backend-url
```

### Correct Axios behavior

The API client should:

- use `VITE_API_BASE_URL` if present
- fall back to `http://127.0.0.1:8000` only in local development
- not silently use localhost in hosted builds

Recommended pattern:

```ts
const apiBaseUrl =
  import.meta.env.VITE_API_BASE_URL ||
  (import.meta.env.DEV ? "http://127.0.0.1:8000" : undefined);
```

## CORS / hosted preview note

The backend already allows:

- localhost dev origins
- `https://*.lovable.app`

If the hosted frontend origin uses a different Lovable domain variant, the backend CORS config may need to allow that exact host pattern too.

## Response-shape rules

### Feed diagnostics is an object, not an array

`/api/diagnostics/feeds` returns:

```json
{
  "utc_time": "...",
  "diagnostics": {
    "oanda_market_data": { "available": true, "value": 1.14619 },
    "rates": { "available": true, "value": "...", "source": "..." }
  }
}
```

So the frontend must not do:

- `diagnostics.length`
- `diagnostics.map(...)`

Instead:

```ts
const items = Object.entries(diagnostics ?? {});
```

Then use:

- `items.length`
- `items.map(...)`

## Defensive rendering rules

The backend may sometimes return incomplete data if a feed is temporarily unavailable.
The frontend must not assume every numeric field exists.

Do not do:

```ts
snapshot.price.toFixed(5)
snapshot.spread.toFixed(1)
snapshot.indicators.rsi_4h.toFixed(1)
```

Use safe formatters:

```ts
const formatFixed = (value: unknown, digits: number) =>
  typeof value === "number" && Number.isFinite(value)
    ? value.toFixed(digits)
    : "—";

const formatCurrency = (value: unknown) =>
  typeof value === "number" && Number.isFinite(value)
    ? `$${value.toLocaleString()}`
    : "—";
```

Then render:

```ts
formatFixed(snapshot?.price, 5)
`${formatFixed(snapshot?.spread, 1)} pips`
formatFixed(snapshot?.indicators?.rsi_4h, 1)
```

This prevents frontend crashes from `.toFixed()` on `undefined`.

## Scheduler rendering rules

The scheduler card should display:

- session
- weekday
- New York time
- next entry window
- schedule reason
- analysis allowed now

### Important bug already identified

If the frontend checks:

```ts
scheduler.schedule_reason.includes("weekend")
```

that is wrong because backend text uses `Weekend` with a capital `W`.

Use:

```ts
scheduler.schedule_reason.toLowerCase().includes("weekend")
```

## Suggested React Query setup

Recommended query keys:

- `["dashboard-summary", refreshLive]`
- `["scheduler-status"]`
- `["live-snapshot", refresh]`
- `["feed-diagnostics", refresh]`
- `["market-candles", pair, granularity, count]`
- `["latest-signal", kind]`
- `["latest-decisions", limit]`
- `["open-trades"]`
- `["closed-trades", limit]`
- `["trade-history", limit]`

Recommended polling:

- scheduler: `30s`
- live snapshot: `30s` to `60s`
- candles: `30s` to `60s` for intraday views
- feed diagnostics: `60s`
- latest signal / decisions: `60s`
- trades: `30s`

## Frontend implementation priorities

### First priority

- stable API client
- correct environment handling
- safe rendering for partial data
- working scheduler card

### Second priority

- real candlestick chart
- feed diagnostics rendering
- latest signal / decisions

### Third priority

- trades pages
- history tables
- richer chart overlays

## Quick local verification workflow

Start backend:

```bash
uvicorn api_server:app --reload
```

Check health:

```bash
curl http://127.0.0.1:8000/api/health
```

Check summary:

```bash
curl "http://127.0.0.1:8000/api/dashboard/summary?refresh_live=true"
```

Check candles:

```bash
curl "http://127.0.0.1:8000/api/market/candles?pair=EUR_USD&granularity=M15&count=50"
```

Check scheduler:

```bash
curl "http://127.0.0.1:8000/api/status/scheduler?refresh=true"
```

## Final integration rules

- backend is the source of truth
- do not duplicate trading logic in React
- treat every API field as potentially unavailable unless validated
- use dedicated scheduler polling
- use a real public backend URL for hosted preview
- use financial chart plugin for candle mode

If these rules are followed, the frontend will stay aligned with the current Python backend without breaking live analysis, scheduler display, or chart mode behavior.
