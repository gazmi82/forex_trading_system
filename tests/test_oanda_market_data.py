from __future__ import annotations

import unittest
from unittest.mock import patch

from app.brokers.oanda import MarketDataBuilder, OANDAClient


class OandaMarketDataTests(unittest.TestCase):
    @patch("requests.get")
    def test_get_open_trades_extracts_stop_loss_price(self, mock_get):
        client = OANDAClient.__new__(OANDAClient)
        client.base_url = "https://api-fxpractice.oanda.com/v3"
        client.account_id = "demo-account"
        client.headers = {"Authorization": "Bearer test"}

        class _Response:
            def json(self):
                return {
                    "trades": [
                        {
                            "id": "5",
                            "instrument": "EUR_USD",
                            "currentUnits": "-362318",
                            "price": "1.15374",
                            "unrealizedPL": "300.72",
                            "openTime": "2030-03-18T12:00:00Z",
                            "stopLossOrder": {"price": "1.15650"},
                        }
                    ]
                }

        mock_get.return_value = _Response()

        trades = client.get_open_trades()

        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["stop_loss_price"], 1.1565)

    def test_calculate_open_risk_pct_uses_stop_distance_when_available(self):
        builder = MarketDataBuilder(oanda_client=object())

        pct = builder._calculate_open_risk_pct(
            [
                {
                    "instrument": "EUR_USD",
                    "units": -100000,
                    "open_price": 1.15374,
                    "stop_loss_price": 1.15650,
                    "unrealized_pl": 300.72,
                }
            ],
            equity=100000.0,
        )

        self.assertEqual(pct, 0.28)

    def test_calculate_open_risk_pct_falls_back_when_stop_missing(self):
        builder = MarketDataBuilder(oanda_client=object())

        pct = builder._calculate_open_risk_pct(
            [
                {
                    "instrument": "EUR_USD",
                    "units": -100000,
                    "open_price": 1.15374,
                    "unrealized_pl": 300.72,
                }
            ],
            equity=100000.0,
        )

        self.assertEqual(pct, 0.3)


if __name__ == "__main__":
    unittest.main()
