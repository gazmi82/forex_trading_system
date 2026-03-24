from __future__ import annotations

import json
import logging
from pathlib import Path

from app.analysis.decision_logging import log_analysis
from app.analysis.message_builder import build_analysis_user_message
from app.analysis.prompt import FOREX_ANALYST_SYSTEM_PROMPT
from app.analysis.signal_pipeline import (
    extract_json_object,
    get_runtime_issue,
    is_allowed_session,
    is_within_news_blackout,
    parse_signal,
    validate_signal,
)
from app.analysis.trade_feedback import TradeFeedbackManager


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
        self._log_analysis(pair, market_data, signal, retrieved_chunks)

        runtime_issue = self._get_runtime_issue(signal)
        if runtime_issue:
            print(f"\n  ❌ Claude analysis failure: {runtime_issue}")
            print("  ⚠️  Using fallback neutral signal")

        direction = signal.get("signal", {}).get("direction", "NEUTRAL")
        confidence = signal.get("signal", {}).get("confidence", 0)
        claude_score = signal.get("confluence_score", 0)
        mechanical_score = signal.get("mechanical_confluence_score", 0)
        execution_label = "EXECUTE" if signal.get("execution_allowed") else "BLOCKED"
        label = "Fallback Signal" if runtime_issue else "Signal"
        print(
            f"\n  📊 {label}: {direction} | Confidence: {confidence}% | "
            f"Claude Score: {claude_score}/100 | Mechanical: {mechanical_score}/100 | "
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
        try:
            response = self.client.messages.create(
                model=self.config.get("model", "claude-sonnet-4-20250514"),
                max_tokens=self.config.get("max_tokens", 2000),
                temperature=0,
                system=FOREX_ANALYST_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
            return response.content[0].text

        except Exception as exc:
            logger.error("Claude API call failed: %s", exc)
            return json.dumps(
                {
                    "error": str(exc),
                    "signal": {"direction": "NEUTRAL", "confidence": 0},
                    "do_not_trade_reason": f"API error: {exc}",
                }
            )

    @staticmethod
    def _extract_json_object(text: str):
        return extract_json_object(text)

    def _parse_signal(self, raw_response: str, pair: str) -> dict:
        return parse_signal(raw_response, pair)

    def _validate_signal(self, signal: dict, market_data: dict) -> dict:
        return validate_signal(
            signal,
            market_data,
            config=self.config,
            has_session_loss_streak=self._has_session_loss_streak,
        )

    def _is_within_news_blackout(self, time_to_event) -> bool:
        return is_within_news_blackout(time_to_event)

    def _is_allowed_session(self, session: str) -> bool:
        return is_allowed_session(session)

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
            return ""

    def record_trade_outcome(self, trade_record: dict):
        lesson = self._generate_trade_lesson(trade_record)
        if lesson:
            trade_record = dict(trade_record)
            trade_record["lesson"] = lesson
        self.feedback.record_trade_outcome(trade_record)

    def _log_analysis(
        self,
        pair: str,
        market_data: dict,
        signal: dict,
        retrieved_chunks: dict,
    ):
        log_analysis(
            self.log_dir,
            pair=pair,
            market_data=market_data,
            signal=signal,
            retrieved_chunks=retrieved_chunks,
        )
