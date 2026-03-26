from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from app.core.runtime_backfill import backfill_runtime_store_from_logs
from app.core.runtime_store import (
    current_open_trades,
    latest_closed_trades,
    latest_decisions,
    latest_signal,
    latest_trade_history,
)


class RuntimeBackfillTests(unittest.TestCase):
    def test_backfill_runtime_store_from_logs_imports_existing_files(self):
        original_store_path = os.environ.get("RUNTIME_STORE_PATH")
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir)
            os.environ["RUNTIME_STORE_PATH"] = str(base / "runtime_store.sqlite3")

            (base / "signal_20260326.json").write_text(
                json.dumps(
                    {
                        "log_type": "signal",
                        "log_date_utc": "2026-03-26",
                        "entries": [
                            {
                                "timestamp": "2026-03-26T14:00:00Z",
                                "logged_at_utc": "2026-03-26T14:00:05Z",
                                "log_filename": "signal_20260326.json",
                                "log_entry_id": "20260326_140005_000001",
                                "signal": {"direction": "SELL", "confidence": 71},
                                "confluence_score": 77,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (base / "agent_decisions.jsonl").write_text(
                json.dumps({"timestamp": "2026-03-26T14:00:00Z", "pair": "EUR/USD"}) + "\n",
                encoding="utf-8",
            )
            (base / "open_trades.json").write_text(
                json.dumps({"trade-1": {"trade_id": "trade-1", "direction": "BUY"}}),
                encoding="utf-8",
            )
            (base / "closed_trades.jsonl").write_text(
                json.dumps({"date": "2026-03-26", "pair": "EUR_USD", "outcome": "WIN"}) + "\n",
                encoding="utf-8",
            )
            (base / "trades.csv").write_text(
                "\n".join(
                    [
                        "timestamp,order_id,trade_id,instrument,direction,units,entry_price,stop_loss,tp1,tp2,status,pnl,notes",
                        "2026-03-26T14:00:00Z,ord-1,trade-1,EUR_USD,BUY,1000,1.08,1.07,1.09,1.10,OPEN,0,Session:NY Kill Zone",
                    ]
                ),
                encoding="utf-8",
            )

            counts = backfill_runtime_store_from_logs(log_dir=base, sync_remote=False)

            signal = latest_signal(kind="signal")
            decisions = latest_decisions(limit=10)
            open_trades = current_open_trades()
            closed = latest_closed_trades(limit=10)
            history = latest_trade_history(limit=10)

        if original_store_path is None:
            os.environ.pop("RUNTIME_STORE_PATH", None)
        else:
            os.environ["RUNTIME_STORE_PATH"] = original_store_path

        self.assertEqual(counts["signals"], 1)
        self.assertEqual(counts["decisions"], 1)
        self.assertEqual(counts["open_trades"], 1)
        self.assertEqual(counts["closed_trades"], 1)
        self.assertEqual(counts["trade_history"], 1)
        self.assertEqual(signal["signal"]["direction"], "SELL")
        self.assertEqual(len(decisions), 1)
        self.assertEqual(len(open_trades), 1)
        self.assertEqual(len(closed), 1)
        self.assertEqual(len(history), 1)


if __name__ == "__main__":
    unittest.main()
