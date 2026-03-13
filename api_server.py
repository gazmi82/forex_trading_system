from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from config import LOGS_DIR, TRADING_CONFIG
from main import get_demo_loop_schedule, write_signal_log
from oanda_connector import MarketDataBuilder, OANDAClient


DEFAULT_CORS_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]


class HealthResponse(BaseModel):
    status: str
    service: str
    utc_time: str
    oanda_configured: bool
    anthropic_configured: bool
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


class LogEnvelope(BaseModel):
    filename: str
    modified_at: str
    data: dict[str, Any]


def _cors_origins() -> list[str]:
    extra_origins = os.getenv("FRONTEND_ORIGINS", "")
    configured = [origin.strip() for origin in extra_origins.split(",") if origin.strip()]
    return DEFAULT_CORS_ORIGINS + configured


app = FastAPI(
    title="Forex Trading System API",
    version="0.1.0",
    description="Read-only REST API for the first frontend version.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_origin_regex=r"https://.*\.lovable\.app",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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


def _get_live_snapshot(*, refresh: bool, persist: bool = False) -> dict[str, Any]:
    if refresh:
        return _build_live_snapshot(persist=persist)

    latest = _latest_file("live_data_check_*.json")
    if latest is not None:
        return _read_json(latest)

    return _build_live_snapshot(persist=persist)


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


def _next_entry_window_start_ny(now_ny: datetime, analysis_allowed_now: bool) -> str | None:
    if analysis_allowed_now:
        return None

    candidate = now_ny.replace(second=0, microsecond=0)
    for day_offset in range(0, 8):
        day = candidate + timedelta(days=day_offset)
        if day.weekday() >= 5:
            continue
        for hour in (3, 8):
            slot = day.replace(hour=hour, minute=0)
            if slot > now_ny:
                return slot.isoformat()
    return None


def _scheduler_status(snapshot: dict[str, Any]) -> SchedulerStatusResponse:
    run_entry_analysis, session, sleep_seconds, schedule_reason = get_demo_loop_schedule(snapshot)
    now_utc = _utc_now()
    now_ny = _ny_now()
    fund = snapshot.get("fundamental", {})

    return SchedulerStatusResponse(
        utc_time=now_utc.isoformat(),
        new_york_time=now_ny.isoformat(),
        weekday=now_ny.strftime("%A"),
        session=session,
        analysis_allowed_now=run_entry_analysis,
        schedule_reason=schedule_reason,
        next_poll_seconds=sleep_seconds,
        next_entry_window_start_ny=_next_entry_window_start_ny(now_ny, run_entry_analysis),
        trade_window_active=bool(fund.get("trade_window_active")),
    )


def _log_envelope(path: Path) -> LogEnvelope:
    return LogEnvelope(
        filename=path.name,
        modified_at=datetime.fromtimestamp(path.stat().st_mtime, tz=ZoneInfo("UTC")).isoformat(),
        data=_read_json(path),
    )


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        service="forex-trading-system-api",
        utc_time=_utc_now().isoformat(),
        oanda_configured=bool(os.getenv("OANDA_API_KEY")) and bool(os.getenv("OANDA_ACCOUNT_ID")),
        anthropic_configured=bool(os.getenv("ANTHROPIC_API_KEY")),
        log_files={
            "signals": len(list(LOGS_DIR.glob("signal_*.json"))),
            "test_signals": len(list(LOGS_DIR.glob("test_signal_*.json"))),
            "live_snapshots": len(list(LOGS_DIR.glob("live_data_check_*.json"))),
        },
    )


@app.get("/api/live/snapshot")
def live_snapshot(
    refresh: bool = Query(True, description="Build a fresh live snapshot from OANDA and live feeds"),
    persist: bool = Query(False, description="Persist the refreshed snapshot into logs/live_data_check_*.json"),
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
    refresh: bool = Query(True, description="Refresh live snapshot before computing scheduler state"),
) -> SchedulerStatusResponse:
    snapshot = _get_live_snapshot(refresh=refresh, persist=False)
    return _scheduler_status(snapshot)


@app.get("/api/diagnostics/feeds")
def feed_diagnostics(
    refresh: bool = Query(False, description="Refresh live snapshot before feed diagnostics"),
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
    latest = _latest_file(f"{kind}_*.json")
    if latest is None:
        raise HTTPException(status_code=404, detail=f"No {kind} logs found")
    return _log_envelope(latest)


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
    refresh_live: bool = Query(False, description="Refresh live snapshot before composing dashboard summary"),
) -> dict[str, Any]:
    snapshot = _get_live_snapshot(refresh=refresh_live, persist=False)
    latest_signal_file = _latest_file("signal_*.json")
    open_state = open_trades()

    return {
        "utc_time": _utc_now().isoformat(),
        "scheduler": _scheduler_status(snapshot).model_dump(),
        "live_snapshot": snapshot,
        "feed_diagnostics": _feed_diagnostics(snapshot),
        "latest_signal": _log_envelope(latest_signal_file).model_dump() if latest_signal_file else None,
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
