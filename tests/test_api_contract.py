from __future__ import annotations

import os
import unittest
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
