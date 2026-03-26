from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
from pathlib import Path
import time

from app.analysis.decision_logging import (
    attach_live_validation_log_reference,
    log_analysis,
    update_calibration_outcome,
    update_live_validation_outcome,
)
from app.analysis.message_builder import build_analysis_user_message
from app.analysis.prompt import FOREX_ANALYST_SYSTEM_PROMPT
from app.analysis.signal_pipeline import (
    get_runtime_issue,
    parse_signal,
    validate_signal,
)
from app.analysis.trade_feedback import TradeFeedbackManager
from app.core.runtime_logging import record_runtime_event


logger = logging.getLogger(__name__)


class ForexAnalystAgent:
    """
    Full integration of:
    - Option 1: Deep system prompt (permanent rules + identity)
    - Option 2: RAG pipeline (dynamic knowledge retrieval)
    - Live market context injection
    - Claude API call
    - Trade logging + feedback loop
    """

    def __init__(self, rag_pipeline, anthropic_client, config: dict, log_dir: Path):
        self.rag = rag_pipeline
        self.client = anthropic_client
        self.config = config
        self.log_dir = log_dir
        self.feedback = TradeFeedbackManager(rag_pipeline, config, log_dir)
        self._claude_failure_count = 0
        self._claude_disabled_until: datetime | None = None
        self._sleep = time.sleep
        self._utcnow = lambda: datetime.now(timezone.utc)
        log_dir.mkdir(parents=True, exist_ok=True)

        logger.info("ForexAnalystAgent initialized")

    def analyze(self, market_data: dict) -> dict:
        """
        Full analysis pipeline:
        1. Retrieve relevant RAG knowledge
        2. Build complete prompt (Option 1 + Option 2 + live data)
        3. Call Claude API
        4. Parse and validate signal
        5. Log everything
        """
        pair = market_data.get("pair", "EUR/USD")
        logger.info("Starting analysis: %s", pair)

        print(f"\n🔍 Retrieving knowledge from RAG for {pair}...")
        market_state = {
            "pair": pair,
            "trend": market_data.get("ohlcv", {}).get("h4_trend", "neutral").lower(),
            "regime": market_data.get("indicators", {}).get("market_regime", "unknown").lower(),
            "next_event": market_data.get("fundamental", {}).get("next_news_event", ""),
            "session": market_data.get("fundamental", {}).get("active_session", ""),
        }

        retrieved_chunks = self.rag.search_for_trading_context(market_state)
        rag_context = self.rag.format_rag_context(
            retrieved_chunks,
            max_tokens=self.config.get("rag_context_tokens", 3000),
        )

        chunk_count = sum(len(value) for value in retrieved_chunks.values())
        print(f"  ✅ Retrieved {chunk_count} relevant knowledge chunks")

        user_message = self._build_user_message(market_data, rag_context)

        print("  🤖 Calling Claude API...")
        raw_response = self._call_claude(user_message)

        signal = self._parse_signal(raw_response, pair)
        signal = self._validate_signal(signal, market_data)
        self._log_analysis(pair, market_data, signal, retrieved_chunks, raw_response, user_message)

        runtime_issue = self._get_runtime_issue(signal)
        if runtime_issue:
            print(f"\n  ❌ Claude analysis failure: {runtime_issue}")
            print("  ⚠️  Using fallback neutral signal")

        direction = signal.get("signal", {}).get("direction", "NEUTRAL")
        confidence = signal.get("signal", {}).get("confidence", 0)
        score = signal.get("confluence_score", 0)
        execution_label = "EXECUTE" if signal.get("execution_allowed") else "BLOCKED"
        label = "Fallback Signal" if runtime_issue else "Signal"
        print(
            f"\n  📊 {label}: {direction} | Confidence: {confidence}% | "
            f"Score: {score}/100 | "
            f"{execution_label}"
        )

        return signal

    def _get_runtime_issue(self, signal: dict) -> str:
        return get_runtime_issue(signal)

    def _build_user_message(self, market_data: dict, rag_context: str) -> str:
        return build_analysis_user_message(
            market_data,
            rag_context,
            self.feedback.render_memory_section(),
        )

    def _call_claude(self, user_message: str) -> str:
        """
        Return Claude's raw text response.

        The fallback path deliberately serializes API failures into a neutral
        JSON payload so parsing, validation, logging, and UI output can all
        stay on the same downstream code path.
        """
        if self.client is None:
            exc = RuntimeError("Claude client unavailable")
            self._register_claude_failure(exc)
            record_runtime_event(
                component="analysis.agent",
                action="claude_api_call",
                message="Claude client unavailable; returning neutral fallback payload",
                context={},
                exc=exc,
                log_dir=self.log_dir,
            )
            return self._fallback_signal_payload(str(exc))

        if self._claude_circuit_active():
            reason = (
                "Claude temporarily unavailable: cooldown active until "
                f"{self._claude_disabled_until.isoformat().replace('+00:00', 'Z')}"
            )
            logger.warning(reason)
            record_runtime_event(
                component="analysis.agent",
                action="claude_circuit_open",
                level="WARNING",
                message="Claude circuit open; skipping provider call",
                context={
                    "cooldown_until_utc": self._claude_disabled_until.isoformat().replace("+00:00", "Z"),
                    "consecutive_failures": self._claude_failure_count,
                },
                log_dir=self.log_dir,
            )
            return self._fallback_signal_payload(reason)

        model = self.config.get("model", "claude-sonnet-4-20250514")
        max_tokens = self.config.get("max_tokens", 2000)
        retry_attempts = max(int(self.config.get("claude_retry_attempts", 3) or 1), 1)
        backoff_seconds = max(float(self.config.get("claude_retry_backoff_seconds", 1.5) or 0), 0.0)

        last_exc: Exception | None = None
        for attempt in range(1, retry_attempts + 1):
            try:
                response = self.client.messages.create(
                    model=model,
                    max_tokens=max_tokens,
                    temperature=0,
                    system=FOREX_ANALYST_SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_message}],
                )
                self._reset_claude_failure_state()
                return response.content[0].text
            except Exception as exc:
                last_exc = exc
                retryable = self._is_retryable_claude_error(exc)
                is_last_attempt = attempt >= retry_attempts
                if retryable and not is_last_attempt:
                    delay = backoff_seconds * (2 ** (attempt - 1))
                    logger.warning(
                        "Claude API transient failure (attempt %s/%s): %s. Retrying in %.1fs",
                        attempt,
                        retry_attempts,
                        exc,
                        delay,
                    )
                    record_runtime_event(
                        component="analysis.agent",
                        action="claude_api_retry",
                        level="WARNING",
                        message="Claude API transient failure; retrying",
                        context={
                            "attempt": attempt,
                            "retry_attempts": retry_attempts,
                            "backoff_seconds": delay,
                            "model": model,
                        },
                        exc=exc,
                        log_dir=self.log_dir,
                    )
                    if delay > 0:
                        self._sleep(delay)
                    continue
                break

        assert last_exc is not None
        self._register_claude_failure(last_exc)
        logger.error("Claude API call failed: %s", last_exc)
        record_runtime_event(
            component="analysis.agent",
            action="claude_api_call",
            message="Claude API call failed; returning neutral fallback payload",
            context={
                "model": model,
                "max_tokens": max_tokens,
                "retry_attempts": retry_attempts,
                "consecutive_failures": self._claude_failure_count,
                "cooldown_until_utc": (
                    self._claude_disabled_until.isoformat().replace("+00:00", "Z")
                    if self._claude_disabled_until is not None
                    else None
                ),
            },
            exc=last_exc,
            log_dir=self.log_dir,
        )
        return self._fallback_signal_payload(str(last_exc))

    def _claude_circuit_active(self) -> bool:
        if self._claude_disabled_until is None:
            return False
        if self._utcnow() >= self._claude_disabled_until:
            self._claude_disabled_until = None
            self._claude_failure_count = 0
            return False
        return True

    def _register_claude_failure(self, exc: Exception) -> None:
        self._claude_failure_count += 1
        threshold = max(int(self.config.get("claude_circuit_failures", 3) or 1), 1)
        cooldown_seconds = max(int(self.config.get("claude_cooldown_seconds", 120) or 0), 0)
        if self._claude_failure_count >= threshold and cooldown_seconds > 0:
            self._claude_disabled_until = self._utcnow() + timedelta(seconds=cooldown_seconds)
            logger.warning(
                "Claude circuit opened for %ss after %s consecutive failures",
                cooldown_seconds,
                self._claude_failure_count,
            )
            record_runtime_event(
                component="analysis.agent",
                action="claude_circuit_opened",
                level="WARNING",
                message="Claude circuit opened after repeated provider failures",
                context={
                    "consecutive_failures": self._claude_failure_count,
                    "cooldown_seconds": cooldown_seconds,
                    "cooldown_until_utc": self._claude_disabled_until.isoformat().replace("+00:00", "Z"),
                    "error": str(exc),
                },
                exc=exc,
                log_dir=self.log_dir,
            )

    def _reset_claude_failure_state(self) -> None:
        self._claude_failure_count = 0
        self._claude_disabled_until = None

    def _is_retryable_claude_error(self, exc: Exception) -> bool:
        message = str(exc).lower()
        non_retryable_markers = (
            "credit balance",
            "authentication",
            "unauthorized",
            "invalid api key",
            "permission",
            "forbidden",
            "invalid_request_error",
            "bad request",
        )
        if any(marker in message for marker in non_retryable_markers):
            return False

        retryable_markers = (
            "529",
            "overloaded",
            "rate limit",
            "timeout",
            "timed out",
            "temporarily unavailable",
            "service unavailable",
            "connection reset",
            "connection aborted",
            "connection refused",
            "server disconnected",
            "network",
            "transport",
            "remote protocol error",
        )
        return any(marker in message for marker in retryable_markers)

    def _fallback_signal_payload(self, error_message: str) -> str:
        return json.dumps(
            {
                "error": error_message,
                "signal": {"direction": "NEUTRAL", "confidence": 0},
                "do_not_trade_reason": f"API error: {error_message}",
            }
        )

    def _parse_signal(self, raw_response: str, pair: str) -> dict:
        return parse_signal(raw_response, pair)

    def _validate_signal(self, signal: dict, market_data: dict) -> dict:
        return validate_signal(
            signal,
            market_data,
            config=self.config,
            has_session_loss_streak=self._has_session_loss_streak,
        )

    def _has_session_loss_streak(self, session: str, limit: int = 2) -> bool:
        return self.feedback.has_session_loss_streak(session, limit)

    def _generate_trade_lesson(self, feedback_record: dict) -> str:
        """
        Generate a concise, process-focused lesson using claude-haiku.
        Called once per closed trade before the record reaches RAG storage.
        """
        try:
            outcome = feedback_record.get("outcome", "UNKNOWN")
            setup_grade = feedback_record.get("setup_grade", "?")
            root_cause = feedback_record.get("root_cause", "UNDETERMINED")
            entry_timing = feedback_record.get("entry_timing", "UNKNOWN")
            ict_post_hoc = feedback_record.get("ict_post_hoc") or {}
            direction = feedback_record.get("direction", "")
            session = feedback_record.get("session", "")
            pnl_r = feedback_record.get("pnl_r", 0)
            tags = feedback_record.get("pattern_tags", [])
            reasoning = feedback_record.get("reasoning", [])
            reasoning_text = "; ".join(str(r) for r in reasoning[:2]) if reasoning else "none recorded"

            prompt = (
                f"Trade: EUR/USD {direction} | Session: {session}\n"
                f"Outcome: {outcome} ({pnl_r}R) | Setup Grade: {setup_grade}\n"
                f"Root cause: {root_cause} | Entry timing: {entry_timing}\n"
                f"ICT post-hoc: {json.dumps(ict_post_hoc)}\n"
                f"Pattern tags: {', '.join(tags)}\n"
                f"Entry reasoning (first 2 points): {reasoning_text}\n\n"
                "In 1-2 sentences, state the single most important process lesson from this trade. "
                "Focus on what should be repeated or avoided next time, not the outcome itself. "
                "Be specific to the root cause and setup grade. No preamble — just the lesson."
            )

            response = self.client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=200,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text.strip()[:300]
        except Exception as exc:
            logger.warning("Trade lesson generation failed: %s", exc)
            record_runtime_event(
                component="analysis.agent",
                action="generate_trade_lesson",
                message="Trade lesson generation failed; continuing without lesson text",
                context={
                    "pair": feedback_record.get("pair", ""),
                    "direction": feedback_record.get("direction", ""),
                    "session": feedback_record.get("session", ""),
                    "outcome": feedback_record.get("outcome", ""),
                },
                exc=exc,
                log_dir=self.log_dir,
            )
            return ""

    def record_trade_outcome(self, trade_record: dict):
        """
        Persist a closed-trade outcome into both learning systems.

        1. TradeFeedbackManager stores the human-readable lesson/memory.
        2. The calibration log is updated so Claude's score can be evaluated
           against actual outcomes later.
        """
        lesson = self._generate_trade_lesson(trade_record)
        if lesson:
            trade_record = dict(trade_record)
            trade_record["lesson"] = lesson
        self.feedback.record_trade_outcome(trade_record)
        updated = update_calibration_outcome(self.log_dir, trade_record)
        update_live_validation_outcome(self.log_dir, trade_record)
        if not updated and (self.log_dir / "score_calibration.jsonl").exists():
            logger.warning(
                "Calibration log update could not match closed trade for signal timestamp %s",
                trade_record.get("signal_timestamp", ""),
            )

    def attach_live_validation_reference(self, signal: dict[str, object]) -> bool:
        return attach_live_validation_log_reference(self.log_dir, signal)

    def _log_analysis(
        self,
        pair: str,
        market_data: dict,
        signal: dict,
        retrieved_chunks: dict,
        raw_response: str,
        user_message: str,
    ):
        log_analysis(
            self.log_dir,
            pair=pair,
            market_data=market_data,
            signal=signal,
            retrieved_chunks=retrieved_chunks,
            raw_response=raw_response,
            user_message=user_message,
            model=self.config.get("model", "claude-sonnet-4-20250514"),
            system_prompt=FOREX_ANALYST_SYSTEM_PROMPT,
        )
