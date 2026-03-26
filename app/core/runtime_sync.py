from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from app.core.runtime_logging import record_runtime_event


logger = logging.getLogger(__name__)


def sync_signal(payload: dict[str, Any], *, kind: str, log_dir: Path | None = None) -> bool:
    return _post_json(
        "/api/internal/runtime/signal",
        payload,
        query={"kind": kind},
        action="sync_signal",
        log_dir=log_dir,
    )


def sync_decision(payload: dict[str, Any], *, log_dir: Path | None = None) -> bool:
    return _post_json("/api/internal/runtime/decision", payload, action="sync_decision", log_dir=log_dir)


def sync_open_trades(trades: dict[str, Any], *, log_dir: Path | None = None) -> bool:
    return _post_json(
        "/api/internal/runtime/open-trades",
        {"items": trades},
        action="sync_open_trades",
        log_dir=log_dir,
    )


def sync_closed_trade(payload: dict[str, Any], *, log_dir: Path | None = None) -> bool:
    return _post_json(
        "/api/internal/runtime/closed-trade",
        payload,
        action="sync_closed_trade",
        log_dir=log_dir,
    )


def sync_trade_history(payload: dict[str, Any], *, log_dir: Path | None = None) -> bool:
    return _post_json(
        "/api/internal/runtime/trade-history",
        payload,
        action="sync_trade_history",
        log_dir=log_dir,
    )


def _post_json(
    path: str,
    payload: dict[str, Any],
    *,
    action: str,
    query: dict[str, Any] | None = None,
    log_dir: Path | None = None,
) -> bool:
    base_url = os.getenv("RUNTIME_SYNC_BASE_URL", "").strip().rstrip("/")
    if not base_url:
        return False

    url = f"{base_url}{path}"
    if query:
        url = f"{url}?{urlencode({key: value for key, value in query.items() if value is not None})}"

    headers = {"Content-Type": "application/json"}
    token = os.getenv("RUNTIME_SYNC_TOKEN", "").strip()
    if token:
        headers["X-Runtime-Sync-Token"] = token

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=10)
        response.raise_for_status()
        return True
    except Exception as exc:
        logger.warning("Runtime sync failed for %s: %s", action, exc)
        record_runtime_event(
            component="core.runtime_sync",
            action=action,
            level="WARNING",
            message="Runtime sync request failed",
            context={"url": url},
            exc=exc,
            log_dir=log_dir,
        )
        return False
