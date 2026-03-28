from __future__ import annotations

HISTORICAL_UNAVAILABLE = "HISTORICAL_UNAVAILABLE"
HISTORICAL_NEWS_UNAVAILABLE = "HISTORICAL_NEWS_UNAVAILABLE"
HISTORICAL_DIRECTION_NEUTRAL = "NEUTRAL"


def build_live_fundamental_snapshot(auto: dict, session_info: dict) -> dict:
    """
    Build the canonical live fundamental payload consumed by the rest of the app.
    """
    return {
        "usd_rate": auto["usd_rate"],
        "fed_target_lower_rate": auto["fed_target_lower_rate"],
        "fed_target_upper_rate": auto["fed_target_upper_rate"],
        "pair_rate": auto["eur_rate"],
        "ecb_main_refi_rate": auto["ecb_main_refi_rate"],
        "ecb_marginal_lending_rate": auto["ecb_marginal_lending_rate"],
        "ecb_deposit_rate": auto["ecb_deposit_rate"],
        "rate_differential": auto["rate_differential"],
        "dxy_direction": auto["dxy_direction"],
        "dxy_level": auto["dxy_level"],
        "cot_net": auto["cot_net"],
        "cot_bias": auto["cot_bias"],
        "retail_sentiment": auto["retail_sentiment"],
        "risk_sentiment": auto["risk_sentiment"],
        "rates_source": auto["rates_source"],
        "next_event_name": auto["next_event_name"],
        "next_news_event": auto["next_news_event"],
        "time_to_event": auto["time_to_event"],
        "news_risk": auto["news_risk"],
        "recent_headline": auto["recent_headline"],
        "active_session": session_info["active_session"],
        "kill_zone_active": session_info["kill_zone_active"],
        "trade_window_active": session_info["trade_window_active"],
    }


def build_replay_session_info(session: str) -> dict:
    return {
        "active_session": session,
        "kill_zone_active": f"YES — {session} replay",
        "trade_window_active": True,
    }


def build_historical_auto_defaults() -> dict:
    return {
        "usd_rate": None,
        "fed_target_lower_rate": None,
        "fed_target_upper_rate": None,
        "eur_rate": None,
        "ecb_main_refi_rate": None,
        "ecb_marginal_lending_rate": None,
        "ecb_deposit_rate": None,
        "rate_differential": HISTORICAL_UNAVAILABLE,
        "dxy_direction": HISTORICAL_DIRECTION_NEUTRAL,
        "dxy_level": None,
        "cot_net": None,
        "cot_bias": HISTORICAL_DIRECTION_NEUTRAL,
        "retail_sentiment": HISTORICAL_UNAVAILABLE,
        "risk_sentiment": HISTORICAL_UNAVAILABLE,
        "rates_source": HISTORICAL_UNAVAILABLE,
        "next_event_name": HISTORICAL_NEWS_UNAVAILABLE,
        "next_news_event": HISTORICAL_NEWS_UNAVAILABLE,
        "time_to_event": None,
        "news_risk": HISTORICAL_UNAVAILABLE,
        "recent_headline": HISTORICAL_NEWS_UNAVAILABLE,
    }


def build_historical_fundamental_snapshot(session: str, auto: dict | None = None) -> dict:
    """
    Build a replay-safe payload that preserves the exact live fundamental shape.
    """
    historical_auto = build_historical_auto_defaults()
    if auto:
        historical_auto.update(auto)
    return build_live_fundamental_snapshot(historical_auto, build_replay_session_info(session))
