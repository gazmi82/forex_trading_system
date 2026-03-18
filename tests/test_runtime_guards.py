from __future__ import annotations

import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from app.analysis.agent import ForexAnalystAgent
from app.cli.main import print_signal_runtime_issue
from app.fundamentals.fetcher import _classify_news_risk


class RuntimeGuardTests(unittest.TestCase):
    def test_print_signal_runtime_issue_handles_null_reason(self):
        with redirect_stdout(io.StringIO()) as output:
            print_signal_runtime_issue({"error": "upstream failed", "do_not_trade_reason": None})

        self.assertIn("Claude API failure", output.getvalue())

    def test_print_signal_runtime_issue_handles_missing_reason(self):
        with redirect_stdout(io.StringIO()) as output:
            print_signal_runtime_issue({"signal": {"direction": "NEUTRAL"}})

        self.assertEqual(output.getvalue(), "")

    def test_agent_runtime_issue_handles_null_reason(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            agent = ForexAnalystAgent(
                rag_pipeline=None,
                anthropic_client=None,
                config={},
                log_dir=Path(tmpdir),
            )

        self.assertEqual(agent._get_runtime_issue({"do_not_trade_reason": None}), "")
        self.assertEqual(
            agent._get_runtime_issue({"do_not_trade_reason": "JSON parse error - bad payload"}),
            "JSON parse error - bad payload",
        )

    def test_classify_news_risk_handles_missing_event_name(self):
        self.assertEqual(_classify_news_risk(None, "25 minutes"), "HIGH")
        self.assertEqual(_classify_news_risk(None, None), "LOW")


if __name__ == "__main__":
    unittest.main()
