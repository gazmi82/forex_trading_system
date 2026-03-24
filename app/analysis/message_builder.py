from __future__ import annotations

from datetime import datetime


def build_analysis_user_message(
    market_data: dict,
    rag_context: str,
    feedback_section: str,
) -> str:
    pair = market_data.get("pair", "EUR/USD")
    ohlcv = market_data.get("ohlcv", {})
    ind = market_data.get("indicators", {})
    fund = market_data.get("fundamental", {})
    port = market_data.get("portfolio", {})

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
