from __future__ import annotations

import csv
import json
import logging
import os
import threading
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel

from config import LOGS_DIR, TRADING_CONFIG
from main import get_demo_loop_schedule_state, get_next_entry_window_start_ny, write_signal_log
from oanda_connector import MarketDataBuilder, OANDAClient
from signal_log_utils import build_signal_log_metadata, infer_recorded_at


DEFAULT_CORS_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://style-whisperer-87.lovable.app",
]
SIGNAL_STALE_AFTER_SECONDS = int(os.getenv("SIGNAL_STALE_AFTER_SECONDS", "3600"))
LIVE_SNAPSHOT_BACKGROUND_REFRESH_SECONDS = int(
    os.getenv("LIVE_SNAPSHOT_BACKGROUND_REFRESH_SECONDS", "30")
)

logger = logging.getLogger(__name__)
_snapshot_cache_lock = threading.Lock()
_snapshot_refresh_lock = threading.Lock()
_snapshot_stop_event = threading.Event()
_snapshot_background_thread: threading.Thread | None = None
_snapshot_cache_data: dict[str, Any] | None = None


class HealthResponse(BaseModel):
    status: str
    service: str
    environment: str
    utc_time: str
    public_api_base_url: str | None
    oanda_configured: bool
    anthropic_configured: bool
    allowed_origins: list[str]
    log_files: dict[str, int]


class SchedulerStatusResponse(BaseModel):
    utc_time: str
    new_york_time: str
    weekday: str
    session: str
    analysis_allowed_now: bool
    schedule_reason: str
    next_poll_seconds: int
    next_entry_window_start_ny: str | None
    trade_window_active: bool
    runtime_mode: Literal["ENTRY_ANALYSIS", "MONITOR_ONLY", "MONITOR_OPEN_TRADES", "WEEKEND_BLOCK"]
    trade_management_active: bool
    open_trades_count: int


class LogEnvelope(BaseModel):
    filename: str
    modified_at: str
    recorded_at: str | None
    age_seconds: int | None
    is_stale: bool
    status: Literal["OK", "FAILED", "STALE", "STALE_FAILED"]
    data: dict[str, Any]


def _split_csv_env(name: str) -> list[str]:
    value = os.getenv(name, "")
    return [item.strip() for item in value.split(",") if item.strip()]


def _cors_origins() -> list[str]:
    configured = _split_csv_env("FRONTEND_ORIGINS")
    origins: list[str] = []
    for origin in DEFAULT_CORS_ORIGINS + configured:
        if origin not in origins:
            origins.append(origin)
    return origins


def _public_api_base_url() -> str | None:
    candidates = [
        os.getenv("PUBLIC_API_BASE_URL", "").strip(),
        os.getenv("RENDER_EXTERNAL_URL", "").strip(),
    ]
    external_host = os.getenv("RENDER_EXTERNAL_HOSTNAME", "").strip()
    if external_host:
        candidates.append(f"https://{external_host}")

    for candidate in candidates:
        if candidate:
            return candidate.rstrip("/")
    return None


def _trusted_hosts() -> list[str]:
    configured = _split_csv_env("API_TRUSTED_HOSTS")
    if configured:
        return configured

    hosts = {"localhost", "127.0.0.1"}
    public_base_url = _public_api_base_url()
    if public_base_url:
        parsed = urlparse(public_base_url)
        if parsed.hostname:
            hosts.add(parsed.hostname)
    return sorted(hosts)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _load_snapshot_from_disk_into_cache()
    _start_snapshot_background_refresh()
    try:
        yield
    finally:
        _stop_snapshot_background_refresh()


app = FastAPI(
    title="Forex Trading System API",
    version="0.1.0",
    description="Read-only REST API for the first frontend version.",
    root_path=os.getenv("API_ROOT_PATH", "").strip(),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=_trusted_hosts(),
)


def _utc_now() -> datetime:
    return datetime.now(tz=ZoneInfo("UTC"))


def _ny_now() -> datetime:
    return datetime.now(tz=ZoneInfo("America/New_York"))


def _read_json(path: Path) -> dict[str, Any]:
    with open(path) as f:
        return json.load(f)


def _latest_file(pattern: str) -> Path | None:
    matches = sorted(LOGS_DIR.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def _latest_signal_file(kind: Literal["signal", "test_signal"]) -> Path | None:
    matches = list(LOGS_DIR.glob(f"{kind}_*.json"))
    if not matches:
        return None

    def _sort_key(path: Path) -> tuple[float, float]:
        data = _read_json(path)
        modified_at = datetime.fromtimestamp(path.stat().st_mtime, tz=ZoneInfo("UTC"))
        recorded_at = infer_recorded_at(path, data, modified_at=modified_at) or modified_at
        return (recorded_at.timestamp(), modified_at.timestamp())

    return max(matches, key=_sort_key)


def _latest_snapshot_file() -> Path | None:
    return _latest_file("live_data_check_*.json")


def _load_jsonl_tail(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    rows: list[dict[str, Any]] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows[-limit:]


def _load_csv_tail(path: Path, limit: int) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    with open(path) as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    return rows[-limit:]


@lru_cache(maxsize=1)
def _get_oanda_builder() -> MarketDataBuilder:
    api_key = os.getenv("OANDA_API_KEY", "").strip()
    account_id = os.getenv("OANDA_ACCOUNT_ID", "").strip()
    if not api_key or not account_id:
        raise RuntimeError("Missing OANDA_API_KEY or OANDA_ACCOUNT_ID")

    client = OANDAClient(api_key, account_id, practice=TRADING_CONFIG["demo_mode"])
    return MarketDataBuilder(client)


def _build_live_snapshot(*, persist: bool = False) -> dict[str, Any]:
    try:
        builder = _get_oanda_builder()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"OANDA unavailable: {exc}") from exc

    try:
        snapshot = builder.build_market_data("EUR_USD")
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Live snapshot build failed: {exc}") from exc

    if persist:
        output_file = LOGS_DIR / f"live_data_check_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
        output_file.parent.mkdir(exist_ok=True)
        with open(output_file, "w") as f:
            json.dump(snapshot, f, indent=2)

    return snapshot


def _cache_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    global _snapshot_cache_data
    with _snapshot_cache_lock:
        _snapshot_cache_data = snapshot
    return snapshot


def _cached_snapshot() -> dict[str, Any] | None:
    with _snapshot_cache_lock:
        if _snapshot_cache_data is None:
            return None
        return dict(_snapshot_cache_data)


def _load_snapshot_from_disk_into_cache() -> dict[str, Any] | None:
    latest = _latest_snapshot_file()
    if latest is None:
        return None
    snapshot = _read_json(latest)
    return _cache_snapshot(snapshot)


def _refresh_snapshot_cache(*, persist: bool = False) -> dict[str, Any] | None:
    if not _snapshot_refresh_lock.acquire(blocking=False):
        return None

    try:
        snapshot = _build_live_snapshot(persist=persist)
    except Exception as exc:
        logger.warning(f"Background live snapshot refresh failed: {exc}")
        return None
    else:
        return _cache_snapshot(snapshot)
    finally:
        _snapshot_refresh_lock.release()


def _start_snapshot_refresh_async(*, persist: bool = False) -> None:
    if _snapshot_refresh_lock.locked():
        return

    thread = threading.Thread(
        target=_refresh_snapshot_cache,
        kwargs={"persist": persist},
        daemon=True,
        name="live-snapshot-refresh",
    )
    thread.start()


def _snapshot_refresh_loop() -> None:
    while not _snapshot_stop_event.is_set():
        _refresh_snapshot_cache(persist=False)
        _snapshot_stop_event.wait(LIVE_SNAPSHOT_BACKGROUND_REFRESH_SECONDS)


def _start_snapshot_background_refresh() -> None:
    global _snapshot_background_thread
    if not os.getenv("OANDA_API_KEY", "").strip() or not os.getenv("OANDA_ACCOUNT_ID", "").strip():
        logger.info("Skipping background live snapshot refresh: OANDA credentials not configured")
        return

    if _snapshot_background_thread and _snapshot_background_thread.is_alive():
        return

    _snapshot_stop_event.clear()
    _snapshot_background_thread = threading.Thread(
        target=_snapshot_refresh_loop,
        daemon=True,
        name="live-snapshot-background",
    )
    _snapshot_background_thread.start()


def _stop_snapshot_background_refresh() -> None:
    global _snapshot_background_thread
    _snapshot_stop_event.set()
    if _snapshot_background_thread and _snapshot_background_thread.is_alive():
        _snapshot_background_thread.join(timeout=1)
    _snapshot_background_thread = None


def _snapshot_warming_http_error() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail=(
            "Live snapshot warming up in background. "
            "Retry shortly; no cached snapshot is available yet."
        ),
    )


def _get_live_snapshot(*, refresh: bool, persist: bool = False) -> dict[str, Any]:
    cached = _cached_snapshot()

    if refresh:
        if cached is not None:
            _start_snapshot_refresh_async(persist=persist)
            return cached

        persisted = _load_snapshot_from_disk_into_cache()
        if persisted is not None:
            _start_snapshot_refresh_async(persist=persist)
            return persisted

        _start_snapshot_refresh_async(persist=persist)
        raise _snapshot_warming_http_error()

    if cached is not None:
        return cached

    persisted = _load_snapshot_from_disk_into_cache()
    if persisted is not None:
        return persisted

    _start_snapshot_refresh_async(persist=persist)
    raise _snapshot_warming_http_error()


def _serialize_candles(df: Any) -> list[dict[str, Any]]:
    candles: list[dict[str, Any]] = []
    for row in df.reset_index().itertuples(index=False):
        candles.append({
            "time": row.time.isoformat(),
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
            "volume": int(row.volume),
        })
    return candles


def _feed_diagnostics(snapshot: dict[str, Any]) -> dict[str, Any]:
    fund = snapshot.get("fundamental", {})
    price = snapshot.get("price")

    def _available(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str) and value.startswith("MANUAL_CHECK"):
            return False
        if value == "":
            return False
        return True

    return {
        "oanda_market_data": {
            "available": price is not None,
            "value": price,
        },
        "rates": {
            "available": fund.get("usd_rate") is not None and fund.get("ecb_deposit_rate") is not None,
            "value": fund.get("rate_differential"),
            "source": fund.get("rates_source"),
        },
        "dxy": {
            "available": _available(fund.get("dxy_direction")),
            "value": fund.get("dxy_direction"),
        },
        "cot": {
            "available": _available(fund.get("cot_bias")),
            "value": fund.get("cot_bias"),
        },
        "calendar": {
            "available": _available(fund.get("next_news_event")),
            "value": fund.get("next_news_event"),
            "time_to_event": fund.get("time_to_event"),
        },
        "headlines": {
            "available": _available(fund.get("recent_headline")),
            "value": fund.get("recent_headline"),
        },
        "retail_sentiment": {
            "available": _available(fund.get("retail_sentiment")),
            "value": fund.get("retail_sentiment"),
        },
        "risk_sentiment": {
            "available": _available(fund.get("risk_sentiment")),
            "value": fund.get("risk_sentiment"),
        },
    }

def _scheduler_status(snapshot: dict[str, Any]) -> SchedulerStatusResponse:
    now_utc = _utc_now()
    now_ny = _ny_now()
    schedule = get_demo_loop_schedule_state(snapshot, now_ny=now_ny)

    return SchedulerStatusResponse(
        utc_time=now_utc.isoformat(),
        new_york_time=now_ny.isoformat(),
        weekday=now_ny.strftime("%A"),
        session=schedule["session"],
        analysis_allowed_now=schedule["analysis_allowed_now"],
        schedule_reason=schedule["schedule_reason"],
        next_poll_seconds=schedule["next_poll_seconds"],
        next_entry_window_start_ny=get_next_entry_window_start_ny(now_ny, schedule["analysis_allowed_now"]),
        trade_window_active=schedule["trade_window_active"],
        runtime_mode=schedule["runtime_mode"],
        trade_management_active=schedule["trade_management_active"],
        open_trades_count=schedule["open_trades_count"],
    )


def _log_envelope(path: Path, *, now_utc: datetime | None = None) -> LogEnvelope:
    if now_utc is None:
        now_utc = _utc_now()

    data = _read_json(path)
    metadata = build_signal_log_metadata(
        path,
        data,
        now_utc=now_utc,
        stale_after_seconds=SIGNAL_STALE_AFTER_SECONDS,
    )
    return LogEnvelope(
        filename=path.name,
        modified_at=metadata["modified_at"],
        recorded_at=metadata["recorded_at"],
        age_seconds=metadata["age_seconds"],
        is_stale=metadata["is_stale"],
        status=metadata["status"],
        data=data,
    )


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="forex-trading-system-api",
        environment=os.getenv("APP_ENV", "development"),
        utc_time=_utc_now().isoformat(),
        public_api_base_url=_public_api_base_url(),
        oanda_configured=bool(os.getenv("OANDA_API_KEY")) and bool(os.getenv("OANDA_ACCOUNT_ID")),
        anthropic_configured=bool(os.getenv("ANTHROPIC_API_KEY")),
        allowed_origins=_cors_origins(),
        log_files={
            "signals": len(list(LOGS_DIR.glob("signal_*.json"))),
            "test_signals": len(list(LOGS_DIR.glob("test_signal_*.json"))),
            "live_snapshots": len(list(LOGS_DIR.glob("live_data_check_*.json"))),
        },
    )


@app.get("/api/live/snapshot")
def live_snapshot(
    refresh: bool = Query(True, description="Trigger a background refresh and return the latest available snapshot"),
    persist: bool = Query(False, description="Persist the next refreshed snapshot into logs/live_data_check_*.json"),
) -> dict[str, Any]:
    return _get_live_snapshot(refresh=refresh, persist=persist)


@app.get("/api/market/candles")
def market_candles(
    pair: Literal["EUR_USD"] = Query("EUR_USD"),
    granularity: Literal["M1", "M5", "M15", "M30", "H1", "H4", "D", "W"] = Query("M15"),
    count: int = Query(200, ge=10, le=1000),
) -> dict[str, Any]:
    try:
        builder = _get_oanda_builder()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"OANDA unavailable: {exc}") from exc

    try:
        df = builder.client.get_candles(pair, granularity, count=count)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Candle fetch failed: {exc}") from exc

    return {
        "pair": pair,
        "granularity": granularity,
        "count": len(df),
        "candles": _serialize_candles(df),
    }


@app.get("/api/status/scheduler", response_model=SchedulerStatusResponse)
def scheduler_status(
    refresh: bool = Query(True, description="Trigger a background snapshot refresh before computing scheduler state"),
) -> SchedulerStatusResponse:
    snapshot = _get_live_snapshot(refresh=refresh, persist=False)
    return _scheduler_status(snapshot)


@app.get("/api/diagnostics/feeds")
def feed_diagnostics(
    refresh: bool = Query(False, description="Trigger a background snapshot refresh before feed diagnostics"),
) -> dict[str, Any]:
    snapshot = _get_live_snapshot(refresh=refresh, persist=False)
    return {
        "utc_time": _utc_now().isoformat(),
        "diagnostics": _feed_diagnostics(snapshot),
    }


@app.get("/api/signals/latest", response_model=LogEnvelope)
def latest_signal(
    kind: Literal["signal", "test_signal"] = Query("signal"),
) -> LogEnvelope:
    latest = _latest_signal_file(kind)
    if latest is None:
        raise HTTPException(status_code=404, detail=f"No {kind} logs found")
    return _log_envelope(latest, now_utc=_utc_now())


@app.get("/api/trades/open")
def open_trades() -> dict[str, Any]:
    path = LOGS_DIR / "open_trades.json"
    if not path.exists():
        return {"count": 0, "items": []}

    data = _read_json(path)
    return {
        "count": len(data),
        "items": list(data.values()),
    }


@app.get("/api/trades/closed")
def closed_trades(limit: int = Query(20, ge=1, le=200)) -> dict[str, Any]:
    path = LOGS_DIR / "closed_trades.jsonl"
    items = _load_jsonl_tail(path, limit=limit)
    return {
        "count": len(items),
        "items": items,
    }


@app.get("/api/trades/history")
def trade_history(limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    path = LOGS_DIR / "trades.csv"
    items = _load_csv_tail(path, limit=limit)
    return {
        "count": len(items),
        "items": items,
    }


@app.get("/api/decisions/latest")
def latest_decisions(limit: int = Query(20, ge=1, le=200)) -> dict[str, Any]:
    path = LOGS_DIR / "agent_decisions.jsonl"
    items = _load_jsonl_tail(path, limit=limit)
    return {
        "count": len(items),
        "items": items,
    }


@app.get("/api/dashboard/summary")
def dashboard_summary(
    refresh_live: bool = Query(False, description="Trigger a background snapshot refresh before composing dashboard summary"),
) -> dict[str, Any]:
    now_utc = _utc_now()
    snapshot = _get_live_snapshot(refresh=refresh_live, persist=False)
    latest_signal_file = _latest_signal_file("signal")
    open_state = open_trades()

    return {
        "utc_time": now_utc.isoformat(),
        "scheduler": _scheduler_status(snapshot).model_dump(),
        "live_snapshot": snapshot,
        "feed_diagnostics": _feed_diagnostics(snapshot),
        "latest_signal": _log_envelope(latest_signal_file, now_utc=now_utc).model_dump() if latest_signal_file else None,
        "open_trades": open_state,
    }


@app.post("/api/signals/log-test-failure")
def log_test_failure(signal: dict[str, Any]) -> dict[str, str]:
    """
    Utility endpoint for the frontend/testing layer to persist a signal payload
    exactly the same way the runtime does. Keeps the file format consistent.
    """
    output = write_signal_log(signal, prefix="signal")
    return {"logged_to": str(output)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api_server:app",
        host=os.getenv("API_HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", os.getenv("API_PORT", "8000"))),
        proxy_headers=True,
        forwarded_allow_ips=os.getenv("FORWARDED_ALLOW_IPS", "*"),
    )
