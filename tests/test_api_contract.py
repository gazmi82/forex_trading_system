from __future__ import annotations

import unittest

FASTAPI_IMPORT_ERROR: ModuleNotFoundError | None = None
FASTAPI_AVAILABLE = True

try:
    from fastapi.testclient import TestClient

    from app.api.server import app
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


if __name__ == "__main__":
    unittest.main()
