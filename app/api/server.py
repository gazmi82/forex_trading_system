from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.analysis.scheduler import get_demo_loop_schedule_state, get_next_entry_window_start_ny
from app.api.frontend_contract import build_frontend_contract
from app.api.live_snapshot_service import LiveSnapshotService
from app.api.log_queries import (
    latest_file as _log_latest_file,
    latest_signal_file as _log_latest_signal_file,
    latest_snapshot_file as _log_latest_snapshot_file,
    load_csv_tail as _log_load_csv_tail,
    load_jsonl_tail as _log_load_jsonl_tail,
    log_envelope as _build_log_envelope,
    read_json as _log_read_json,
)
from app.api.models import (
    DashboardSummaryResponse,
    FeedDiagnosticsPayload,
    FeedDiagnosticsResponse,
    FrontendContractResponse,
    HealthResponse,
    ItemListResponse,
    LogEnvelope,
    LogWriteResponse,
    MarketCandlesResponse,
    SchedulerStatusResponse,
)
from app.brokers.oanda import MarketDataBuilder
from app.core.config import LOGS_DIR, TRADING_CONFIG
from app.logs.signal_logs import write_signal_log

DEFAULT_CORS_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "https://style-whisperer-87.lovable.app",
]
SIGNAL_STALE_AFTER_SECONDS = int(os.getenv("SIGNAL_STALE_AFTER_SECONDS", "3600"))

logger = logging.getLogger(__name__)
_snapshot_service = LiveSnapshotService(logs_dir=LOGS_DIR, trading_config=TRADING_CONFIG)


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


# ---------------------------------------------------------------------------
# Compatibility wrappers around extracted modules/services
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict[str, Any]:
    return _log_read_json(path)


def _latest_file(pattern: str) -> Path | None:
    return _log_latest_file(pattern, logs_dir=LOGS_DIR)


def _latest_signal_file(kind: Literal["signal", "test_signal"]) -> Path | None:
    return _log_latest_signal_file(kind, logs_dir=LOGS_DIR)


def _latest_snapshot_file() -> Path | None:
    return _log_latest_snapshot_file(logs_dir=LOGS_DIR)


def _load_jsonl_tail(path: Path, limit: int) -> list[dict[str, Any]]:
    return _log_load_jsonl_tail(path, limit)


def _load_csv_tail(path: Path, limit: int) -> list[dict[str, Any]]:
    return _log_load_csv_tail(path, limit)


def _get_oanda_builder() -> MarketDataBuilder:
    return _snapshot_service.get_oanda_builder()


def _build_live_snapshot(*, persist: bool = False) -> dict[str, Any]:
    return _snapshot_service.build_live_snapshot(persist=persist)


def _cache_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    return _snapshot_service.cache_snapshot(snapshot)


def _cached_snapshot() -> dict[str, Any] | None:
    return _snapshot_service.cached_snapshot()


def _load_snapshot_from_disk_into_cache() -> dict[str, Any] | None:
    return _snapshot_service.load_snapshot_from_disk_into_cache()


def _refresh_snapshot_cache(*, persist: bool = False) -> dict[str, Any] | None:
    return _snapshot_service.refresh_snapshot_cache(persist=persist)


def _start_snapshot_refresh_async(*, persist: bool = False) -> None:
    _snapshot_service.start_snapshot_refresh_async(persist=persist)


def _start_snapshot_background_refresh() -> None:
    _snapshot_service.start_background_refresh()


def _stop_snapshot_background_refresh() -> None:
    _snapshot_service.stop_background_refresh()


def _snapshot_warming_http_error() -> HTTPException:
    return _snapshot_service.snapshot_warming_http_error()


def _get_live_snapshot(*, refresh: bool, persist: bool = False) -> dict[str, Any]:
    return _snapshot_service.get_live_snapshot(refresh=refresh, persist=persist)


def _log_envelope(path: Path, *, now_utc: datetime | None = None) -> LogEnvelope:
    if now_utc is None:
        now_utc = _utc_now()
    return _build_log_envelope(
        path,
        now_utc=now_utc,
        stale_after_seconds=SIGNAL_STALE_AFTER_SECONDS,
    )


def _frontend_contract(*, now_utc: datetime | None = None) -> FrontendContractResponse:
    if now_utc is None:
        now_utc = _utc_now()
    return build_frontend_contract(
        now_utc=now_utc,
        openapi_url=app.openapi_url or "/openapi.json",
    )


def _serialize_candles(df: Any) -> list[dict[str, Any]]:
    candles: list[dict[str, Any]] = []
    for row in df.reset_index().itertuples(index=False):
        candles.append(
            {
                "time": row.time.isoformat(),
                "open": float(row.open),
                "high": float(row.high),
                "low": float(row.low),
                "close": float(row.close),
                "volume": int(row.volume),
            }
        )
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
        next_entry_window_start_ny=get_next_entry_window_start_ny(
            now_ny,
            schedule["analysis_allowed_now"],
        ),
        trade_window_active=schedule["trade_window_active"],
        runtime_mode=schedule["runtime_mode"],
        trade_management_active=schedule["trade_management_active"],
        open_trades_count=schedule["open_trades_count"],
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


@app.get("/api/market/candles", response_model=MarketCandlesResponse)
def market_candles(
    pair: Literal["EUR_USD"] = Query("EUR_USD"),
    granularity: Literal["M1", "M5", "M15", "M30", "H1", "H4", "D", "W"] = Query("M15"),
    count: int = Query(200, ge=10, le=1000),
) -> MarketCandlesResponse:
    try:
        builder = _get_oanda_builder()
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"OANDA unavailable: {exc}") from exc

    try:
        df = builder.client.get_candles(pair, granularity, count=count)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Candle fetch failed: {exc}") from exc

    return MarketCandlesResponse(
        pair=pair,
        granularity=granularity,
        count=len(df),
        candles=_serialize_candles(df),
    )


@app.get("/api/status/scheduler", response_model=SchedulerStatusResponse)
def scheduler_status(
    refresh: bool = Query(True, description="Trigger a background snapshot refresh before computing scheduler state"),
) -> SchedulerStatusResponse:
    snapshot = _get_live_snapshot(refresh=refresh, persist=False)
    return _scheduler_status(snapshot)


@app.get("/api/diagnostics/feeds", response_model=FeedDiagnosticsResponse)
def feed_diagnostics(
    refresh: bool = Query(False, description="Trigger a background snapshot refresh before feed diagnostics"),
) -> FeedDiagnosticsResponse:
    snapshot = _get_live_snapshot(refresh=refresh, persist=False)
    return FeedDiagnosticsResponse(
        utc_time=_utc_now().isoformat(),
        diagnostics=FeedDiagnosticsPayload.model_validate(_feed_diagnostics(snapshot)),
    )


@app.get("/api/signals/latest", response_model=LogEnvelope | None)
def latest_signal(
    kind: Literal["signal", "test_signal"] = Query("signal"),
) -> LogEnvelope | None:
    latest = _latest_signal_file(kind)
    if latest is None:
        return None
    return _log_envelope(latest, now_utc=_utc_now())


@app.get("/api/trades/open", response_model=ItemListResponse)
def open_trades() -> ItemListResponse:
    path = LOGS_DIR / "open_trades.json"
    if not path.exists():
        return ItemListResponse(count=0, items=[])

    data = _read_json(path)
    return ItemListResponse(count=len(data), items=list(data.values()))


@app.get("/api/trades/closed", response_model=ItemListResponse)
def closed_trades(limit: int = Query(20, ge=1, le=200)) -> ItemListResponse:
    path = LOGS_DIR / "closed_trades.jsonl"
    items = _load_jsonl_tail(path, limit=limit)
    return ItemListResponse(count=len(items), items=items)


@app.get("/api/trades/history", response_model=ItemListResponse)
def trade_history(limit: int = Query(50, ge=1, le=500)) -> ItemListResponse:
    path = LOGS_DIR / "trades.csv"
    items = _load_csv_tail(path, limit=limit)
    return ItemListResponse(count=len(items), items=items)


@app.get("/api/decisions/latest", response_model=ItemListResponse)
def latest_decisions(limit: int = Query(20, ge=1, le=200)) -> ItemListResponse:
    path = LOGS_DIR / "agent_decisions.jsonl"
    items = _load_jsonl_tail(path, limit=limit)
    return ItemListResponse(count=len(items), items=items)


@app.get("/api/dashboard/summary", response_model=DashboardSummaryResponse)
def dashboard_summary(
    refresh_live: bool = Query(False, description="Trigger a background snapshot refresh before composing dashboard summary"),
) -> DashboardSummaryResponse:
    now_utc = _utc_now()
    snapshot = _get_live_snapshot(refresh=refresh_live, persist=False)
    latest_signal_file = _latest_signal_file("signal")
    open_state = open_trades()
    scheduler = _scheduler_status(snapshot)
    feed_diagnostics_payload = FeedDiagnosticsPayload.model_validate(_feed_diagnostics(snapshot))

    return DashboardSummaryResponse(
        utc_time=now_utc.isoformat(),
        scheduler=scheduler,
        live_snapshot=snapshot,
        feed_diagnostics=feed_diagnostics_payload,
        latest_signal=_log_envelope(latest_signal_file, now_utc=now_utc) if latest_signal_file else None,
        open_trades=open_state,
    )


@app.get("/api/meta/frontend-contract", response_model=FrontendContractResponse)
def frontend_contract() -> FrontendContractResponse:
    return _frontend_contract(now_utc=_utc_now())


@app.post("/api/signals/log-test-failure", response_model=LogWriteResponse)
def log_test_failure(signal: dict[str, Any]) -> LogWriteResponse:
    """
    Utility endpoint for the frontend/testing layer to persist a signal payload
    exactly the same way the runtime does. Keeps the file format consistent.
    """
    output = write_signal_log(signal, prefix="signal")
    return LogWriteResponse(logged_to=str(output))
