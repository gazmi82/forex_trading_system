# Public API Deployment

This backend already exposes the endpoint set required by the frontend handoff:

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

The missing step was deployment to a real public HTTPS host. This repo now includes a `render.yaml` for that.

## Deploy On Render

1. Push this repository to GitHub.
2. In Render, create a new `Blueprint` deployment from the repo.
3. Render will read [`render.yaml`](/Users/gazmirsulcaj/forex_trading_system/render.yaml).
4. Set these required secret env vars in Render:
   - `PUBLIC_API_BASE_URL=https://<your-render-service>.onrender.com`
   - `API_TRUSTED_HOSTS=<your-render-service>.onrender.com`
   - `OANDA_API_KEY=...`
   - `OANDA_ACCOUNT_ID=...`
   - `ANTHROPIC_API_KEY=...` if runtime analysis endpoints depend on it
   - `FINNHUB_API_KEY=...` and `NEWS_API_KEY=...` if you want live headline providers
5. Deploy the service.

## Frontend CORS

The API now explicitly allows:

- `https://style-whisperer-87.lovable.app`
- any extra origins provided through `FRONTEND_ORIGINS`

For a second frontend hostname, add it to `FRONTEND_ORIGINS` as a comma-separated list.

Example:

```text
FRONTEND_ORIGINS=https://style-whisperer-87.lovable.app,https://your-prod-frontend.example.com
```

## Frontend Base URL

After the backend is live, set the frontend environment variable to the public HTTPS URL:

```text
VITE_API_BASE_URL=https://<your-render-service>.onrender.com
```

Do not use:

- `http://127.0.0.1:8000`
- `http://0.0.0.0:8000`
- a LAN IP such as `192.168.x.x`

## Verification

Run these after deployment:

```bash
curl https://<your-render-service>.onrender.com/api/health
curl "https://<your-render-service>.onrender.com/api/dashboard/summary?refresh_live=true"
curl "https://<your-render-service>.onrender.com/api/status/scheduler?refresh=true"
curl "https://<your-render-service>.onrender.com/api/market/candles?pair=EUR_USD&granularity=M15&count=50"
```

Expected result:

- HTTP `200`
- JSON response
- browser requests from `https://style-whisperer-87.lovable.app` are not blocked by CORS

## Public Deployment Notes

- The API server reads `PORT` automatically for hosted platforms.
- Trusted hosts are locked down through `API_TRUSTED_HOSTS`.
- `PUBLIC_API_BASE_URL` is exposed through `/api/health` so the deployed service can self-report its public URL.
- `GET /api/dashboard/summary?refresh_live=true` remains the main frontend aggregation endpoint.
