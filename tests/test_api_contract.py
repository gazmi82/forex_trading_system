from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

FASTAPI_IMPORT_ERROR: ModuleNotFoundError | None = None
FASTAPI_AVAILABLE = True

try:
    from fastapi.testclient import TestClient

    from app.api.server import _trusted_hosts, app
except ModuleNotFoundError as exc:
    FASTAPI_AVAILABLE = False
    FASTAPI_IMPORT_ERROR = exc


@unittest.skipUnless(FASTAPI_AVAILABLE, f"fastapi not installed: {FASTAPI_IMPORT_ERROR}")
class ApiContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app, base_url="http://localhost")

    def tearDown(self) -> None:
        self.client.close()

    def test_frontend_contract_exposes_machine_readable_semantics(self):
        response = self.client.get("/api/meta/frontend-contract")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["discovery"]["openapi_url"], "/openapi.json")
        self.assertEqual(body["discovery"]["contract_url"], "/api/meta/frontend-contract")
        self.assertEqual(body["snapshot"]["warmup_status_code"], 503)
        self.assertEqual(body["snapshot"]["upstream_failure_status_code"], 502)
        self.assertEqual(body["signals"]["status_values"], ["OK", "FAILED", "STALE", "STALE_FAILED"])
        self.assertIn("Return null", body["signals"]["empty_state_behavior"])
        self.assertEqual(body["signals"]["preferred_timestamp_fields"], ["recorded_at", "data.timestamp"])
        self.assertEqual(body["scheduler"]["actionable_when"], "analysis_allowed_now == true")

        route_names = {route["name"] for route in body["routes"]}
        self.assertIn("dashboard_summary", route_names)
        self.assertIn("latest_signal", route_names)
        self.assertIn("market_candles", route_names)

    def test_openapi_includes_frontend_contract_endpoint(self):
        response = self.client.get("/openapi.json")

        self.assertEqual(response.status_code, 200)
        paths = response.json()["paths"]
        self.assertIn("/api/meta/frontend-contract", paths)
        self.assertIn("/api/market/candles", paths)
        self.assertIn("/api/dashboard/summary", paths)

    def test_latest_signal_returns_null_when_logs_are_missing(self):
        with patch("app.api.server._latest_signal_file", return_value=None):
            response = self.client.get("/api/signals/latest?kind=test_signal")

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json())

    def test_trade_history_tolerates_legacy_rows_with_extra_columns(self):
        original_store_path = os.environ.get("RUNTIME_STORE_PATH")
        with tempfile.TemporaryDirectory() as tmpdir:
            log_dir = Path(tmpdir)
            os.environ["RUNTIME_STORE_PATH"] = str(log_dir / "runtime_store.sqlite3")
            trades_csv = log_dir / "trades.csv"
            trades_csv.write_text(
                "\n".join(
                    [
                        "timestamp,order_id,trade_id,instrument,direction,units,entry_price,stop_loss,tp1,tp2,status,pnl,notes",
                        "2026-03-14,EUR_USD,SELL,1.145,,1.148,1.139,10000,LOSS,-1.0,-100.0,2.5,NY Kill Zone,72,Manual test lesson",
                        "2026-03-19,order-2,trade-2,EUR_USD,BUY,1000,1.081,1.078,1.085,1.09,CLOSED,25.0,Normal row",
                    ]
                ),
                encoding="utf-8",
            )

            with patch("app.api.server.LOGS_DIR", log_dir):
                response = self.client.get("/api/trades/history")
        if original_store_path is None:
            os.environ.pop("RUNTIME_STORE_PATH", None)
        else:
            os.environ["RUNTIME_STORE_PATH"] = original_store_path

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 2)
        self.assertNotIn("null", body["items"][0])
        self.assertEqual(body["items"][0]["notes"], "NY Kill Zone")

    def test_internal_runtime_signal_ingest_populates_public_latest_signal(self):
        original_store_path = os.environ.get("RUNTIME_STORE_PATH")
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["RUNTIME_STORE_PATH"] = str(Path(tmpdir) / "runtime_store.sqlite3")
            response = self.client.post(
                "/api/internal/runtime/signal?kind=signal",
                json={
                    "timestamp": "2026-03-26T14:05:00Z",
                    "logged_at_utc": "2026-03-26T14:05:01Z",
                    "log_filename": "signal_20260326.json",
                    "log_entry_id": "20260326_140501_000001",
                    "signal": {"direction": "BUY", "confidence": 72},
                    "confluence_score": 78,
                    "validator_overrides": [],
                },
            )
            latest = self.client.get("/api/signals/latest?kind=signal")
        if original_store_path is None:
            os.environ.pop("RUNTIME_STORE_PATH", None)
        else:
            os.environ["RUNTIME_STORE_PATH"] = original_store_path

        self.assertEqual(response.status_code, 200)
        self.assertEqual(latest.status_code, 200)
        body = latest.json()
        self.assertEqual(body["filename"], "signal_20260326.json")
        self.assertEqual(body["data"]["signal"]["direction"], "BUY")
        self.assertEqual(body["data"]["confluence_score"], 78)

    def test_internal_runtime_trade_ingest_populates_public_closed_trades(self):
        original_store_path = os.environ.get("RUNTIME_STORE_PATH")
        with tempfile.TemporaryDirectory() as tmpdir:
            os.environ["RUNTIME_STORE_PATH"] = str(Path(tmpdir) / "runtime_store.sqlite3")
            response = self.client.post(
                "/api/internal/runtime/closed-trade",
                json={
                    "date": "2026-03-26",
                    "pair": "EUR_USD",
                    "direction": "BUY",
                    "outcome": "WIN",
                    "pnl_r": 1.8,
                    "pnl_usd": 180.0,
                    "session": "NY Kill Zone",
                    "confluence_score": 81,
                },
            )
            closed = self.client.get("/api/trades/closed?limit=20")
        if original_store_path is None:
            os.environ.pop("RUNTIME_STORE_PATH", None)
        else:
            os.environ["RUNTIME_STORE_PATH"] = original_store_path

        self.assertEqual(response.status_code, 200)
        self.assertEqual(closed.status_code, 200)
        body = closed.json()
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["items"][0]["outcome"], "WIN")
        self.assertEqual(body["items"][0]["pair"], "EUR_USD")

    def test_trusted_hosts_include_render_wildcard_for_onrender_deploys(self):
        original_public_api_base_url = os.environ.get("PUBLIC_API_BASE_URL")
        original_render_external_hostname = os.environ.get("RENDER_EXTERNAL_HOSTNAME")
        try:
            os.environ["PUBLIC_API_BASE_URL"] = "https://forex-trading-system-api.onrender.com"
            os.environ["RENDER_EXTERNAL_HOSTNAME"] = "srv-d6s14en5gffc738avmlg.onrender.com"
            hosts = _trusted_hosts()
        finally:
            if original_public_api_base_url is None:
                os.environ.pop("PUBLIC_API_BASE_URL", None)
            else:
                os.environ["PUBLIC_API_BASE_URL"] = original_public_api_base_url
            if original_render_external_hostname is None:
                os.environ.pop("RENDER_EXTERNAL_HOSTNAME", None)
            else:
                os.environ["RENDER_EXTERNAL_HOSTNAME"] = original_render_external_hostname

        self.assertIn("*.onrender.com", hosts)
        self.assertIn("forex-trading-system-api.onrender.com", hosts)
        self.assertIn("srv-d6s14en5gffc738avmlg.onrender.com", hosts)


if __name__ == "__main__":
    unittest.main()
