from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.api.log_queries import latest_snapshot_file, read_json
from app.brokers.oanda import MarketDataBuilder, OANDAClient

logger = logging.getLogger(__name__)


class LiveSnapshotService:
    def __init__(self, *, logs_dir: Path, trading_config: dict):
        self.logs_dir = Path(logs_dir)
        self.trading_config = trading_config
        self.background_refresh_seconds = int(
            os.getenv("LIVE_SNAPSHOT_BACKGROUND_REFRESH_SECONDS", "30")
        )

        self._snapshot_cache_lock = threading.Lock()
        self._snapshot_refresh_lock = threading.Lock()
        self._snapshot_stop_event = threading.Event()
        self._snapshot_background_thread: threading.Thread | None = None
        self._snapshot_cache_data: dict[str, Any] | None = None

    @lru_cache(maxsize=1)
    def get_oanda_builder(self) -> MarketDataBuilder:
        api_key = os.getenv("OANDA_API_KEY", "").strip()
        account_id = os.getenv("OANDA_ACCOUNT_ID", "").strip()
        if not api_key or not account_id:
            raise RuntimeError("Missing OANDA_API_KEY or OANDA_ACCOUNT_ID")

        client = OANDAClient(api_key, account_id, practice=self.trading_config["demo_mode"])
        return MarketDataBuilder(client)

    def build_live_snapshot(self, *, persist: bool = False) -> dict[str, Any]:
        try:
            builder = self.get_oanda_builder()
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"OANDA unavailable: {exc}") from exc

        try:
            snapshot = builder.build_market_data("EUR_USD")
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Live snapshot build failed: {exc}") from exc

        if persist:
            output_file = self.logs_dir / f"live_data_check_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
            output_file.parent.mkdir(exist_ok=True)
            with open(output_file, "w") as f:
                json.dump(snapshot, f, indent=2)

        return snapshot

    def cache_snapshot(self, snapshot: dict[str, Any]) -> dict[str, Any]:
        with self._snapshot_cache_lock:
            self._snapshot_cache_data = snapshot
        return snapshot

    def cached_snapshot(self) -> dict[str, Any] | None:
        with self._snapshot_cache_lock:
            if self._snapshot_cache_data is None:
                return None
            return dict(self._snapshot_cache_data)

    def load_snapshot_from_disk_into_cache(self) -> dict[str, Any] | None:
        latest = latest_snapshot_file(logs_dir=self.logs_dir)
        if latest is None:
            return None
        snapshot = read_json(latest)
        return self.cache_snapshot(snapshot)

    def refresh_snapshot_cache(self, *, persist: bool = False) -> dict[str, Any] | None:
        if not self._snapshot_refresh_lock.acquire(blocking=False):
            return None

        try:
            snapshot = self.build_live_snapshot(persist=persist)
        except Exception as exc:
            logger.warning(f"Background live snapshot refresh failed: {exc}")
            return None
        else:
            return self.cache_snapshot(snapshot)
        finally:
            self._snapshot_refresh_lock.release()

    def start_snapshot_refresh_async(self, *, persist: bool = False) -> None:
        if self._snapshot_refresh_lock.locked():
            return

        thread = threading.Thread(
            target=self.refresh_snapshot_cache,
            kwargs={"persist": persist},
            daemon=True,
            name="live-snapshot-refresh",
        )
        thread.start()

    def _snapshot_refresh_loop(self) -> None:
        while not self._snapshot_stop_event.is_set():
            self.refresh_snapshot_cache(persist=False)
            self._snapshot_stop_event.wait(self.background_refresh_seconds)

    def start_background_refresh(self) -> None:
        if not os.getenv("OANDA_API_KEY", "").strip() or not os.getenv("OANDA_ACCOUNT_ID", "").strip():
            logger.info("Skipping background live snapshot refresh: OANDA credentials not configured")
            return

        if self._snapshot_background_thread and self._snapshot_background_thread.is_alive():
            return

        self._snapshot_stop_event.clear()
        self._snapshot_background_thread = threading.Thread(
            target=self._snapshot_refresh_loop,
            daemon=True,
            name="live-snapshot-background",
        )
        self._snapshot_background_thread.start()

    def stop_background_refresh(self) -> None:
        self._snapshot_stop_event.set()
        if self._snapshot_background_thread and self._snapshot_background_thread.is_alive():
            self._snapshot_background_thread.join(timeout=1)
        self._snapshot_background_thread = None

    @staticmethod
    def snapshot_warming_http_error() -> HTTPException:
        return HTTPException(
            status_code=503,
            detail=(
                "Live snapshot warming up in background. "
                "Retry shortly; no cached snapshot is available yet."
            ),
        )

    def get_live_snapshot(self, *, refresh: bool, persist: bool = False) -> dict[str, Any]:
        cached = self.cached_snapshot()

        if refresh:
            if cached is not None:
                self.start_snapshot_refresh_async(persist=persist)
                return cached

            persisted = self.load_snapshot_from_disk_into_cache()
            if persisted is not None:
                self.start_snapshot_refresh_async(persist=persist)
                return persisted

            self.start_snapshot_refresh_async(persist=persist)
            raise self.snapshot_warming_http_error()

        if cached is not None:
            return cached

        persisted = self.load_snapshot_from_disk_into_cache()
        if persisted is not None:
            return persisted

        self.start_snapshot_refresh_async(persist=persist)
        raise self.snapshot_warming_http_error()
