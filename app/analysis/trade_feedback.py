from __future__ import annotations

import csv
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.config import FEEDBACK_DIR
from app.core.text_utils import display_pair, slugify_text

logger = logging.getLogger(__name__)


class TradeFeedbackManager:
    def __init__(self, rag_pipeline, config: dict, log_dir: Path, feedback_dir: Path | None = None):
        self.rag = rag_pipeline
        self.config = config
        self.log_dir = Path(log_dir)
        self.feedback_dir = feedback_dir or FEEDBACK_DIR
        self.feedback_memory: list[dict[str, Any]] = []
        self.feedback_dir.mkdir(parents=True, exist_ok=True)

    def render_memory_section(self) -> str:
        if not self.feedback_memory:
            return ""

        recent = self.feedback_memory[-self.config.get("feedback_memory_limit", 15):]
        lines = [
            "",
            "═══════════════════════════════════════════",
            "YOUR RECENT TRADE MEMORY (last outcomes):",
            "═══════════════════════════════════════════",
        ]
        for feedback in recent[-5:]:
            lines.append(
                f"• {feedback['date']} {feedback['pair']} {feedback['direction']} → "
                f"{feedback['outcome']} ({feedback['pnl_r']}R): {feedback['lesson']}"
            )
        return "\n".join(lines)

    def has_session_loss_streak(self, session: str, limit: int = 2) -> bool:
        # Check in-memory feedback (populated during the current runtime session)
        session_seen = 0
        streak = 0
        for item in reversed(self.feedback_memory):
            if item.get("session") != session:
                continue
            session_seen += 1
            if item.get("outcome") == "LOSS":
                streak += 1
                if streak >= limit:
                    return True
            else:
                return False  # win in this session breaks the streak

        # If no trades for this session exist in memory (e.g. after a restart),
        # fall back to the persistent closed_trades.jsonl file so the rule survives
        # process restarts and stays consistent with the TradeJournal implementation.
        if session_seen == 0:
            closed_file = self.log_dir / "closed_trades.jsonl"
            if not closed_file.exists():
                return False
            try:
                closed_rows = []
                with open(closed_file) as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        row = json.loads(line)
                        if row.get("session") == session:
                            closed_rows.append(row)
            except Exception:
                return False
            if len(closed_rows) < limit:
                return False
            recent = closed_rows[-limit:]
            return all(row.get("outcome") == "LOSS" for row in recent)

        return False

    def record_trade_outcome(self, trade_record: dict):
        trade_record = self._enrich_trade_record(dict(trade_record))
        pair = trade_record.get("pair", "")
        direction = trade_record.get("direction", "")
        outcome = trade_record.get("outcome", "")
        pnl_r = trade_record.get("pnl_r", 0)
        date = trade_record.get("date", datetime.utcnow().strftime("%Y-%m-%d"))
        lesson = self._extract_lesson(trade_record)
        trade_record["lesson"] = lesson

        feedback_text = self._generate_feedback_text(trade_record)
        self.rag.store_feedback(feedback_text, date, pair)

        self.feedback_memory.append(
            {
                "date": date,
                "pair": pair,
                "direction": direction,
                "outcome": outcome,
                "pnl_r": pnl_r,
                "session": trade_record.get("session", ""),
                "lesson": lesson,
            }
        )

        limit = self.config.get("feedback_memory_limit", 15)
        if len(self.feedback_memory) > limit:
            del self.feedback_memory[:-limit]

        feedback_file = self._write_feedback_markdown(trade_record, feedback_text)
        self._log_trade_outcome(trade_record)
        logger.info(
            f"Trade outcome recorded: {pair} {direction} → {outcome} ({pnl_r}R) | "
            f"Feedback note: {feedback_file.name}"
        )

    @staticmethod
    def _missing_reasons(trade_record: dict) -> list[str]:
        reasons = trade_record.get("missing_detail_reasons", [])
        if isinstance(reasons, list):
            return [str(item).strip() for item in reasons if str(item).strip()]
        if isinstance(reasons, str) and reasons.strip():
            return [reasons.strip()]
        return []

    def _append_missing_reason(self, trade_record: dict, reason: str):
        if not reason:
            return
        reasons = self._missing_reasons(trade_record)
        if reason not in reasons:
            reasons.append(reason)
        trade_record["missing_detail_reasons"] = reasons

    def _hydrate_trade_record_from_signal_log(self, trade_record: dict):
        filename = str(trade_record.get("signal_log_filename") or "").strip()
        if not filename:
            self._append_missing_reason(
                trade_record,
                "No linked signal log filename was saved with this trade, so the review could not reload the exact entry analysis JSON.",
            )
            return trade_record

        signal_path = self.log_dir / filename
        if not signal_path.exists():
            self._append_missing_reason(
                trade_record,
                f"Linked signal log '{filename}' was not found in the runtime log directory, so some entry-time context could not be reloaded.",
            )
            return trade_record

        try:
            with open(signal_path, encoding="utf-8") as f:
                signal_data = json.load(f)
        except Exception as exc:
            self._append_missing_reason(
                trade_record,
                f"Linked signal log '{filename}' could not be parsed ({exc}), so fallback narrative data was unavailable.",
            )
            return trade_record

        field_map = {
            "signal_timestamp": "timestamp",
            "signal_strength": "signal_strength",
            "macro_bias": "macro_bias",
            "technical_analysis": "technical_analysis",
            "ict_analysis": "ict_analysis",
            "fundamental_context": "fundamental",
            "reasoning": "reasoning",
            "key_risk": "key_risk",
            "knowledge_sources": "knowledge_sources_used",
            "trade_management_plan": "trade_management",
            "validator_overrides": "validator_overrides",
            "session": "session",
            "pair": "pair",
        }

        for target, source in field_map.items():
            current = trade_record.get(target)
            if current in ("", None, [], {}):
                value = signal_data.get(source)
                if value not in ("", None, [], {}):
                    trade_record[target] = value

        return trade_record

    @staticmethod
    def _format_macro_context(macro_bias: dict) -> str:
        if not isinstance(macro_bias, dict) or not macro_bias:
            return ""
        weekly = macro_bias.get("weekly", "UNKNOWN")
        daily = macro_bias.get("daily", "UNKNOWN")
        h4 = macro_bias.get("h4", "UNKNOWN")
        alignment = macro_bias.get("alignment", "UNKNOWN")
        return (
            f"Macro bias at entry: Weekly {weekly}, Daily {daily}, 4H {h4}, "
            f"alignment {alignment}."
        )

    @staticmethod
    def _format_fundamental_context(fundamental: dict) -> str:
        if not isinstance(fundamental, dict) or not fundamental:
            return ""
        details = []
        if fundamental.get("rate_differential"):
            details.append(f"rate differential {fundamental['rate_differential']}")
        if fundamental.get("dxy_direction"):
            details.append(f"DXY {fundamental['dxy_direction']}")
        if fundamental.get("cot_bias"):
            details.append(f"COT {fundamental['cot_bias']}")
        if fundamental.get("next_news_event"):
            risk = fundamental.get("news_risk", "UNKNOWN")
            details.append(f"next event '{fundamental['next_news_event']}' (risk {risk})")
        elif fundamental.get("news_risk"):
            details.append(f"news risk {fundamental['news_risk']}")
        return f"Fundamental snapshot at entry: {'; '.join(details)}." if details else ""

    @staticmethod
    def _format_technical_context(technical: dict) -> str:
        if not isinstance(technical, dict) or not technical:
            return ""
        regime = technical.get("market_regime")
        ema_bias = technical.get("ema_bias")
        adx = technical.get("adx_14")
        rsi = technical.get("rsi_14")
        rsi_signal = technical.get("rsi_signal")
        bits = []
        if regime:
            bits.append(f"market regime {regime}")
        if ema_bias:
            bits.append(f"EMA bias {ema_bias}")
        if adx not in (None, ""):
            bits.append(f"ADX {adx}")
        if rsi not in (None, ""):
            bits.append(f"RSI {rsi} ({rsi_signal or 'NO_SIGNAL'})")
        return f"Technical backdrop at entry: {'; '.join(bits)}." if bits else ""

    @staticmethod
    def _format_ict_context(ict_analysis: dict) -> str:
        if not isinstance(ict_analysis, dict) or not ict_analysis:
            return ""
        bits = []
        order_block = ict_analysis.get("order_block") or {}
        if order_block.get("present"):
            bits.append(
                f"{order_block.get('type', 'UNKNOWN')} order block near {order_block.get('level', 'N/A')}"
            )
        fvg = ict_analysis.get("fair_value_gap") or {}
        if fvg.get("present"):
            bits.append(
                f"{fvg.get('type', 'UNKNOWN')} fair value gap {fvg.get('lower', 'N/A')}-{fvg.get('upper', 'N/A')}"
            )
        liquidity = ict_analysis.get("liquidity") or {}
        if liquidity.get("recent_sweep"):
            bits.append(
                f"recent {liquidity.get('direction', 'UNKNOWN')} liquidity sweep at {liquidity.get('swept_level', 'N/A')}"
            )
        if ict_analysis.get("premium_discount"):
            bits.append(f"premium/discount state {ict_analysis['premium_discount']}")
        return f"ICT context at entry: {'; '.join(bits)}." if bits else ""

    def _build_original_reasoning(self, trade_record: dict) -> str:
        parts = []
        reasoning = trade_record.get("reasoning", [])
        if isinstance(reasoning, list) and reasoning:
            bullet_lines = "\n".join(f"- {item}" for item in reasoning if str(item).strip())
            parts.append("Saved entry thesis:\n" + bullet_lines)
        else:
            reasons = self._missing_reasons(trade_record)
            reason_text = reasons[0] if reasons else (
                "The trade record did not preserve reasoning bullets, so the exact thesis cannot be reconstructed word-for-word."
            )
            parts.append(f"Exact entry reasoning was not available. Reason: {reason_text}")

        for formatter, field_name in (
            (self._format_macro_context, "macro_bias"),
            (self._format_fundamental_context, "fundamental_context"),
            (self._format_technical_context, "technical_analysis"),
            (self._format_ict_context, "ict_analysis"),
        ):
            text = formatter(trade_record.get(field_name, {}))
            if text:
                parts.append(text)

        if trade_record.get("key_risk"):
            parts.append(f"Primary risk flagged at entry: {trade_record['key_risk']}.")

        knowledge_sources = trade_record.get("knowledge_sources", [])
        if isinstance(knowledge_sources, list) and knowledge_sources:
            parts.append(
                "Knowledge sources referenced by the model: "
                + "; ".join(str(item) for item in knowledge_sources if str(item).strip())
                + "."
            )

        if trade_record.get("signal_strength"):
            parts.append(f"Signal strength at entry: {trade_record['signal_strength']}.")

        return "\n\n".join(parts)

    def _build_price_action_summary(self, trade_record: dict) -> str:
        pair_value = display_pair(trade_record.get("pair"))
        parts = [
            f"The {trade_record.get('direction', 'UNKNOWN')} {pair_value} trade was held for "
            f"{trade_record.get('duration_hours', 'unknown')} hours in the "
            f"{trade_record.get('session', 'UNKNOWN')} session."
        ]

        if trade_record.get("tp1_hit"):
            tp1_price = trade_record.get("tp1_fill_price", "unknown")
            tp1_units = trade_record.get("tp1_closed_units", "unknown")
            partial_pnl = trade_record.get("partial_realized_pnl_usd", 0)
            parts.append(
                f"TP1 was recorded at {tp1_price}, closing {tp1_units} units and moving the stop loss to entry. "
                f"The recorded partial realized PnL from that event was approximately ${partial_pnl}."
            )

        close_reason = trade_record.get("close_reason", "")
        if close_reason == "TIME_STOP":
            parts.append(
                "The remaining position was closed by the executor's time-stop logic because the trade stayed open too long "
                "without recovering enough relative to risk."
            )
        elif close_reason == "CLOSED_BY_OANDA":
            parts.append(
                "The remaining position disappeared from OANDA's open-trades list, so a broker-managed protective order closed it."
            )
            if trade_record.get("tp1_hit"):
                parts.append(
                    "Because TP1 had already been hit and the stop was moved to entry, the remainder may have been stopped at breakeven, "
                    "but the system cannot confirm that without the broker close transaction."
                )
            else:
                parts.append(
                    "Without the broker close transaction, the review cannot tell whether that external close was the original stop loss, "
                    "the take-profit order, or a manual intervention outside the executor."
                )

        if trade_record.get("pnl_is_partial_estimate"):
            parts.append(
                f"Known realized PnL from recorded components is ${trade_record.get('pnl_usd', 0)} "
                f"({trade_record.get('pnl_r', 0)}R), but that figure is incomplete."
            )
            if trade_record.get("pnl_missing_reason"):
                parts.append(f"Why incomplete: {trade_record['pnl_missing_reason']}")
        else:
            parts.append(
                f"Recorded total realized PnL was ${trade_record.get('pnl_usd', 0)} "
                f"({trade_record.get('pnl_r', 0)}R)."
            )

        return "\n\n".join(parts)

    def _build_relevant_events(self, trade_record: dict) -> str:
        fundamental = trade_record.get("fundamental_context", {})
        parts = []

        if isinstance(fundamental, dict) and fundamental:
            next_event = fundamental.get("next_news_event", "")
            news_risk = fundamental.get("news_risk", "")
            if next_event:
                parts.append(
                    f"The saved entry snapshot explicitly recorded the next calendar event as '{next_event}' "
                    f"with news risk '{news_risk or 'UNKNOWN'}'."
                )
                parts.append(
                    "That means the system did not completely miss the calendar at entry; the event was visible in the saved context."
                )
            elif news_risk:
                parts.append(
                    f"A news-risk label of '{news_risk}' was saved at entry, but no specific event name was preserved."
                )
            else:
                parts.append(
                    "No specific event was named in the saved fundamental snapshot, so there is no concrete scheduled-news driver to attribute here."
                )
        else:
            parts.append(
                "No entry-time calendar snapshot was preserved, so this review cannot confirm whether a news event was absent, ignored, or simply not recorded."
            )

        if trade_record.get("key_risk"):
            parts.append(f"Key risk recorded at entry: {trade_record['key_risk']}.")

        return "\n\n".join(parts)

    def _build_improvement(self, trade_record: dict) -> str:
        improvements = []
        macro_bias = trade_record.get("macro_bias", {})
        alignment = ""
        if isinstance(macro_bias, dict):
            alignment = str(macro_bias.get("alignment", "")).upper()
        news_risk = ""
        fundamental = trade_record.get("fundamental_context", {})
        if isinstance(fundamental, dict):
            news_risk = str(fundamental.get("news_risk", "")).upper()

        if alignment == "CONFLICTING":
            improvements.append(
                "Reduce size or skip similar EUR/USD setups when weekly, daily, and 4H structure are still conflicting; demand a cleaner directional alignment."
            )

        if news_risk in {"MEDIUM", "HIGH"}:
            improvements.append(
                "Before the next EUR/USD entry, re-check the economic calendar immediately before execution and document whether the setup still has enough edge through the upcoming event."
            )

        if trade_record.get("pnl_is_partial_estimate"):
            improvements.append(
                "Capture the broker close transaction for OANDA-managed exits so post-trade reviews can distinguish stop-loss, take-profit, and breakeven outcomes instead of relying on partial estimates."
            )

        if not trade_record.get("reasoning"):
            improvements.append(
                "Persist the original reasoning bullets with the trade ticket every time; otherwise you cannot tell whether the trade failed because of bad analysis, bad timing, or missing event awareness."
            )

        if trade_record.get("tp1_hit"):
            improvements.append(
                "After TP1 events, keep tracking the remainder explicitly so the review can tell whether the runner was managed well or given back unnecessarily."
            )

        if not improvements:
            improvements.append(
                "Keep the same process, but continue validating session quality, event timing, and whether the entry still matches the saved thesis immediately before execution."
            )

        return "\n".join(f"- {item}" for item in improvements)

    def _build_missing_details_section(self, trade_record: dict) -> str:
        reasons = self._missing_reasons(trade_record)
        if not reasons:
            return "No major review-data gaps were detected for this trade."
        return "\n".join(f"- {reason}" for reason in reasons)

    def _enrich_trade_record(self, trade_record: dict) -> dict:
        trade_record = self._hydrate_trade_record_from_signal_log(trade_record)

        if not trade_record.get("reasoning"):
            self._append_missing_reason(
                trade_record,
                "The trade review does not have saved reasoning bullets for this entry, so the precise pre-trade thesis cannot be reconstructed fully.",
            )

        if not trade_record.get("fundamental_context"):
            self._append_missing_reason(
                trade_record,
                "No entry-time fundamental context was available, so calendar/news attribution remains incomplete.",
            )

        if trade_record.get("pnl_is_partial_estimate") and trade_record.get("pnl_missing_reason"):
            self._append_missing_reason(trade_record, trade_record["pnl_missing_reason"])

        trade_record["original_reasoning"] = self._build_original_reasoning(trade_record)
        trade_record["price_action_summary"] = self._build_price_action_summary(trade_record)
        trade_record["relevant_events"] = self._build_relevant_events(trade_record)
        trade_record["lesson"] = self._extract_lesson(trade_record)
        trade_record["improvement"] = self._build_improvement(trade_record)
        trade_record["data_gaps_summary"] = self._build_missing_details_section(trade_record)

        return trade_record

    @staticmethod
    def _fmt_bool(value) -> str:
        if value is None:
            return "N/A (concept not present at entry)"
        return "Yes" if value else "No"

    def _generate_feedback_text(self, trade_record: dict) -> str:
        pair_value  = display_pair(trade_record.get("pair"))
        ict_ph      = trade_record.get("ict_post_hoc") or {}
        tags        = trade_record.get("pattern_tags") or []
        tags_str    = ", ".join(tags) if tags else "none"

        return f"""
TRADE REVIEW — {pair_value} {trade_record.get('direction')}
Date: {trade_record.get('date')}
Outcome: {trade_record.get('outcome')} | PnL: {trade_record.get('pnl_r')}R
Session: {trade_record.get('session')}
Duration: {trade_record.get('duration_hours')} hours

SETUP ANALYSIS (process-based):
Setup Grade:    {trade_record.get('setup_grade', 'N/A')}  (A=full confluence, B=good, C=marginal, F=rule violation)
Entry Timing:   {trade_record.get('entry_timing', 'N/A')}  (OPTIMAL/ACCEPTABLE/AT_EDGE/OUTSIDE_ZONE)
Root Cause:     {trade_record.get('root_cause', 'N/A')}
Pattern Tags:   {tags_str}

ICT POST-HOC EVALUATION:
Order Block Held:          {self._fmt_bool(ict_ph.get('ob_held'))}
FVG Acted as Magnet:       {self._fmt_bool(ict_ph.get('fvg_acted_as_magnet'))}
Sweep Led to Reversal:     {self._fmt_bool(ict_ph.get('sweep_led_to_reversal'))}
P/D Zone Respected:        {self._fmt_bool(ict_ph.get('pd_zone_respected'))}

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

WHAT TO DO DIFFERENTLY ON {pair_value} NEXT TIME:
{trade_record.get('improvement', 'Not recorded')}

DATA GAPS / WHY SOME DETAILS ARE MISSING:
{trade_record.get('data_gaps_summary', 'No major review-data gaps were detected for this trade.')}
"""

    def _write_feedback_markdown(self, trade_record: dict, feedback_text: str) -> Path:
        timestamp = datetime.utcnow()
        timestamp_slug = timestamp.strftime("%Y%m%d_%H%M%S")
        pair_slug = slugify_text(trade_record.get("pair", "eur-usd"))
        outcome_slug = slugify_text(trade_record.get("outcome", "unknown"))
        session_slug = slugify_text(trade_record.get("session", "unknown-session"))
        filename = f"feedback_{timestamp_slug}_{pair_slug}_{session_slug}_{outcome_slug}.md"
        output_path = self.feedback_dir / filename
        pair_value = display_pair(trade_record.get("pair"))

        ict_ph   = trade_record.get("ict_post_hoc") or {}
        tags     = trade_record.get("pattern_tags") or []
        tags_str = ", ".join(f"`{t}`" for t in tags) if tags else "none"

        markdown = (
            f"# Trade Review — {pair_value} {trade_record.get('direction', '')}\n\n"
            f"| Field | Value |\n"
            f"|-------|-------|\n"
            f"| Logged At (UTC) | {timestamp.isoformat()}Z |\n"
            f"| Trade Date | {trade_record.get('date', '')} |\n"
            f"| Outcome | {trade_record.get('outcome', '')} |\n"
            f"| Session | {trade_record.get('session', '')} |\n"
            f"| PnL (R) | {trade_record.get('pnl_r', '')} |\n"
            f"| PnL (USD) | {trade_record.get('pnl_usd', '')} |\n"
            f"| Duration (hours) | {trade_record.get('duration_hours', '')} |\n"
            f"| Confluence Score | {trade_record.get('confluence_score', '')} |\n\n"
            f"## Setup Analysis\n\n"
            f"| Dimension | Value |\n"
            f"|-----------|-------|\n"
            f"| Setup Grade | {trade_record.get('setup_grade', 'N/A')} |\n"
            f"| Entry Timing | {trade_record.get('entry_timing', 'N/A')} |\n"
            f"| Root Cause | {trade_record.get('root_cause', 'N/A')} |\n"
            f"| Pattern Tags | {tags_str} |\n\n"
            f"## ICT Post-Hoc\n\n"
            f"| Concept | Played Out? |\n"
            f"|---------|-------------|\n"
            f"| Order Block Held | {self._fmt_bool(ict_ph.get('ob_held'))} |\n"
            f"| FVG Acted as Magnet | {self._fmt_bool(ict_ph.get('fvg_acted_as_magnet'))} |\n"
            f"| Sweep Led to Reversal | {self._fmt_bool(ict_ph.get('sweep_led_to_reversal'))} |\n"
            f"| P/D Zone Respected | {self._fmt_bool(ict_ph.get('pd_zone_respected'))} |\n\n"
            f"## Lesson\n\n"
            f"{trade_record.get('lesson', 'Not recorded')}\n\n"
            f"## Data Gaps\n\n"
            f"{trade_record.get('data_gaps_summary', 'No major review-data gaps were detected for this trade.')}\n\n"
            f"## Full Review\n\n"
            f"```\n{feedback_text.strip()}\n```\n"
        )

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(markdown)

        return output_path

    def _extract_lesson(self, trade_record: dict) -> str:
        # Prefer pre-generated haiku lesson (already in trade_record["lesson"] if agent ran it)
        lesson = trade_record.get("lesson", "")
        if lesson:
            return lesson[:300]

        # Root-cause-specific fallbacks (more useful than generic outcome messages)
        root_cause = trade_record.get("root_cause", "")
        outcome    = trade_record.get("outcome", "")
        pnl_r      = trade_record.get("pnl_r", 0)

        _rc = {
            "RULE_VIOLATION":                "A validator override fired — do not take trades when any hard rule is blocked.",
            "NEWS_INTERFERENCE":             "High/medium news risk was present; reduce size or skip when a major event is near.",
            "WRONG_MTF_READ":                "Multi-timeframe alignment was MIXED or CONFLICTING; wait for a cleaner directional agreement before entering.",
            "ENTRY_TIMING_COST":             "Time stop fired before the thesis played out; consider whether the entry was too early in the session.",
            "CORRECT_PROCESS_CORRECT_OUTCOME": f"Setup grade was solid and it worked ({pnl_r}R). Replicate the same conditions.",
            "CORRECT_PROCESS_ADVERSE_OUTCOME": f"The process was sound but the trade lost ({pnl_r}R). Accept the outcome — re-evaluate only if a new pattern emerges across several such trades.",
            "MARGINAL_SETUP_GOT_LUCKY":      "A marginal setup produced a win; avoid treating this as confirmation of a weak edge.",
            "MARGINAL_SETUP_POOR_OUTCOME":   "Setup was marginal at entry; raise the standard before the next similar trigger.",
        }
        if root_cause in _rc:
            return _rc[root_cause]

        # Generic outcome fallback
        if outcome == "WIN":
            return f"Setup worked ({pnl_r}R). Preserve the conditions that supported the trade."
        if outcome == "LOSS":
            return "Setup failed. Re-check whether entry thesis, timing, and event context were aligned."
        if outcome == "PARTIAL_WIN":
            return "Banked TP1 but full runner outcome was not captured. Trade management was partly successful."
        if outcome == "UNKNOWN":
            return "Final broker close was not fetched; outcome is unclassified. Improve close-event observability."
        return "Review whether the thesis was right but follow-through was weak, or the trade lacked enough edge."

    def _log_trade_outcome(self, trade_record: dict):
        csv_file = self.log_dir / "trades.csv"
        file_exists = csv_file.exists()

        fields = [
            "date",
            "pair",
            "direction",
            "entry_price",
            "exit_price",
            "stop_loss",
            "take_profit",
            "lot_size",
            "outcome",
            "pnl_r",
            "pnl_usd",
            "duration_hours",
            "session",
            "confluence_score",
            "lesson",
        ]

        with open(csv_file, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            if not file_exists:
                writer.writeheader()
            writer.writerow(trade_record)
