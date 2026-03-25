from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Callable, Mapping

from app.analysis.confluence_scorer import calculate_confluence
from app.analysis.scheduler import ALLOWED_ENTRY_SESSIONS


logger = logging.getLogger(__name__)


def get_runtime_issue(signal: Mapping[str, Any]) -> str:
    if signal.get("error"):
        return str(signal["error"])

    reason = signal.get("do_not_trade_reason") or ""
    if reason.startswith("API error"):
        return reason
    if reason.startswith("JSON parse error"):
        return reason
    return ""


def extract_json_object(text: str) -> str | None:
    """
    Find the first complete, balanced JSON object in text.
    Tracks brace depth and string state to avoid the greedy-regex
    pitfall where {…} matches from the first { to the very last }.
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_string = False
    escape_next = False
    for i, ch in enumerate(text[start:], start):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def parse_signal(raw_response: str, pair: str) -> dict[str, Any]:
    try:
        return json.loads(raw_response)
    except json.JSONDecodeError:
        json_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw_response, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except Exception:
                pass

        extracted = extract_json_object(raw_response)
        if extracted:
            try:
                return json.loads(extracted)
            except Exception:
                pass

        logger.error("Failed to parse signal JSON for %s", pair)
        return {
            "pair": pair,
            "timestamp": datetime.utcnow().isoformat(),
            "signal": {"direction": "NEUTRAL", "confidence": 0},
            "confluence_score": 0,
            "signal_strength": "NEUTRAL",
            "do_not_trade_reason": "JSON parse error — raw response logged",
            "raw_response": raw_response[:500],
        }


def is_within_news_blackout(time_to_event: Any) -> bool:
    if time_to_event is None or not time_to_event:
        return False
    try:
        lowered = str(time_to_event).lower()
        mins_match = re.search(r"(\d+)\s*min", lowered)
        hours_match = re.search(r"(\d+)\s*hour", lowered)
        if not mins_match and not hours_match:
            return False
        total_minutes = 0
        if hours_match:
            total_minutes += int(hours_match.group(1)) * 60
        if mins_match:
            total_minutes += int(mins_match.group(1))
        if "ago" in lowered:
            total_minutes *= -1
        return -30 <= total_minutes <= 30
    except Exception:
        return False


def is_allowed_session(session: str) -> bool:
    return session in ALLOWED_ENTRY_SESSIONS


def validate_signal(
    signal: dict[str, Any],
    market_data: dict[str, Any],
    *,
    config: Mapping[str, Any],
    has_session_loss_streak: Callable[[str, int], bool],
) -> dict[str, Any]:
    """
    Apply all deterministic runtime gates after Claude returns.

    This function is the single place where a proposed signal becomes either
    executable or blocked. It preserves Claude's original payload, computes the
    mechanical score, and records explicit override reasons instead of mutating
    the proposal into an ambiguous partial state.
    """
    port = market_data.get("portfolio", {})
    fund = market_data.get("fundamental", {})
    sig = signal.get("signal", {})
    overrides: list[str] = []
    proposed_direction = (sig.get("direction") or "NEUTRAL").upper()
    execution_allowed = proposed_direction != "NEUTRAL"
    execution_direction = proposed_direction if execution_allowed else "NEUTRAL"

    runtime_issue = get_runtime_issue(signal)
    if runtime_issue:
        if signal.get("error"):
            overrides.append("BLOCKED: Claude API unavailable")
        else:
            overrides.append("BLOCKED: Claude response parsing failed")
        signal["mechanical_confluence_score"] = 0
        signal["mechanical_confluence_components"] = {}
        signal["mechanical_direction_implied"] = "NEUTRAL"
        signal["execution_allowed"] = False
        signal["execution_direction"] = "NEUTRAL"
        if market_data.get("demo_mode", True):
            signal["demo_mode"] = True
        signal["validator_overrides"] = overrides
        signal["signal"] = sig
        logger.warning("Signal blocked: %s", overrides)
        return signal

    try:
        mech_result = calculate_confluence(market_data, signal)
        mech_score = mech_result["confluence_score"]
    except Exception as exc:
        logger.warning("Mechanical confluence scorer failed: %s", exc)
        mech_score = 0
        mech_result = {
            "confluence_score": 0,
            "direction_implied": "NEUTRAL",
            "component_scores": {},
        }

    signal["mechanical_confluence_score"] = mech_score
    signal["mechanical_confluence_components"] = mech_result.get("component_scores", {})
    signal["mechanical_direction_implied"] = mech_result.get("direction_implied", "NEUTRAL")

    def block(reason: str):
        nonlocal execution_allowed, execution_direction
        execution_allowed = False
        execution_direction = "NEUTRAL"
        overrides.append(reason)

    min_confidence = int(config.get("min_confidence", 65))
    if mech_score < min_confidence:
        block(
            f"BLOCKED: Mechanical confluence score too low "
            f"({mech_score}/100, minimum {min_confidence}; Claude scored {signal.get('confluence_score', 0)})"
        )

    session = fund.get("active_session", signal.get("session", ""))
    signal["session"] = session
    if not is_allowed_session(session):
        block(f"BLOCKED: Outside allowed kill zones ({session})")

    daily_pnl = port.get("daily_pnl_pct", 0)
    max_trade_risk_pct = config.get("max_risk_per_trade", 0.01) * 100
    max_daily_loss_pct = config.get("max_daily_loss", 0.02) * 100
    if daily_pnl <= -max_daily_loss_pct or (daily_pnl - max_trade_risk_pct) < -max_daily_loss_pct:
        block(
            f"BLOCKED: Daily loss limit reached or would be exceeded "
            f"({daily_pnl:.2f}% today, {max_trade_risk_pct:.1f}% new risk, limit {max_daily_loss_pct:.1f}%)"
        )

    weekly_pnl = port.get("weekly_pnl_pct", 0)
    max_weekly_loss_pct = config.get("max_weekly_loss", 0.05) * 100
    if weekly_pnl <= -max_weekly_loss_pct or (weekly_pnl - max_trade_risk_pct) < -max_weekly_loss_pct:
        block(
            f"BLOCKED: Weekly loss limit reached or would be exceeded "
            f"({weekly_pnl:.2f}% this week, {max_trade_risk_pct:.1f}% new risk, limit {max_weekly_loss_pct:.1f}%)"
        )

    open_risk = port.get("open_risk_pct", 0)
    max_portfolio_risk_pct = config.get("max_portfolio_risk", 0.03) * 100
    if open_risk + max_trade_risk_pct > max_portfolio_risk_pct:
        block(
            f"BLOCKED: Adding trade would exceed portfolio risk cap "
            f"({open_risk:.1f}% open + {max_trade_risk_pct:.1f}% new > {max_portfolio_risk_pct:.1f}% limit)"
        )

    if has_session_loss_streak(session, 2):
        block(f"BLOCKED: Two consecutive losses already recorded in {session}")

    next_event = fund.get("next_news_event") or fund.get("next_event_name") or ""
    if next_event.startswith("MANUAL_CHECK"):
        block("BLOCKED: Live economic calendar unavailable")
    time_to_event = fund.get("time_to_event", "")
    if is_within_news_blackout(time_to_event):
        block(f"BLOCKED: News blackout active ({time_to_event} to event)")

    confidence = sig.get("confidence", 0)
    if confidence < min_confidence:
        block(f"BLOCKED: Confidence too low ({confidence}%)")

    rr = sig.get("risk_reward", 0)
    if rr > 0 and rr < 2.0:
        block(f"BLOCKED: R:R too low ({rr} < 2.0 minimum)")

    if market_data.get("demo_mode", True):
        signal["demo_mode"] = True

    signal["execution_allowed"] = execution_allowed
    signal["execution_direction"] = execution_direction
    signal["validator_overrides"] = overrides
    signal["signal"] = sig
    if overrides:
        logger.warning("Signal blocked: %s", overrides)

    return signal
