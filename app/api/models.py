from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


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


class CandleResponse(BaseModel):
    time: str
    open: float
    high: float
    low: float
    close: float
    volume: int


class MarketCandlesResponse(BaseModel):
    pair: str
    granularity: str
    count: int
    candles: list[CandleResponse]


class FeedDiagnosticItem(BaseModel):
    available: bool
    value: Any | None = None
    source: str | None = None
    time_to_event: str | None = None


class FeedDiagnosticsPayload(BaseModel):
    oanda_market_data: FeedDiagnosticItem
    rates: FeedDiagnosticItem
    dxy: FeedDiagnosticItem
    cot: FeedDiagnosticItem
    calendar: FeedDiagnosticItem
    headlines: FeedDiagnosticItem
    retail_sentiment: FeedDiagnosticItem
    risk_sentiment: FeedDiagnosticItem


class FeedDiagnosticsResponse(BaseModel):
    utc_time: str
    diagnostics: FeedDiagnosticsPayload


class ItemListResponse(BaseModel):
    count: int
    items: list[dict[str, Any]]


class DashboardSummaryResponse(BaseModel):
    utc_time: str
    scheduler: SchedulerStatusResponse
    live_snapshot: dict[str, Any]
    feed_diagnostics: FeedDiagnosticsPayload
    latest_signal: LogEnvelope | None
    open_trades: ItemListResponse


class LogWriteResponse(BaseModel):
    logged_to: str


class ContractRoute(BaseModel):
    name: str
    method: Literal["GET", "POST"]
    path: str
    description: str
    query_defaults: dict[str, Any] = Field(default_factory=dict)
    recommended_passive_query: dict[str, Any] | None = None
    manual_refresh_query: dict[str, Any] | None = None


class SnapshotContract(BaseModel):
    endpoint: str
    query_parameter: str
    query_defaults: dict[str, Any]
    recommended_passive_query: dict[str, Any]
    manual_refresh_query: dict[str, Any]
    warmup_status_code: int
    upstream_failure_status_code: int
    refresh_behavior: str


class DashboardContract(BaseModel):
    endpoint: str
    query_parameter: str
    query_defaults: dict[str, Any]
    recommended_passive_query: dict[str, Any]
    manual_refresh_query: dict[str, Any]
    latest_signal_semantics: str


class SignalContract(BaseModel):
    endpoint: str
    response_fields: list[str]
    status_values: list[Literal["OK", "FAILED", "STALE", "STALE_FAILED"]]
    preferred_timestamp_fields: list[str]
    failure_indicators: list[str]
    empty_state_behavior: str


class SchedulerContract(BaseModel):
    endpoint: str
    actionable_when: str
    blocked_reason_field: str
    blocked_runtime_modes: list[Literal["MONITOR_ONLY", "MONITOR_OPEN_TRADES", "WEEKEND_BLOCK"]]


class FrontendDiscovery(BaseModel):
    openapi_url: str
    contract_url: str
    primary_dashboard_endpoint: str


class FrontendContractResponse(BaseModel):
    generated_at_utc: str
    discovery: FrontendDiscovery
    routes: list[ContractRoute]
    snapshot: SnapshotContract
    dashboard: DashboardContract
    signals: SignalContract
    scheduler: SchedulerContract
