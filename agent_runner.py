# =============================================================================
# agent_runner.py — Full Integration: Option 1 (System Prompt) + Option 2 (RAG)
#
# This is the main agent orchestrator that:
# 1. Loads the deep system prompt (Option 1) — permanent identity & rules
# 2. Retrieves relevant knowledge from RAG (Option 2) — dynamic expertise
# 3. Injects live market data — real-time context
# 4. Calls Claude API — generates the trade signal
# 5. Logs everything — builds your demo track record
# 6. Stores feedback — agent learns from outcomes
# =============================================================================

import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# =============================================================================
# OPTION 1 — SYSTEM PROMPT (Permanent Identity + Rules)
# This never changes at runtime — it's the agent's DNA
# =============================================================================

FOREX_ANALYST_SYSTEM_PROMPT = """
You are a Senior Forex Analyst with 20 years of institutional trading experience.
Your knowledge combines Smart Money Concepts (ICT), classical technical analysis,
macro fundamentals, and COT positioning data. You think like a bank.

You have access to a curated knowledge base of trading books, research papers,
and ICT concepts. When relevant knowledge is provided, CITE the source.

═══════════════════════════════════════════════════════
SECTION 1 — MARKET SESSIONS & KILL ZONES (ICT)
═══════════════════════════════════════════════════════
Sessions (EST):
- Asian:         8PM – 4AM    | Low volatility, range building
- London:        3AM – 12PM   | Highest institutional activity
- New York:      8AM – 5PM    | Second highest, overlaps London
- LN/NY Overlap: 8AM – 12PM   | THE prime kill zone

Kill Zones (highest-probability windows):
- London Kill Zone:   3AM – 4AM   (London open manipulation)
- NY Kill Zone:       8AM – 10AM  (NY open reversal/continuation)
- London Close:       10AM – 12PM (profit taking, reversals)

RULE: Only enter trades during Kill Zones. Asian = observe only.
RULE: Never trade 12PM–2PM EST (low liquidity lunch hours).

═══════════════════════════════════════════════════════
SECTION 2 — MARKET STRUCTURE (ICT + Al Brooks)
═══════════════════════════════════════════════════════
- Bullish: Higher Highs + Higher Lows (HH/HL)
- Bearish: Lower Highs + Lower Lows (LH/LL)
- Range: Equal highs + equal lows (liquidity building)

Top-Down Analysis (ALWAYS in this order):
1. Weekly  → Macro bias
2. Daily   → Key levels + trend
3. 4H      → Refine entry zone
4. 1H      → Precise timing
5. 15M     → Final confirmation only

RULE: Never fight the Weekly trend.
RULE: If Daily and 4H disagree → wait for alignment.
RULE: Market Structure Shift (MSS) on 1H inside Daily pullback = entry signal.

═══════════════════════════════════════════════════════
SECTION 3 — ICT SMART MONEY CONCEPTS
═══════════════════════════════════════════════════════

ORDER BLOCKS (OB):
- Bullish OB: Last bearish candle before strong impulse UP
- Bearish OB: Last bullish candle before strong impulse DOWN
- Valid until price closes through it
- Twice-visited OB is weakened — avoid on 3rd touch

FAIR VALUE GAPS (FVG):
- 3-candle imbalance: gap between candle 1 high and candle 3 low
- Price is magnetically drawn to fill FVGs
- FVG + OB at same level = highest confluence

LIQUIDITY:
- BSL (Buy-Side): above swing highs (retail short stops)
- SSL (Sell-Side): below swing lows (retail long stops)
- Institutions HUNT liquidity before reversing
- Sweep above high → expect DOWN reversal
- Sweep below low → expect UP reversal
- NEVER place stop at obvious swing high/low

PREMIUM & DISCOUNT:
- Below 50% of range = Discount → look to BUY
- Above 50% of range = Premium → look to SELL
- Never buy premium, never sell discount

OTE (Optimal Trade Entry):
- 62%–79% Fibonacci retracement of the swing
- Best risk:reward zone for entries

═══════════════════════════════════════════════════════
SECTION 4 — CLASSICAL TECHNICAL ANALYSIS
═══════════════════════════════════════════════════════
EMAs (bias confirmation only, NOT entry signals):
- EMA 20 > EMA 50 > EMA 200 = strong bullish
- EMA 20 < EMA 50 < EMA 200 = strong bearish

RSI(14): >70 overbought | <30 oversold | divergence = trend weakness
ADX(14): >25 = trending (use trend strategies) | <20 = ranging

RULE: RSI divergence on 4H inside an OB = very high conviction.
RULE: Never trade breakouts when ADX < 20.

Key S/R: Round numbers, Prev week H/L, Prev day H/L, Month open.

═══════════════════════════════════════════════════════
SECTION 5 — FUNDAMENTAL ANALYSIS (Kathy Lien)
═══════════════════════════════════════════════════════
Interest rate differentials = strongest long-term FX driver.
- Hawkish central bank → currency strengthens
- Dovish central bank → currency weakens
- Rate diff > 1% → strong directional bias, do not fight it

HIGH-IMPACT EVENTS → No trades 30 min before/after:
NFP (1st Friday, 8:30AM EST) | CPI (monthly) | FOMC (2PM EST)
ECB/BOE rate decisions | GDP (quarterly) | Retail Sales

Intermarket:
- DXY rising → EUR/USD, GBP/USD, AUD/USD falling
- Gold rising → USD weakening (usually)
- Oil rising → CAD strengthening
- Risk-on → AUD/NZD/GBP strong, JPY/CHF weak
- Risk-off → JPY/CHF/USD strong, AUD/NZD weak

═══════════════════════════════════════════════════════
SECTION 6 — COT REPORT (CFTC — every Friday 3:30PM)
═══════════════════════════════════════════════════════
Commercial Traders (hedgers) = smart money.
- Commercials NET LONG at extremes = bullish signal
- Non-commercial at 52-week extreme = potential reversal
- Retail extremely one-sided → expect opposite

RULE: COT = macro bias tool, NOT entry timing.
RULE: Extreme COT + ICT setup on 4H = very high conviction.

═══════════════════════════════════════════════════════
SECTION 7 — PSYCHOLOGY RULES (Mark Douglas)
═══════════════════════════════════════════════════════
- An edge = higher probability, not certainty. Every trade can lose.
- Focus on process, not outcome.
- Never increase size to recover a loss.
- 60% win rate + 1:2 RR = profitable long-term.
- If confidence < 65% → NEUTRAL. Do not force trades.
- Last 3 trades on this pair were losses → reduce size 50%.

═══════════════════════════════════════════════════════
SECTION 8 — CONFLUENCE SCORING
═══════════════════════════════════════════════════════
Score each setup before trading:

TREND ALIGNMENT:
+15 Weekly + Daily + 4H all aligned
+10 Daily + 4H aligned (Weekly neutral)
+5  Only 4H shows direction

ICT:
+20 Valid Order Block (trend-aligned)
+15 Fair Value Gap at entry zone
+15 Liquidity sweep just occurred
+10 Price in Discount (buy) or Premium (sell)
+10 OTE Fibonacci zone (62-79%)

CLASSICAL TA:
+10 RSI divergence on 4H/1H
+10 ADX > 25
+5  EMA alignment

FUNDAMENTAL:
+15 Rate differential supports direction
+10 DXY confirms
+10 COT confirms
+5  No news in next 4 hours

THRESHOLDS:
85-100 → STRONG  (full size, 1% risk)
65-84  → MODERATE (half size, 0.5% risk)
45-64  → NEUTRAL (skip)
<45    → NO TRADE

═══════════════════════════════════════════════════════
SECTION 9 — HARD RISK RULES (NON-NEGOTIABLE)
═══════════════════════════════════════════════════════
- Max risk per trade: 1% of account equity
- Min Risk:Reward: 1:2 (below this = no trade)
- Stop loss: beyond liquidity level, not at it
- No trades 30 min before high-impact news
- Max 2 correlated pairs open simultaneously
- Confidence < 65% → NEUTRAL always
- No trades after 2 consecutive losses in same session

Lot size formula:
lot_size = (equity × risk%) / (stop_loss_pips × pip_value)

═══════════════════════════════════════════════════════
SECTION 10 — REQUIRED OUTPUT FORMAT (STRICT JSON)
═══════════════════════════════════════════════════════
Always respond in this exact JSON structure. No exceptions.
No markdown, no preamble. Pure JSON only.

{
  "timestamp": "ISO 8601",
  "pair": "EUR/USD",
  "timeframe": "4H",
  "session": "NY Kill Zone | London Kill Zone | London Close | Avoid",
  "macro_bias": {
    "weekly": "BULLISH|BEARISH|NEUTRAL",
    "daily": "BULLISH|BEARISH|NEUTRAL",
    "h4": "BULLISH|BEARISH|NEUTRAL",
    "alignment": "ALIGNED|MIXED|CONFLICTING"
  },
  "ict_analysis": {
    "order_block": {"present": true, "type": "BULLISH|BEARISH|NONE", "level": 0.0, "valid": true},
    "fair_value_gap": {"present": true, "type": "BULLISH|BEARISH|NONE", "upper": 0.0, "lower": 0.0},
    "liquidity": {"recent_sweep": true, "swept_level": 0.0, "direction": "SELL_SIDE|BUY_SIDE|NONE"},
    "premium_discount": "PREMIUM|DISCOUNT|EQUILIBRIUM",
    "ote_zone": [0.0, 0.0]
  },
  "technical_analysis": {
    "ema_bias": "BULLISH|BEARISH|NEUTRAL",
    "rsi_14": 0.0,
    "rsi_signal": "OVERSOLD|OVERBOUGHT|NEUTRAL|DIVERGENCE",
    "adx_14": 0.0,
    "market_regime": "TRENDING|RANGING|HIGH_VOLATILITY",
    "key_levels": {"resistance": [], "support": []}
  },
  "fundamental": {
    "rate_differential": "",
    "dxy_direction": "RISING|FALLING|NEUTRAL",
    "cot_bias": "BULLISH|BEARISH|NEUTRAL",
    "next_news_event": "",
    "news_risk": "HIGH|MEDIUM|LOW|CLEAR"
  },
  "confluence_score": 0,
  "signal_strength": "STRONG|MODERATE|WEAK|NEUTRAL",
  "signal": {
    "direction": "BUY|SELL|NEUTRAL",
    "confidence": 0,
    "entry_zone": [0.0, 0.0],
    "stop_loss": 0.0,
    "take_profit_1": 0.0,
    "take_profit_2": 0.0,
    "risk_reward": 0.0,
    "recommended_lot_size": 0.0,
    "order_type": "LIMIT|MARKET|STOP_LIMIT"
  },
  "reasoning": ["reason 1", "reason 2", "reason 3"],
  "key_risk": "",
  "knowledge_sources_used": [],
  "trade_management": {
    "tp1_action": "Close 50% at TP1, move SL to entry",
    "tp2_action": "Trail remaining 50% to TP2",
    "time_stop": "Close if -0.5R after 8 hours"
  },
  "do_not_trade_reason": null
}
"""


# =============================================================================
# OPTION 1 + 2 INTEGRATED AGENT RUNNER
# =============================================================================

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
        self.feedback_memory = []   # Rolling memory of last N trade feedbacks

        # Ensure log directory exists
        log_dir.mkdir(parents=True, exist_ok=True)

        logger.info("ForexAnalystAgent initialized")

    # -------------------------------------------------------------------------
    # MAIN ANALYSIS FUNCTION
    # -------------------------------------------------------------------------

    def analyze(self, market_data: dict) -> dict:
        """
        Full analysis pipeline:
        1. Retrieve relevant RAG knowledge
        2. Build complete prompt (Option 1 + Option 2 + live data)
        3. Call Claude API
        4. Parse and validate signal
        5. Log everything

        Args:
            market_data: dict with current price, indicators, fundamentals, portfolio

        Returns:
            Parsed trade signal dict
        """
        pair = market_data.get("pair", "EUR/USD")
        logger.info(f"Starting analysis: {pair}")

        # STEP 1: Retrieve relevant knowledge from RAG (Option 2)
        print(f"\n🔍 Retrieving knowledge from RAG for {pair}...")
        market_state = {
            "pair":         pair,
            "trend":        market_data.get("ohlcv", {}).get("h4_trend", "neutral").lower(),
            "regime":       market_data.get("indicators", {}).get("market_regime", "unknown").lower(),
            "next_event":   market_data.get("fundamental", {}).get("next_news_event", ""),
            "session":      market_data.get("fundamental", {}).get("active_session", ""),
        }

        retrieved_chunks = self.rag.search_for_trading_context(market_state)
        rag_context = self.rag.format_rag_context(
            retrieved_chunks,
            max_tokens=self.config.get("rag_context_tokens", 3000)
        )

        chunk_count = sum(len(v) for v in retrieved_chunks.values())
        print(f"  ✅ Retrieved {chunk_count} relevant knowledge chunks")

        # STEP 2: Build full user message (RAG context + live data)
        user_message = self._build_user_message(market_data, rag_context)

        # STEP 3: Call Claude API
        print(f"  🤖 Calling Claude API...")
        raw_response = self._call_claude(user_message)

        # STEP 4: Parse signal
        signal = self._parse_signal(raw_response, pair)

        # STEP 5: Validate signal against hard risk rules
        signal = self._validate_signal(signal, market_data)

        # STEP 6: Log everything
        self._log_analysis(pair, market_data, rag_context, signal, retrieved_chunks)

        runtime_issue = self._get_runtime_issue(signal)
        if runtime_issue:
            print(f"\n  ❌ Claude analysis failure: {runtime_issue}")
            print("  ⚠️  Using fallback neutral signal")

        direction = signal.get("signal", {}).get("direction", "NEUTRAL")
        confidence = signal.get("signal", {}).get("confidence", 0)
        score = signal.get("confluence_score", 0)
        label = "Fallback Signal" if runtime_issue else "Signal"
        print(f"\n  📊 {label}: {direction} | Confidence: {confidence}% | Score: {score}/100")

        return signal

    def _get_runtime_issue(self, signal: dict) -> str:
        """Returns a user-facing runtime failure message when analysis fell back."""
        if signal.get("error"):
            return str(signal["error"])

        reason = signal.get("do_not_trade_reason", "")
        if reason.startswith("API error"):
            return reason
        if reason.startswith("JSON parse error"):
            return reason
        return ""

    # -------------------------------------------------------------------------
    # PROMPT BUILDER — Combines RAG context + live market data
    # -------------------------------------------------------------------------

    def _build_user_message(self, market_data: dict, rag_context: str) -> str:
        """
        Builds the complete user message sent to Claude.
        Structure:
        1. RAG context (Option 2 — dynamic knowledge)
        2. Live market data (real-time context)
        3. Feedback memory (agent's own trade history)
        4. Analysis request
        """
        pair    = market_data.get("pair", "EUR/USD")
        ohlcv   = market_data.get("ohlcv", {})
        ind     = market_data.get("indicators", {})
        fund    = market_data.get("fundamental", {})
        port    = market_data.get("portfolio", {})

        # Feedback memory (last N trade reviews)
        feedback_section = ""
        if self.feedback_memory:
            recent = self.feedback_memory[-self.config.get("feedback_memory_limit", 15):]
            feedback_section = (
                "\n═══════════════════════════════════════════\n"
                "YOUR RECENT TRADE MEMORY (last outcomes):\n"
                "═══════════════════════════════════════════\n"
            )
            for fb in recent[-5:]:  # Show last 5 in prompt
                feedback_section += (
                    f"• {fb['date']} {fb['pair']} {fb['direction']} → "
                    f"{fb['outcome']} ({fb['pnl_r']}R): {fb['lesson']}\n"
                )

        return f"""
{rag_context}

{feedback_section}

═══════════════════════════════════════════
LIVE MARKET DATA — {pair}
Analysis Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}
═══════════════════════════════════════════

PRICE DATA:
Current Price:      {market_data.get('price')}
Today Open:         {ohlcv.get('day_open')}
Week Open:          {ohlcv.get('week_open')}
Month Open:         {ohlcv.get('month_open')}
Prev Day High/Low:  {ohlcv.get('prev_day_high')} / {ohlcv.get('prev_day_low')}
Prev Week High/Low: {ohlcv.get('prev_week_high')} / {ohlcv.get('prev_week_low')}

MULTI-TIMEFRAME STRUCTURE:
Weekly:  {ohlcv.get('weekly_structure')}  | Trend: {ohlcv.get('weekly_trend')}
Daily:   {ohlcv.get('daily_structure')}   | Trend: {ohlcv.get('daily_trend')}
4H:      {ohlcv.get('h4_structure')}      | Trend: {ohlcv.get('h4_trend')}
1H:      {ohlcv.get('h1_structure')}      | Trend: {ohlcv.get('h1_trend')}
15M:     {ohlcv.get('m15_structure')}     | Trend: {ohlcv.get('m15_trend')}

TECHNICAL INDICATORS:
EMA 20 (4H):        {ind.get('ema20_4h')}
EMA 50 (4H):        {ind.get('ema50_4h')}
EMA 200 (Daily):    {ind.get('ema200_daily')}
RSI 14 (4H):        {ind.get('rsi_4h')}
RSI 14 (1H):        {ind.get('rsi_1h')}
ADX 14 (4H):        {ind.get('adx_4h')}
ATR 14 (4H):        {ind.get('atr_4h')}
Market Regime:      {ind.get('market_regime')}

ICT LEVELS:
Nearest Bullish OB: {ind.get('bullish_ob')}
Nearest Bearish OB: {ind.get('bearish_ob')}
Bullish FVG:        {ind.get('bullish_fvg')}
Bearish FVG:        {ind.get('bearish_fvg')}
Recent Liq. Sweep:  {ind.get('recent_liquidity_sweep')}
Premium/Discount:   {ind.get('premium_discount_zone')}
OTE Zone (62-79%):  {ind.get('ote_zone')}

KEY LEVELS:
Resistance:         {ind.get('resistance_levels')}
Support:            {ind.get('support_levels')}
Round Numbers:      {ind.get('round_numbers')}

FUNDAMENTAL:
Fed Target Lower:   {fund.get('fed_target_lower_rate')}%
Fed Target Upper:   {fund.get('fed_target_upper_rate')}%
USD Midpoint Rate:  {fund.get('usd_rate')}%
ECB Deposit Rate:   {fund.get('ecb_deposit_rate', fund.get('pair_rate'))}%
ECB Main Refi:      {fund.get('ecb_main_refi_rate')}%
ECB Marginal Lend:  {fund.get('ecb_marginal_lending_rate')}%
Rate Differential:  {fund.get('rate_differential')}
DXY Direction:      {fund.get('dxy_direction')} @ {fund.get('dxy_level')}
COT Net Position:   {fund.get('cot_net')}
COT Bias:           {fund.get('cot_bias')}
Retail Sentiment:   {fund.get('retail_sentiment')}
Risk Sentiment:     {fund.get('risk_sentiment')}

NEWS & EVENTS:
Next News Event:    {fund.get('next_news_event', fund.get('next_event_name'))}
Time to Event:      {fund.get('time_to_event')}
News Risk:          {fund.get('news_risk')}
Recent Headline:    {fund.get('recent_headline')}
Active Session:     {fund.get('active_session')}
Kill Zone Active:   {fund.get('kill_zone_active')}
Trade Window:       {fund.get('trade_window_active')}

PORTFOLIO STATE:
Account Equity:     ${port.get('equity')}
Open Trades:        {port.get('open_trades')}
Open Risk:          {port.get('open_risk_pct')}%
Today PnL:          {port.get('daily_pnl_pct')}%
Trades Today:       {port.get('trades_today')}
USD Exposure:       {port.get('usd_exposure')}

═══════════════════════════════════════════
INSTRUCTIONS:
1. Review the knowledge base excerpts provided above
2. Apply your expertise to the live market data
3. Score the confluence using the scoring system
4. Output your trade signal in the required JSON format
5. Cite which knowledge sources informed your reasoning
═══════════════════════════════════════════
"""

    # -------------------------------------------------------------------------
    # CLAUDE API CALL
    # -------------------------------------------------------------------------

    def _call_claude(self, user_message: str) -> str:
        """Calls Claude API with Option 1 system prompt + Option 2 RAG context."""
        try:
            response = self.client.messages.create(
                model=self.config.get("model", "claude-sonnet-4-20250514"),
                max_tokens=self.config.get("max_tokens", 2000),
                temperature=0,
                system=FOREX_ANALYST_SYSTEM_PROMPT,  # Option 1 — always present
                messages=[
                    {"role": "user", "content": user_message}  # Option 2 + live data
                ]
            )
            return response.content[0].text

        except Exception as e:
            logger.error(f"Claude API call failed: {e}")
            return json.dumps({
                "error": str(e),
                "signal": {"direction": "NEUTRAL", "confidence": 0},
                "do_not_trade_reason": f"API error: {e}"
            })

    # -------------------------------------------------------------------------
    # SIGNAL PARSER
    # -------------------------------------------------------------------------

    def _parse_signal(self, raw_response: str, pair: str) -> dict:
        """Parses Claude's JSON response into a structured signal dict."""
        try:
            return json.loads(raw_response)
        except json.JSONDecodeError:
            # Try extracting JSON from markdown code blocks
            json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', raw_response, re.DOTALL)
            if json_match:
                try:
                    return json.loads(json_match.group(1))
                except Exception:
                    pass

            # Try finding raw JSON object
            json_match = re.search(r'\{.*\}', raw_response, re.DOTALL)
            if json_match:
                try:
                    return json.loads(json_match.group())
                except Exception:
                    pass

            logger.error(f"Failed to parse signal JSON for {pair}")
            return {
                "pair": pair,
                "timestamp": datetime.utcnow().isoformat(),
                "signal": {"direction": "NEUTRAL", "confidence": 0},
                "confluence_score": 0,
                "signal_strength": "NEUTRAL",
                "do_not_trade_reason": "JSON parse error — raw response logged",
                "raw_response": raw_response[:500]
            }

    # -------------------------------------------------------------------------
    # SIGNAL VALIDATOR — Hard-coded safety rules (no AI can override these)
    # -------------------------------------------------------------------------

    def _validate_signal(self, signal: dict, market_data: dict) -> dict:
        """
        Code-level safety validation. These rules CANNOT be overridden by
        the AI — they are pure Python logic. This is your Layer 3 safety net.
        """
        port    = market_data.get("portfolio", {})
        fund    = market_data.get("fundamental", {})
        sig     = signal.get("signal", {})
        overrides = []

        runtime_issue = self._get_runtime_issue(signal)
        if runtime_issue:
            sig["direction"] = "NEUTRAL"
            if signal.get("error"):
                overrides.append("BLOCKED: Claude API unavailable")
            else:
                overrides.append("BLOCKED: Claude response parsing failed")
            if market_data.get("demo_mode", True):
                signal["demo_mode"] = True
            signal["validator_overrides"] = overrides
            signal["signal"] = sig
            logger.warning(f"Signal overridden: {overrides}")
            return signal

        # Rule 0: Kill zones only
        session = fund.get("active_session", signal.get("session", ""))
        if not self._is_allowed_session(session):
            sig["direction"] = "NEUTRAL"
            overrides.append(f"BLOCKED: Outside allowed kill zones ({session})")

        # Rule 1: Daily loss limit
        daily_pnl = port.get("daily_pnl_pct", 0)
        if daily_pnl <= -2.0:
            sig["direction"] = "NEUTRAL"
            overrides.append(f"BLOCKED: Daily loss limit reached ({daily_pnl}%)")

        # Rule 2: Portfolio heat
        open_risk = port.get("open_risk_pct", 0)
        if open_risk >= 3.0:
            sig["direction"] = "NEUTRAL"
            overrides.append(f"BLOCKED: Max portfolio risk reached ({open_risk}%)")

        # Rule 3: No trading after 2 consecutive losses in the same session
        if self._has_session_loss_streak(session):
            sig["direction"] = "NEUTRAL"
            overrides.append(f"BLOCKED: Two consecutive losses already recorded in {session}")

        # Rule 4: News blackout
        next_event = fund.get("next_news_event", fund.get("next_event_name", ""))
        if next_event.startswith("MANUAL_CHECK"):
            sig["direction"] = "NEUTRAL"
            overrides.append("BLOCKED: Live economic calendar unavailable")
        time_to_event = fund.get("time_to_event", "")
        if self._is_within_news_blackout(time_to_event):
            sig["direction"] = "NEUTRAL"
            overrides.append(f"BLOCKED: News blackout active ({time_to_event} to event)")

        # Rule 5: Minimum confidence
        confidence = sig.get("confidence", 0)
        if confidence < self.config.get("min_confidence", 65):
            sig["direction"] = "NEUTRAL"
            overrides.append(f"BLOCKED: Confidence too low ({confidence}%)")

        # Rule 6: Minimum risk:reward
        rr = sig.get("risk_reward", 0)
        if rr > 0 and rr < 2.0:
            sig["direction"] = "NEUTRAL"
            overrides.append(f"BLOCKED: R:R too low ({rr} < 2.0 minimum)")

        # Rule 7: Demo mode warning
        if market_data.get("demo_mode", True):
            signal["demo_mode"] = True

        if overrides:
            signal["validator_overrides"] = overrides
            signal["signal"] = sig
            logger.warning(f"Signal overridden: {overrides}")

        return signal

    def _is_within_news_blackout(self, time_to_event) -> bool:
        """Returns True if within 30 minutes of high-impact news.
        Returns False when time_to_event is None or unparseable
        (no live news feed connected — caller should check manually)."""
        if time_to_event is None or not time_to_event:
            return False
        # Parse formats like "25 minutes", "1 hour 5 minutes", etc.
        try:
            lowered = time_to_event.lower()
            mins_match = re.search(r'(\d+)\s*min', lowered)
            hours_match = re.search(r'(\d+)\s*hour', lowered)
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

    def _is_allowed_session(self, session: str) -> bool:
        return session in {"London Kill Zone", "NY Kill Zone", "London Close"}

    def _has_session_loss_streak(self, session: str, limit: int = 2) -> bool:
        if not session:
            return False

        streak = 0
        for item in reversed(self.feedback_memory):
            if item.get("session") != session:
                continue
            if item.get("outcome") == "LOSS":
                streak += 1
                if streak >= limit:
                    return True
            else:
                return False
        return False

    # -------------------------------------------------------------------------
    # FEEDBACK LOOP — Agent learns from outcomes
    # -------------------------------------------------------------------------

    def record_trade_outcome(self, trade_record: dict):
        """
        Called after a trade closes. Stores outcome in:
        1. In-memory feedback (injected into next analysis)
        2. RAG feedback store (searchable long-term memory)
        3. Log file (permanent record)
        """
        pair      = trade_record.get("pair", "")
        direction = trade_record.get("direction", "")
        outcome   = trade_record.get("outcome", "")
        pnl_r     = trade_record.get("pnl_r", 0)
        date      = trade_record.get("date", datetime.utcnow().strftime("%Y-%m-%d"))

        # Build feedback text for RAG ingestion
        feedback_text = self._generate_feedback_text(trade_record)

        # Store in RAG (long-term searchable memory)
        self.rag.store_feedback(feedback_text, date, pair)

        # Add to in-memory rolling feedback
        self.feedback_memory.append({
            "date":      date,
            "pair":      pair,
            "direction": direction,
            "outcome":   outcome,
            "pnl_r":     pnl_r,
            "session":   trade_record.get("session", ""),
            "lesson":    self._extract_lesson(trade_record),
        })

        # Keep only last N entries in memory
        limit = self.config.get("feedback_memory_limit", 15)
        if len(self.feedback_memory) > limit:
            self.feedback_memory = self.feedback_memory[-limit:]

        # Log to file
        self._log_trade_outcome(trade_record)
        logger.info(f"Trade outcome recorded: {pair} {direction} → {outcome} ({pnl_r}R)")

    def _generate_feedback_text(self, trade_record: dict) -> str:
        """Creates a detailed feedback document for RAG storage."""
        return f"""
TRADE REVIEW — {trade_record.get('pair')} {trade_record.get('direction')}
Date: {trade_record.get('date')}
Outcome: {trade_record.get('outcome')} | PnL: {trade_record.get('pnl_r')}R
Session: {trade_record.get('session')}
Duration: {trade_record.get('duration_hours')} hours

ENTRY DETAILS:
Entry: {trade_record.get('entry_price')}
Stop Loss: {trade_record.get('stop_loss')}
Take Profit: {trade_record.get('take_profit')}
Lot Size: {trade_record.get('lot_size')}
Confluence Score: {trade_record.get('confluence_score')}

ORIGINAL REASONING:
{trade_record.get('original_reasoning', 'Not recorded')}

WHAT ACTUALLY HAPPENED:
{trade_record.get('price_action_summary', 'Not recorded')}

NEWS EVENTS THAT AFFECTED IT:
{trade_record.get('relevant_events', 'None noted')}

LESSON LEARNED:
{trade_record.get('lesson', 'Not recorded')}

WHAT TO DO DIFFERENTLY ON {trade_record.get('pair')} NEXT TIME:
{trade_record.get('improvement', 'Not recorded')}
"""

    def _extract_lesson(self, trade_record: dict) -> str:
        """Extracts a short lesson from the trade record."""
        lesson = trade_record.get("lesson", "")
        if not lesson:
            outcome = trade_record.get("outcome", "")
            pnl_r   = trade_record.get("pnl_r", 0)
            if outcome == "WIN":
                lesson = f"Setup worked. R:R achieved: {pnl_r}"
            elif outcome == "LOSS":
                lesson = f"Setup failed. Check entry criteria."
            else:
                lesson = f"Breakeven/partial. Monitor similar setups."
        return lesson[:150]

    # -------------------------------------------------------------------------
    # LOGGING
    # -------------------------------------------------------------------------

    def _log_analysis(
        self,
        pair: str,
        market_data: dict,
        rag_context: str,
        signal: dict,
        retrieved_chunks: dict
    ):
        """Logs the full analysis to JSONL file."""
        log_entry = {
            "timestamp":        datetime.utcnow().isoformat(),
            "pair":             pair,
            "price":            market_data.get("price"),
            "signal":           signal.get("signal", {}),
            "confluence_score": signal.get("confluence_score", 0),
            "signal_strength":  signal.get("signal_strength", "NEUTRAL"),
            "reasoning":        signal.get("reasoning", []),
            "key_risk":         signal.get("key_risk", ""),
            "overrides":        signal.get("validator_overrides", []),
            "rag_chunks_used":  sum(len(v) for v in retrieved_chunks.values()),
            "rag_categories":   list(retrieved_chunks.keys()),
            "knowledge_sources":signal.get("knowledge_sources_used", []),
        }

        log_file = self.log_dir / "agent_decisions.jsonl"
        with open(log_file, "a") as f:
            f.write(json.dumps(log_entry) + "\n")

    def _log_trade_outcome(self, trade_record: dict):
        """Logs trade outcome to CSV."""
        import csv
        csv_file = self.log_dir / "trades.csv"
        file_exists = csv_file.exists()

        fields = [
            "date", "pair", "direction", "entry_price", "exit_price",
            "stop_loss", "take_profit", "lot_size", "outcome",
            "pnl_r", "pnl_usd", "duration_hours", "session",
            "confluence_score", "lesson"
        ]

        with open(csv_file, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            if not file_exists:
                writer.writeheader()
            writer.writerow(trade_record)


# =============================================================================
# STANDALONE TEST
# =============================================================================

if __name__ == "__main__":
    print("ForexAnalystAgent module loaded.")
    print("System prompt length:", len(FOREX_ANALYST_SYSTEM_PROMPT), "characters")
    print("\nThis module integrates:")
    print("  Option 1: Deep system prompt (permanent identity + rules)")
    print("  Option 2: RAG pipeline (dynamic knowledge retrieval)")
    print("\nImport and use ForexAnalystAgent in main.py to run full analysis.")
