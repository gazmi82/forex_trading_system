from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from app.analysis.agent import ForexAnalystAgent


class _RecordingMessages:
    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class _RecordingClient:
    def __init__(self, outcomes):
        self.messages = _RecordingMessages(outcomes)


class AgentResilienceTests(unittest.TestCase):
    def _make_agent(self, client, *, config: dict | None = None) -> ForexAnalystAgent:
        with tempfile.TemporaryDirectory() as tmpdir:
            agent = ForexAnalystAgent(
                rag_pipeline=None,
                anthropic_client=client,
                config=config or {},
                log_dir=Path(tmpdir),
            )
            agent._sleep = lambda *_args: None
            return agent

    def test_call_claude_retries_transient_overload_then_recovers(self):
        response = SimpleNamespace(content=[SimpleNamespace(text='{"signal":{"direction":"BUY","confidence":70}}')])
        client = _RecordingClient([RuntimeError("Error code: 529 - overloaded"), response])
        agent = self._make_agent(
            client,
            config={
                "claude_retry_attempts": 3,
                "claude_retry_backoff_seconds": 0,
            },
        )

        raw = agent._call_claude("hello")

        self.assertEqual(client.messages.calls, 2)
        payload = json.loads(raw)
        self.assertEqual(payload["signal"]["direction"], "BUY")
        self.assertEqual(agent._claude_failure_count, 0)
        self.assertIsNone(agent._claude_disabled_until)

    def test_call_claude_does_not_retry_non_retryable_credit_error(self):
        client = _RecordingClient([RuntimeError("credit balance too low")])
        agent = self._make_agent(
            client,
            config={
                "claude_retry_attempts": 3,
                "claude_retry_backoff_seconds": 0,
            },
        )

        raw = agent._call_claude("hello")
        payload = json.loads(raw)

        self.assertEqual(client.messages.calls, 1)
        self.assertEqual(payload["signal"]["direction"], "NEUTRAL")
        self.assertIn("credit balance too low", payload["error"])

    def test_call_claude_opens_circuit_after_repeated_failures(self):
        client = _RecordingClient(
            [
                RuntimeError("Error code: 529 - overloaded"),
                RuntimeError("Error code: 529 - overloaded"),
            ]
        )
        agent = self._make_agent(
            client,
            config={
                "claude_retry_attempts": 1,
                "claude_circuit_failures": 2,
                "claude_cooldown_seconds": 120,
            },
        )
        now = datetime(2026, 3, 26, 14, 0, tzinfo=timezone.utc)
        agent._utcnow = lambda: now

        first = json.loads(agent._call_claude("hello"))
        second = json.loads(agent._call_claude("hello"))
        third = json.loads(agent._call_claude("hello"))

        self.assertEqual(client.messages.calls, 2)
        self.assertIn("overloaded", first["error"])
        self.assertIn("overloaded", second["error"])
        self.assertIn("cooldown active", third["error"])
        self.assertIsNotNone(agent._claude_disabled_until)


if __name__ == "__main__":
    unittest.main()
