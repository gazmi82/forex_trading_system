from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path


def log_analysis(
    log_dir: Path,
    *,
    pair: str,
    market_data: dict,
    signal: dict,
    retrieved_chunks: dict,
) -> None:
    log_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "pair": pair,
        "price": market_data.get("price"),
        "signal": signal.get("signal", {}),
        "confluence_score": signal.get("confluence_score", 0),
        "mechanical_confluence_score": signal.get("mechanical_confluence_score", 0),
        "signal_strength": signal.get("signal_strength", "NEUTRAL"),
        "execution_allowed": signal.get("execution_allowed", False),
        "execution_direction": signal.get("execution_direction", "NEUTRAL"),
        "reasoning": signal.get("reasoning", []),
        "key_risk": signal.get("key_risk", ""),
        "overrides": signal.get("validator_overrides", []),
        "rag_chunks_used": sum(len(value) for value in retrieved_chunks.values()),
        "rag_categories": list(retrieved_chunks.keys()),
        "knowledge_sources": signal.get("knowledge_sources_used", []),
    }

    log_file = log_dir / "agent_decisions.jsonl"
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry) + "\n")

    claude_score = signal.get("confluence_score", 0)
    mech_score = signal.get("mechanical_confluence_score", 0)
    direction = (signal.get("signal") or {}).get("direction", "NEUTRAL")
    session = signal.get("session", "")
    calibration_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "session": session,
        "claude_score": claude_score,
        "mechanical_score": mech_score,
        "delta": mech_score - claude_score,
        "direction": direction,
        "outcome": None,
    }
    cal_file = log_dir / "score_calibration.jsonl"
    with open(cal_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(calibration_entry) + "\n")
