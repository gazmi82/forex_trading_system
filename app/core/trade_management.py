from __future__ import annotations

from typing import Any, Mapping


def resolve_time_stop_hours(
    config_value: Any,
    session: str,
    *,
    default: float = 8.0,
) -> float:
    """
    Resolve the configured holding limit for a session.

    Supports both the new session-specific mapping and the legacy scalar value
    so older configs and tests keep working while Phase 4 rolls out.
    """
    if isinstance(config_value, Mapping):
        session_key = str(session or "").strip()
        candidates = (
            session_key,
            session_key.replace("_", " "),
            "default",
        )
        for key in candidates:
            if key in config_value:
                return _coerce_positive_float(config_value[key], default)
        return float(default)
    return _coerce_positive_float(config_value, default)


def trail_distance_from_context(
    *,
    entry_price: float,
    tp1_price: float,
    atr_1h_at_entry: Any,
    trail_atr_multiplier: Any,
) -> float:
    """
    Prefer ATR-based trailing when entry-time ATR is known, otherwise fall back
    to the legacy entry-to-TP1 distance so existing trades remain manageable.
    """
    atr_value = _coerce_positive_float(atr_1h_at_entry, 0.0)
    multiplier = _coerce_positive_float(trail_atr_multiplier, 1.0)
    if atr_value > 0:
        return round(atr_value * multiplier, 5)

    fallback = abs(float(tp1_price or 0.0) - float(entry_price or 0.0))
    return round(max(fallback, 0.0005), 5)


def resolve_adaptive_time_stop_hours(
    config: Mapping[str, Any],
    *,
    session: str,
    direction: str,
    technical_analysis: Mapping[str, Any] | None = None,
    macro_bias: Mapping[str, Any] | None = None,
    mechanical_score: Any = None,
) -> tuple[float, list[str]]:
    """
    Build a custom time window from the session base plus deterministic
    extensions that indicate the original directional thesis is stronger.
    """
    base_hours = resolve_time_stop_hours(
        config.get("time_stop_hours", 8),
        session,
    )
    if not bool(config.get("adaptive_time_stop", True)):
        return round(base_hours, 2), []

    technical = technical_analysis if isinstance(technical_analysis, Mapping) else {}
    macro = macro_bias if isinstance(macro_bias, Mapping) else {}
    direction_upper = str(direction or "").strip().upper()
    ema_bias = str(technical.get("ema_bias") or "").strip().upper()
    regime = str(technical.get("market_regime") or "").strip().upper()
    alignment = str(macro.get("alignment") or "").strip().upper()

    ext_cfg = config.get("adaptive_time_stop_extensions", {})
    extension = 0.0
    reasons: list[str] = []

    if (direction_upper == "BUY" and ema_bias == "BULLISH") or (
        direction_upper == "SELL" and ema_bias == "BEARISH"
    ):
        extension += _coerce_nonnegative_float(ext_cfg.get("trend_aligned_hours"), 1.0)
        reasons.append("trend_aligned")

    if alignment == "ALIGNED":
        extension += _coerce_nonnegative_float(ext_cfg.get("macro_aligned_hours"), 0.5)
        reasons.append("macro_aligned")

    if regime == "TRENDING":
        extension += _coerce_nonnegative_float(ext_cfg.get("trending_hours"), 0.5)
        reasons.append("trending_regime")
    elif regime == "HIGH_VOLATILITY":
        extension += _coerce_nonnegative_float(ext_cfg.get("high_volatility_hours"), 1.0)
        reasons.append("high_volatility")

    score_value = _coerce_nonnegative_float(mechanical_score, 0.0)
    if score_value >= _coerce_nonnegative_float(ext_cfg.get("strong_signal_threshold"), 85.0):
        extension += _coerce_nonnegative_float(ext_cfg.get("strong_signal_hours"), 0.5)
        reasons.append("strong_confluence")

    max_extension = _coerce_nonnegative_float(ext_cfg.get("max_total_hours"), 2.0)
    extension = min(extension, max_extension)
    return round(base_hours + extension, 2), reasons


def _coerce_positive_float(value: Any, fallback: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(fallback)
    if parsed <= 0:
        return float(fallback)
    return float(parsed)


def _coerce_nonnegative_float(value: Any, fallback: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(fallback)
    if parsed < 0:
        return float(fallback)
    return float(parsed)
