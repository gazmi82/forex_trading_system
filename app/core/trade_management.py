from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class EarlyMomentumAssessment:
    enabled: bool
    minutes: float
    gap_pips: float | None
    progress_ratio: float | None
    should_exit: bool
    trigger_reason: str


def resolve_tp1_price(
    config: Mapping[str, Any],
    *,
    entry_price: Any,
    tp2_price: Any,
    fallback_tp1: Any = None,
) -> float | None:
    """
    Place TP1 at a configurable fraction of the distance from entry to TP2.

    This keeps live validation and deterministic replay aligned on the same
    trade-management structure instead of relying on prompt wording alone.
    """
    try:
        entry = float(entry_price)
        tp2 = float(tp2_price)
    except (TypeError, ValueError):
        return _coerce_optional_price(fallback_tp1)

    if entry <= 0 or tp2 <= 0 or entry == tp2:
        return _coerce_optional_price(fallback_tp1)

    fraction = _coerce_nonnegative_float(
        config.get("tp1_target_fraction_of_tp2", 0.50),
        0.50,
    )
    fraction = min(max(fraction, 0.0), 1.0)
    return round(entry + ((tp2 - entry) * fraction), 5)


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
    confluence_score: Any = None,
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

    score_value = _coerce_nonnegative_float(confluence_score, 0.0)
    if score_value >= _coerce_nonnegative_float(ext_cfg.get("strong_signal_threshold"), 85.0):
        extension += _coerce_nonnegative_float(ext_cfg.get("strong_signal_hours"), 0.5)
        reasons.append("strong_confluence")

    max_extension = _coerce_nonnegative_float(ext_cfg.get("max_total_hours"), 2.0)
    extension = min(extension, max_extension)
    return round(base_hours + extension, 2), reasons


def assess_early_momentum_exit(
    config: Mapping[str, Any],
    *,
    direction: str,
    entry_price: Any,
    tp2_price: Any,
    favorable_price: Any,
) -> EarlyMomentumAssessment:
    """
    Evaluate whether a trade has shown enough expansion toward TP2 in the first
    configured minutes after entry.

    By default the rule is:
    - enabled
    - check once after 60 minutes
    - exit if the best favorable price is still more than 15 pips from TP2

    An optional progress ratio can also be configured if a percentage-based
    threshold is preferred in addition to the absolute gap check.
    """
    if not bool(config.get("early_momentum_exit", True)):
        return EarlyMomentumAssessment(False, 0.0, None, None, False, "")

    minutes = _coerce_positive_float(config.get("early_momentum_minutes"), 60.0)
    try:
        entry = float(entry_price)
        tp2 = float(tp2_price)
        favorable = float(favorable_price)
    except (TypeError, ValueError):
        return EarlyMomentumAssessment(True, minutes, None, None, False, "")

    total_distance = abs(tp2 - entry)
    if total_distance <= 0:
        return EarlyMomentumAssessment(True, minutes, None, None, False, "")

    direction_upper = str(direction or "").strip().upper()
    if direction_upper == "BUY":
        progressed = favorable - entry
    elif direction_upper == "SELL":
        progressed = entry - favorable
    else:
        return EarlyMomentumAssessment(True, minutes, None, None, False, "")

    progress_ratio = max(0.0, progressed / total_distance)
    gap_pips = abs(tp2 - favorable) * 10000

    max_gap_pips = config.get("early_momentum_max_gap_pips", 15.0)
    min_progress = config.get("early_momentum_min_tp2_progress")

    meets_gap = True
    if max_gap_pips not in (None, ""):
        meets_gap = gap_pips <= _coerce_nonnegative_float(max_gap_pips, 15.0)

    meets_progress = True
    if min_progress not in (None, ""):
        meets_progress = progress_ratio >= _coerce_nonnegative_float(min_progress, 0.0)

    should_exit = not (meets_gap and meets_progress)
    reasons = []
    if not meets_gap and max_gap_pips not in (None, ""):
        reasons.append(f"tp2 gap {gap_pips:.1f} pips > {float(max_gap_pips):.1f}")
    if not meets_progress and min_progress not in (None, ""):
        reasons.append(f"progress {progress_ratio:.2f} < {float(min_progress):.2f}")

    return EarlyMomentumAssessment(
        True,
        round(minutes, 2),
        round(gap_pips, 2),
        round(progress_ratio, 4),
        should_exit,
        "; ".join(reasons),
    )


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


def _coerce_optional_price(value: Any) -> float | None:
    try:
        return round(float(value), 5)
    except (TypeError, ValueError):
        return None
