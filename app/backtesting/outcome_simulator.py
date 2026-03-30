from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any

import pandas as pd

from app.core.trade_management import (
    assess_early_momentum_exit,
    resolve_adaptive_time_stop_hours,
    trail_distance_from_context,
)
from app.core.config import TRADING_CONFIG
from app.execution.trade_journal import TradeJournal


@dataclass(frozen=True)
class SimulationSummary:
    instrument: str
    source_signals_path: str
    output_path: str
    signals_seen: int
    tradable_signals: int
    filled_trades: int
    no_fill_signals: int
    blocked_by_open_position: int = 0
    blocked_by_daily_loss: int = 0
    blocked_by_weekly_loss: int = 0


class OutcomeSimulator:
    """
    Convert replayed signals into closed-trade style records using historical M1
    candles and a deterministic approximation of the live executor lifecycle.
    """

    def __init__(
        self,
        loader,
        *,
        output_root: Path | None = None,
        trading_config: dict[str, Any] | None = None,
    ):
        self.loader = loader
        self.output_root = Path(output_root or "backtest_results")
        self.output_root.mkdir(parents=True, exist_ok=True)
        self.config = dict(TRADING_CONFIG)
        if trading_config:
            self.config.update(trading_config)

    def simulate(
        self,
        signals_path: Path,
        *,
        instrument: str = "EUR_USD",
        local_only: bool = True,
        output_path: Path | None = None,
    ) -> SimulationSummary:
        signals = _read_jsonl(signals_path)
        tradable = sorted(
            [
            item
            for item in signals
            if bool(item.get("execution_allowed"))
            and str((item.get("signal") or {}).get("direction", "NEUTRAL")).upper() in {"BUY", "SELL"}
            ],
            key=lambda item: _parse_utc(item.get("timestamp")) or datetime.max.replace(tzinfo=timezone.utc),
        )

        instrument_slug = instrument.replace("/", "_").upper()
        output_file = Path(output_path or self._default_output_path(instrument_slug, signals_path))
        output_file.parent.mkdir(parents=True, exist_ok=True)

        if not tradable:
            output_file.write_text("", encoding="utf-8")
            return SimulationSummary(
                instrument=instrument_slug,
                source_signals_path=str(signals_path),
                output_path=str(output_file),
                signals_seen=len(signals),
                tradable_signals=0,
                filled_trades=0,
                no_fill_signals=0,
            )

        timestamps = [_parse_utc(item.get("timestamp")) for item in tradable]
        signal_times = [item for item in timestamps if item is not None]
        if not signal_times:
            output_file.write_text("", encoding="utf-8")
            return SimulationSummary(
                instrument=instrument_slug,
                source_signals_path=str(signals_path),
                output_path=str(output_file),
                signals_seen=len(signals),
                tradable_signals=len(tradable),
                filled_trades=0,
                no_fill_signals=len(tradable),
            )

        earliest = min(signal_times)
        latest = max(signal_times)
        extension = timedelta(days=7)
        if local_only:
            local_range = self.loader.find_covering_raw_dataset(
                instrument_slug,
                "M1",
                start=earliest,
                end=latest,
            )
            load_start = local_range.start if local_range is not None else earliest
            load_end = local_range.end if local_range is not None else latest + extension
        else:
            load_start = earliest
            load_end = latest + extension

        candles = self.loader.load_candles(
            instrument_slug,
            "M1",
            start=load_start,
            end=load_end,
            allow_remote=not local_only,
        )

        filled = 0
        no_fill = 0
        blocked_overlap = 0
        blocked_daily_loss = 0
        blocked_weekly_loss = 0
        sequential_mode = bool(self.config.get("backtest_single_position_mode", True))
        enforce_loss_limits = bool(self.config.get("backtest_enforce_loss_limits", True))
        equity = float(self.config.get("backtest_starting_balance", 1000.0) or 1000.0)
        active_until: datetime | None = None
        current_day = None
        day_start_equity = equity
        current_week = None
        week_start_equity = equity

        with open(output_file, "w", encoding="utf-8") as handle:
            for signal in tradable:
                signal_time = _parse_utc(signal.get("timestamp"))
                if signal_time is None:
                    no_fill += 1
                    continue

                if current_day != signal_time.date():
                    current_day = signal_time.date()
                    day_start_equity = equity

                iso_week = (signal_time.isocalendar().year, signal_time.isocalendar().week)
                if current_week != iso_week:
                    current_week = iso_week
                    week_start_equity = equity

                if sequential_mode and active_until and signal_time < active_until:
                    blocked_overlap += 1
                    continue

                block_reason = self._account_block_reason(
                    equity=equity,
                    day_start_equity=day_start_equity,
                    week_start_equity=week_start_equity,
                ) if enforce_loss_limits else ""
                if block_reason == "daily_loss":
                    blocked_daily_loss += 1
                    continue
                if block_reason == "weekly_loss":
                    blocked_weekly_loss += 1
                    continue

                result = self.simulate_signal(signal, candles)
                if result is None:
                    no_fill += 1
                    continue
                result = self._apply_account_valuation(result, equity)
                filled += 1
                handle.write(json.dumps(result) + "\n")
                equity = float(result.get("account_equity_after", equity))
                closed_at = _parse_utc(result.get("closed_at"))
                if sequential_mode and closed_at is not None:
                    active_until = closed_at

        return SimulationSummary(
            instrument=instrument_slug,
            source_signals_path=str(signals_path),
            output_path=str(output_file),
            signals_seen=len(signals),
            tradable_signals=len(tradable),
            filled_trades=filled,
            no_fill_signals=no_fill,
            blocked_by_open_position=blocked_overlap,
            blocked_by_daily_loss=blocked_daily_loss,
            blocked_by_weekly_loss=blocked_weekly_loss,
        )

    def simulate_signal(self, signal: dict[str, Any], candles: pd.DataFrame) -> dict[str, Any] | None:
        signal_time = _parse_utc(signal.get("timestamp"))
        if signal_time is None:
            return None

        direction = str((signal.get("signal") or {}).get("direction", "NEUTRAL")).upper()
        if direction not in {"BUY", "SELL"}:
            return None

        sig = signal.get("signal") or {}
        entry_zone = sig.get("entry_zone") or []
        if not isinstance(entry_zone, list) or len(entry_zone) < 2:
            return None

        entry_price = round((float(entry_zone[0]) + float(entry_zone[1])) / 2, 5)
        stop_loss = float(sig.get("stop_loss", 0) or 0)
        tp1 = float(sig.get("take_profit_1", 0) or 0)
        tp2 = float(sig.get("take_profit_2", 0) or 0)
        risk_reward = float(sig.get("risk_reward", 0) or 0)
        modeled_entry_price = self._effective_entry_price(direction, entry_price)
        risk_distance = abs(modeled_entry_price - stop_loss)
        if risk_distance <= 0:
            return None

        future = candles[candles.index >= signal_time]
        if future.empty:
            return None

        fill_candle = self._find_entry_fill(future, entry_price)
        if fill_candle is None:
            return None

        fill_time = fill_candle.name.to_pydatetime().astimezone(timezone.utc)
        max_hours, _ = resolve_adaptive_time_stop_hours(
            self.config,
            session=str(signal.get("session", "")),
            direction=direction,
            technical_analysis=(signal.get("technical_analysis") if isinstance(signal.get("technical_analysis"), dict) else {}),
            macro_bias=(signal.get("macro_bias") if isinstance(signal.get("macro_bias"), dict) else {}),
            confluence_score=signal.get("confluence_score"),
        )
        early_exit_after = fill_time + timedelta(
            minutes=max(int(float(self.config.get("early_momentum_minutes", 60))), 1)
        )
        time_stop_after = fill_time + timedelta(hours=max_hours)
        close_pct = float(self.config.get("tp1_close_percent", 0.50))
        remaining_fraction = 1.0
        partial_realized_r = 0.0
        tp1_hit = False
        tp1_fill_price = None
        tp1_closed_fraction = 0.0
        stop_moved_to_entry = False
        partial_close_events: list[dict[str, Any]] = []
        current_sl = stop_loss
        technical = signal.get("technical_analysis") or {}
        trail_distance = trail_distance_from_context(
            entry_price=entry_price,
            tp1_price=tp1,
            atr_1h_at_entry=(technical.get("atr_1h") if isinstance(technical, dict) else None),
            trail_atr_multiplier=self.config.get("trail_atr_multiplier", 1.0),
        )
        early_momentum_checked = False
        best_favorable_price = entry_price
        for candle in future[future.index >= fill_candle.name].itertuples():
            candle_time = candle.Index.to_pydatetime().astimezone(timezone.utc)
            high = float(candle.high)
            low = float(candle.low)
            close = float(candle.close)
            if direction == "BUY":
                best_favorable_price = max(best_favorable_price, high)
            else:
                best_favorable_price = min(best_favorable_price, low)

            if direction == "BUY":
                protective_hit = low <= current_sl
                tp2_hit = high >= tp2 if tp2 else False
                tp1_hit_now = (not tp1_hit) and (high >= tp1 if tp1 else False)
            else:
                protective_hit = high >= current_sl
                tp2_hit = low <= tp2 if tp2 else False
                tp1_hit_now = (not tp1_hit) and (low <= tp1 if tp1 else False)

            # Conservative same-candle ordering: once the trade is open, adverse
            # protective levels win ties against favorable intrabar touches.
            if protective_hit:
                total_r = partial_realized_r + self._remaining_r(
                    direction,
                    modeled_entry_price,
                    self._effective_exit_price(direction, current_sl),
                    risk_distance,
                    remaining_fraction,
                )
                return self._closed_trade_record(
                    signal,
                    entry_price=entry_price,
                    modeled_entry_price=modeled_entry_price,
                    stop_loss=stop_loss,
                    tp2=tp2,
                    fill_time=fill_time,
                    close_time=candle_time,
                    close_reason="STOP_LOSS" if current_sl != entry_price else "BREAKEVEN_STOP",
                    total_pnl_r=total_r,
                    partial_realized_r=partial_realized_r,
                    risk_reward=risk_reward,
                    tp1_hit=tp1_hit,
                    tp1_fill_price=tp1_fill_price,
                    tp1_closed_fraction=tp1_closed_fraction,
                    stop_moved_to_entry=stop_moved_to_entry,
                    partial_close_events=partial_close_events,
                )

            if tp2_hit:
                if not tp1_hit and tp1:
                    partial_r = (
                        self._price_to_r(
                            direction,
                            modeled_entry_price,
                            self._effective_exit_price(direction, tp1),
                            risk_distance,
                        ) * close_pct
                    )
                    partial_realized_r += partial_r
                    tp1_hit = True
                    tp1_fill_price = tp1
                    tp1_closed_fraction = close_pct
                    stop_moved_to_entry = True
                    partial_close_events.append(
                        {
                            "timestamp": _to_utc_z(candle_time),
                            "type": "TP1_PARTIAL",
                            "price": round(tp1, 5),
                            "size_fraction_closed": close_pct,
                            "estimated_realized_pnl_r": round(partial_r, 4),
                        }
                    )
                    remaining_fraction = max(0.0, 1.0 - close_pct)
                    current_sl = entry_price

                total_r = partial_realized_r + self._remaining_r(
                    direction,
                    modeled_entry_price,
                    self._effective_exit_price(direction, tp2),
                    risk_distance,
                    remaining_fraction,
                )
                return self._closed_trade_record(
                    signal,
                    entry_price=entry_price,
                    modeled_entry_price=modeled_entry_price,
                    stop_loss=stop_loss,
                    tp2=tp2,
                    fill_time=fill_time,
                    close_time=candle_time,
                    close_reason="TAKE_PROFIT_2",
                    total_pnl_r=total_r,
                    partial_realized_r=partial_realized_r,
                    risk_reward=risk_reward,
                    tp1_hit=tp1_hit,
                    tp1_fill_price=tp1_fill_price,
                    tp1_closed_fraction=tp1_closed_fraction,
                    stop_moved_to_entry=stop_moved_to_entry,
                    partial_close_events=partial_close_events,
                )

            # Mirror the live monitor lifecycle closely enough for replay:
            # 1. broker-managed SL / TP2
            # 2. time-stop decision using the current marked price
            # 3. executor-managed TP1 handling
            # 4. first-hour momentum exit for stalled trades
            # 5. trailing-stop ratchet after TP1
            if candle_time >= time_stop_after:
                floating_r = self._remaining_r(
                    direction,
                    modeled_entry_price,
                    self._effective_exit_price(direction, close),
                    risk_distance,
                    remaining_fraction,
                )
                if floating_r <= -(0.5 * remaining_fraction):
                    total_r = partial_realized_r + floating_r
                    return self._closed_trade_record(
                        signal,
                        entry_price=entry_price,
                        modeled_entry_price=modeled_entry_price,
                        stop_loss=stop_loss,
                        tp2=tp2,
                        fill_time=fill_time,
                        close_time=candle_time,
                        close_reason="TIME_STOP",
                        total_pnl_r=total_r,
                        partial_realized_r=partial_realized_r,
                        risk_reward=risk_reward,
                        tp1_hit=tp1_hit,
                        tp1_fill_price=tp1_fill_price,
                        tp1_closed_fraction=tp1_closed_fraction,
                        stop_moved_to_entry=stop_moved_to_entry,
                        partial_close_events=partial_close_events,
                    )

            if tp1_hit_now:
                partial_r = (
                    self._price_to_r(
                        direction,
                        modeled_entry_price,
                        self._effective_exit_price(direction, tp1),
                        risk_distance,
                    ) * close_pct
                )
                partial_realized_r += partial_r
                tp1_hit = True
                tp1_fill_price = tp1
                tp1_closed_fraction = close_pct
                stop_moved_to_entry = True
                partial_close_events.append(
                    {
                        "timestamp": _to_utc_z(candle_time),
                        "type": "TP1_PARTIAL",
                        "price": round(tp1, 5),
                        "size_fraction_closed": close_pct,
                        "estimated_realized_pnl_r": round(partial_r, 4),
                    }
                )
                remaining_fraction = max(0.0, 1.0 - close_pct)
                current_sl = entry_price

            if not early_momentum_checked and candle_time >= early_exit_after:
                early_momentum_checked = True
                assessment = assess_early_momentum_exit(
                    self.config,
                    direction=direction,
                    entry_price=entry_price,
                    tp2_price=tp2,
                    favorable_price=best_favorable_price,
                )
                if assessment.enabled and assessment.should_exit and not tp1_hit:
                    total_r = partial_realized_r + self._remaining_r(
                        direction,
                        modeled_entry_price,
                        self._effective_exit_price(direction, close),
                        risk_distance,
                        remaining_fraction,
                    )
                    return self._closed_trade_record(
                        signal,
                        entry_price=entry_price,
                        modeled_entry_price=modeled_entry_price,
                        stop_loss=stop_loss,
                        tp2=tp2,
                        fill_time=fill_time,
                        close_time=candle_time,
                        close_reason="EARLY_MOMENTUM_EXIT",
                        total_pnl_r=total_r,
                        partial_realized_r=partial_realized_r,
                        risk_reward=risk_reward,
                        tp1_hit=tp1_hit,
                        tp1_fill_price=tp1_fill_price,
                        tp1_closed_fraction=tp1_closed_fraction,
                        stop_moved_to_entry=stop_moved_to_entry,
                        partial_close_events=partial_close_events,
                    )

            if tp1_hit and remaining_fraction > 0 and self.config.get("tp2_trail", True):
                if direction == "BUY":
                    new_sl = round(high - trail_distance, 5)
                    if new_sl < high and new_sl > current_sl:
                        current_sl = new_sl
                else:
                    new_sl = round(low + trail_distance, 5)
                    if new_sl > low and new_sl < current_sl:
                        current_sl = new_sl

        last_candle = future.iloc[-1]
        last_time = future.index[-1].to_pydatetime().astimezone(timezone.utc)
        close_price = float(last_candle["close"])
        total_r = partial_realized_r + self._remaining_r(
            direction,
            modeled_entry_price,
            self._effective_exit_price(direction, close_price),
            risk_distance,
            remaining_fraction,
        )
        return self._closed_trade_record(
            signal,
            entry_price=entry_price,
            modeled_entry_price=modeled_entry_price,
            stop_loss=stop_loss,
            tp2=tp2,
            fill_time=fill_time,
            close_time=last_time,
            close_reason="DATA_END",
            total_pnl_r=total_r,
            partial_realized_r=partial_realized_r,
            risk_reward=risk_reward,
            tp1_hit=tp1_hit,
            tp1_fill_price=tp1_fill_price,
            tp1_closed_fraction=tp1_closed_fraction,
            stop_moved_to_entry=stop_moved_to_entry,
            partial_close_events=partial_close_events,
        )

    def _default_output_path(self, instrument_slug: str, signals_path: Path) -> Path:
        filename = signals_path.name.replace("_replay_", "_simulated_closed_trades_")
        return self.output_root / instrument_slug / filename

    @staticmethod
    def _find_entry_fill(candles: pd.DataFrame, entry_price: float):
        touched = candles[(candles["low"] <= entry_price) & (candles["high"] >= entry_price)]
        if touched.empty:
            return None
        return touched.iloc[0]

    def _account_block_reason(
        self,
        *,
        equity: float,
        day_start_equity: float,
        week_start_equity: float,
    ) -> str:
        risk_pct = float(self.config.get("max_risk_per_trade", 0.01) or 0.01) * 100
        max_daily_loss = float(self.config.get("max_daily_loss", 0.02) or 0.02) * 100
        max_weekly_loss = float(self.config.get("max_weekly_loss", 0.05) or 0.05) * 100

        daily_pnl_pct = self._account_pnl_pct(equity, day_start_equity)
        if daily_pnl_pct <= -max_daily_loss or (daily_pnl_pct - risk_pct) < -max_daily_loss:
            return "daily_loss"

        weekly_pnl_pct = self._account_pnl_pct(equity, week_start_equity)
        if weekly_pnl_pct <= -max_weekly_loss or (weekly_pnl_pct - risk_pct) < -max_weekly_loss:
            return "weekly_loss"

        return ""

    @staticmethod
    def _account_pnl_pct(equity: float, start_equity: float) -> float:
        if start_equity <= 0:
            return 0.0
        return ((equity - start_equity) / start_equity) * 100

    def _apply_account_valuation(self, trade_record: dict[str, Any], equity_before: float) -> dict[str, Any]:
        risk_usd = max(equity_before * float(self.config.get("max_risk_per_trade", 0.01) or 0.01), 0.0001)
        pnl_r = float(trade_record.get("pnl_r", 0) or 0)
        partial_realized_r = float(trade_record.get("partial_realized_pnl_r", 0) or 0)
        pnl_usd = risk_usd * pnl_r
        equity_after = equity_before + pnl_usd

        updated = dict(trade_record)
        updated["risk_amount_usd"] = round(risk_usd, 2)
        updated["pnl_usd"] = round(pnl_usd, 2)
        updated["partial_realized_pnl_usd"] = round(risk_usd * partial_realized_r, 2)
        updated["account_equity_before"] = round(equity_before, 2)
        updated["account_equity_after"] = round(equity_after, 2)
        return updated

    def _execution_cost_distance(self) -> float:
        spread_pips = max(float(self.config.get("backtest_spread_pips", 0.0) or 0.0), 0.0)
        slippage_pips = max(float(self.config.get("backtest_slippage_pips", 0.0) or 0.0), 0.0)
        return ((spread_pips / 2.0) + slippage_pips) / 10000.0

    def _effective_entry_price(self, direction: str, price: float) -> float:
        cost = self._execution_cost_distance()
        if direction == "BUY":
            return round(price + cost, 5)
        return round(price - cost, 5)

    def _effective_exit_price(self, direction: str, price: float) -> float:
        cost = self._execution_cost_distance()
        if direction == "BUY":
            return round(price - cost, 5)
        return round(price + cost, 5)

    @staticmethod
    def _price_to_r(direction: str, entry: float, price: float, risk_distance: float) -> float:
        move = (price - entry) if direction == "BUY" else (entry - price)
        return move / risk_distance

    def _remaining_r(
        self,
        direction: str,
        entry: float,
        exit_price: float,
        risk_distance: float,
        remaining_fraction: float,
    ) -> float:
        return self._price_to_r(direction, entry, exit_price, risk_distance) * remaining_fraction

    @staticmethod
    def _closed_trade_record(
        signal: dict[str, Any],
        *,
        entry_price: float,
        modeled_entry_price: float,
        stop_loss: float,
        tp2: float,
        fill_time: datetime,
        close_time: datetime,
        close_reason: str,
        total_pnl_r: float,
        partial_realized_r: float,
        risk_reward: float,
        tp1_hit: bool,
        tp1_fill_price: float | None,
        tp1_closed_fraction: float,
        stop_moved_to_entry: bool,
        partial_close_events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        signal_payload = signal.get("signal") or {}
        confidence = int(signal_payload.get("confidence", 0) or 0)
        validator_overrides = list(signal.get("validator_overrides") or [])
        setup_grade = TradeJournal._grade_setup(
            int(signal.get("confluence_score", 0) or 0),
            confidence,
            float(risk_reward or 0),
            validator_overrides,
        )
        synthetic_trade = {
            "entry_price": entry_price,
            "confluence": signal.get("confluence_score", 0),
            "confidence": confidence,
            "risk_reward": risk_reward,
            "validator_overrides": validator_overrides,
            "entry_signal_snapshot": signal,
        }
        entry_timing = TradeJournal._classify_entry_timing(synthetic_trade)
        outcome = TradeJournal._determine_close_outcome(
            {"tp1_hit": tp1_hit, "partial_realized_pnl_usd": partial_realized_r},
            total_pnl_r,
            True,
        )

        return {
            "date": close_time.strftime("%Y-%m-%d"),
            "pair": signal.get("pair", "EUR/USD"),
            "direction": signal_payload.get("direction", ""),
            "entry_price": round(entry_price, 5),
            "modeled_entry_price": round(modeled_entry_price, 5),
            "stop_loss": round(stop_loss, 5),
            "take_profit": round(float(tp2 or 0), 5),
            "lot_size": 1.0,
            "outcome": outcome,
            "pnl_r": round(total_pnl_r, 4),
            "pnl_usd": round(total_pnl_r, 4),
            "pnl_unit": "R",
            "duration_hours": round((close_time - fill_time).total_seconds() / 3600, 2),
            "session": signal.get("session", ""),
            "confluence_score": signal.get("confluence_score", 0),
            "close_reason": close_reason,
            "close_reason_detail": _close_reason_detail(close_reason),
            "setup_grade": setup_grade,
            "entry_timing": entry_timing,
            "signal_timestamp": signal.get("timestamp", ""),
            "signal_strength": signal.get("signal_strength", ""),
            "confidence": confidence,
            "risk_reward": risk_reward,
            "validator_overrides": validator_overrides,
            "tp1_hit": tp1_hit,
            "tp1_fill_price": round(float(tp1_fill_price), 5) if tp1_fill_price else None,
            "tp1_closed_units": round(tp1_closed_fraction, 4),
            "stop_moved_to_entry": stop_moved_to_entry,
            "partial_realized_pnl_r": round(partial_realized_r, 4),
            "partial_realized_pnl_usd": round(partial_realized_r, 4),
            "partial_close_events": partial_close_events,
            "pnl_is_partial_estimate": False,
            "pnl_missing_reason": "",
            "missing_detail_reasons": [],
            "backtest_mode": True,
            "entry_filled_at": _to_utc_z(fill_time),
            "closed_at": _to_utc_z(close_time),
        }


def simulation_summary_to_dict(summary: SimulationSummary) -> dict[str, Any]:
    return asdict(summary)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _parse_utc(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _to_utc_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _close_reason_detail(reason: str) -> str:
    details = {
        "STOP_LOSS": "Historical price touched the active protective stop before any further target progression.",
        "BREAKEVEN_STOP": "Remaining position was stopped at entry after TP1 moved the stop to breakeven.",
        "TAKE_PROFIT_2": "Historical price reached the broker-managed final take-profit level.",
        "EARLY_MOMENTUM_EXIT": "The trade was closed early because the first-hour expansion toward TP2 was too weak.",
        "TIME_STOP": "Trade was still below -0.5R after the configured holding limit and was closed at market.",
        "DATA_END": "Historical dataset ended before another exit condition triggered; the remaining position was marked to the final available close.",
    }
    return details.get(reason, reason)
