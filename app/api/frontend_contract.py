from __future__ import annotations

from datetime import datetime

from app.api.models import (
    ContractRoute,
    DashboardContract,
    FrontendContractResponse,
    FrontendDiscovery,
    SchedulerContract,
    SignalContract,
    SnapshotContract,
)


def build_frontend_contract(*, now_utc: datetime, openapi_url: str) -> FrontendContractResponse:
    contract_url = "/api/meta/frontend-contract"

    return FrontendContractResponse(
        generated_at_utc=now_utc.isoformat(),
        discovery=FrontendDiscovery(
            openapi_url=openapi_url,
            contract_url=contract_url,
            primary_dashboard_endpoint="/api/dashboard/summary",
        ),
        routes=[
            ContractRoute(
                name="health",
                method="GET",
                path="/api/health",
                description="Service health and deployment metadata.",
            ),
            ContractRoute(
                name="dashboard_summary",
                method="GET",
                path="/api/dashboard/summary",
                description="Primary dashboard aggregate for scheduler, snapshot, diagnostics, latest signal, and open trades.",
                query_defaults={"refresh_live": False},
                recommended_passive_query={"refresh_live": False},
                manual_refresh_query={"refresh_live": True},
            ),
            ContractRoute(
                name="live_snapshot",
                method="GET",
                path="/api/live/snapshot",
                description="Cached live market snapshot with optional background refresh trigger.",
                query_defaults={"refresh": True, "persist": False},
                recommended_passive_query={"refresh": False, "persist": False},
                manual_refresh_query={"refresh": True, "persist": False},
            ),
            ContractRoute(
                name="market_candles",
                method="GET",
                path="/api/market/candles",
                description="Frontend-ready OHLCV candles for chart bootstrapping.",
                query_defaults={"pair": "EUR_USD", "granularity": "M15", "count": 200},
            ),
            ContractRoute(
                name="scheduler_status",
                method="GET",
                path="/api/status/scheduler",
                description="Current scheduler gate and next polling cadence.",
                query_defaults={"refresh": True},
                recommended_passive_query={"refresh": False},
                manual_refresh_query={"refresh": True},
            ),
            ContractRoute(
                name="feed_diagnostics",
                method="GET",
                path="/api/diagnostics/feeds",
                description="Normalized per-feed availability and latest values.",
                query_defaults={"refresh": False},
            ),
            ContractRoute(
                name="latest_signal",
                method="GET",
                path="/api/signals/latest",
                description="Latest saved signal envelope with freshness and failure metadata.",
                query_defaults={"kind": "signal"},
            ),
            ContractRoute(
                name="open_trades",
                method="GET",
                path="/api/trades/open",
                description="Current open trades tracked by the execution layer.",
            ),
            ContractRoute(
                name="closed_trades",
                method="GET",
                path="/api/trades/closed",
                description="Recent closed trades from the execution layer JSONL log.",
                query_defaults={"limit": 20},
            ),
            ContractRoute(
                name="trade_history",
                method="GET",
                path="/api/trades/history",
                description="Recent trade history rows from the CSV trade log.",
                query_defaults={"limit": 50},
            ),
            ContractRoute(
                name="latest_decisions",
                method="GET",
                path="/api/decisions/latest",
                description="Recent agent decision log entries.",
                query_defaults={"limit": 20},
            ),
            ContractRoute(
                name="log_test_failure",
                method="POST",
                path="/api/signals/log-test-failure",
                description="Persist a provided signal payload using the runtime log format.",
            ),
        ],
        snapshot=SnapshotContract(
            endpoint="/api/live/snapshot",
            query_parameter="refresh",
            query_defaults={"refresh": True, "persist": False},
            recommended_passive_query={"refresh": False, "persist": False},
            manual_refresh_query={"refresh": True, "persist": False},
            warmup_status_code=503,
            upstream_failure_status_code=502,
            refresh_behavior=(
                "When refresh=true, return the latest cached snapshot immediately when available "
                "and trigger a background refresh. When no cached snapshot exists yet, return 503."
            ),
        ),
        dashboard=DashboardContract(
            endpoint="/api/dashboard/summary",
            query_parameter="refresh_live",
            query_defaults={"refresh_live": False},
            recommended_passive_query={"refresh_live": False},
            manual_refresh_query={"refresh_live": True},
            latest_signal_semantics="latest_signal is informational when scheduler.analysis_allowed_now is false.",
        ),
        signals=SignalContract(
            endpoint="/api/signals/latest",
            response_fields=["filename", "modified_at", "recorded_at", "age_seconds", "is_stale", "status", "data"],
            status_values=["OK", "FAILED", "STALE", "STALE_FAILED"],
            preferred_timestamp_fields=["recorded_at", "data.timestamp"],
            failure_indicators=["data.error", "data.validator_overrides"],
            empty_state_behavior="Return null with HTTP 200 when no matching signal logs exist yet.",
        ),
        scheduler=SchedulerContract(
            endpoint="/api/status/scheduler",
            actionable_when="analysis_allowed_now == true",
            blocked_reason_field="schedule_reason",
            blocked_runtime_modes=["MONITOR_ONLY", "MONITOR_OPEN_TRADES", "WEEKEND_BLOCK"],
        ),
    )
