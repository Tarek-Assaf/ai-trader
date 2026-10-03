"""Deterministic, fail-closed approval rules for proposed trades."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal

from trader.core.audit import AuditEvent, AuditWriter

LOGGER = logging.getLogger(__name__)
COMBINED_EXPOSURE_INSTRUMENTS = frozenset({"NAS100", "XAUUSD"})
RuleName = Literal[
    "has_stop",
    "stop_distance",
    "reward_risk",
    "risk_per_trade",
    "daily_loss",
    "weekly_loss",
    "drawdown",
    "open_positions",
    "combined_exposure",
    "margin_usage",
    "leverage",
    "market_open",
    "news_blackout",
    "data_fresh",
    "spread",
    "order_rate",
    "stage_cap",
    "trade_quality",
    "loss_streak",
    "kill_pause",
]


@dataclass(frozen=True, slots=True)
class RiskLimits:
    """Immutable runtime values copied from the validated risk configuration."""

    risk_per_trade_pct: Decimal
    max_daily_loss_pct: Decimal
    max_weekly_loss_pct: Decimal
    max_drawdown_pct: Decimal
    max_open_positions_total: int
    max_open_positions_per_instrument: int
    max_combined_risk_pct: Decimal
    max_margin_usage_pct: Decimal
    max_effective_leverage: Decimal
    min_reward_risk: Decimal
    min_stop_atr: Decimal
    max_stop_atr: Decimal
    max_spread_atr_ratio: Decimal
    stale_data_max_seconds: int
    max_orders_per_hour: int
    blackout_before_min: int
    blackout_after_min: int
    consecutive_loss_pause: int
    loss_cooldown_minutes: int
    min_trade_quality_score: Decimal

    def __post_init__(self) -> None:
        numeric_limits = (
            self.risk_per_trade_pct,
            self.max_daily_loss_pct,
            self.max_weekly_loss_pct,
            self.max_drawdown_pct,
            self.max_combined_risk_pct,
            self.max_margin_usage_pct,
            self.max_effective_leverage,
            self.min_reward_risk,
            self.min_stop_atr,
            self.max_stop_atr,
            self.max_spread_atr_ratio,
        )
        count_limits = (
            self.max_open_positions_total,
            self.max_open_positions_per_instrument,
            self.stale_data_max_seconds,
            self.max_orders_per_hour,
            self.blackout_before_min,
            self.blackout_after_min,
            self.consecutive_loss_pause,
            self.loss_cooldown_minutes,
        )
        if any(not value.is_finite() or value <= 0 for value in numeric_limits):
            raise ValueError("risk thresholds must be finite and positive")
        if any(value <= 0 for value in count_limits):
            raise ValueError("risk count and duration limits must be positive")
        if self.max_stop_atr < self.min_stop_atr:
            raise ValueError("max_stop_atr cannot be lower than min_stop_atr")
        if (
            not self.min_trade_quality_score.is_finite()
            or self.min_trade_quality_score <= 0
            or self.min_trade_quality_score > 1
        ):
            raise ValueError("min_trade_quality_score must be in (0, 1]")

    @classmethod
    def from_mapping(cls, values: Mapping[str, object]) -> RiskLimits:
        """Build limits from the strict result of ``load_all_configs``."""
        try:
            return cls(
                risk_per_trade_pct=_decimal(values["risk_per_trade_pct"]),
                max_daily_loss_pct=_decimal(values["max_daily_loss_pct"]),
                max_weekly_loss_pct=_decimal(values["max_weekly_loss_pct"]),
                max_drawdown_pct=_decimal(values["max_drawdown_pct"]),
                max_open_positions_total=_integer(values["max_open_positions_total"]),
                max_open_positions_per_instrument=_integer(
                    values["max_open_positions_per_instrument"]
                ),
                max_combined_risk_pct=_decimal(values["max_combined_risk_pct"]),
                max_margin_usage_pct=_decimal(values["max_margin_usage_pct"]),
                max_effective_leverage=_decimal(values["max_effective_leverage"]),
                min_reward_risk=_decimal(values["min_reward_risk"]),
                min_stop_atr=_decimal(values["min_stop_atr"]),
                max_stop_atr=_decimal(values["max_stop_atr"]),
                max_spread_atr_ratio=_decimal(values["max_spread_atr_ratio"]),
                stale_data_max_seconds=_integer(values["stale_data_max_seconds"]),
                max_orders_per_hour=_integer(values["max_orders_per_hour"]),
                blackout_before_min=_integer(values["blackout_before_min"]),
                blackout_after_min=_integer(values["blackout_after_min"]),
                consecutive_loss_pause=_integer(values["consecutive_loss_pause"]),
                loss_cooldown_minutes=_integer(values["loss_cooldown_minutes"]),
                min_trade_quality_score=_decimal(values["min_trade_quality_score"]),
            )
        except (KeyError, InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError(
                "risk configuration is missing or has invalid Guardian limits"
            ) from exc


@dataclass(frozen=True, slots=True)
class TradeIntent:
    """Trade proposal without a position size; an LLM cannot choose size."""

    instrument: str
    direction: Literal["buy", "sell"]
    entry_price: Decimal
    stop_loss: Decimal | None
    take_profit: Decimal | None
    stage_size_cap: Decimal | None
    trade_quality_score: Decimal


@dataclass(frozen=True, slots=True)
class NewsEvent:
    """A scheduled event; high impact events also trigger the news rule."""

    at: datetime
    high_impact: bool


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """Market state required to evaluate tradability and quote quality."""

    latest_data_at: datetime
    market_open: bool
    in_break: bool
    bid: Decimal
    ask: Decimal
    atr: Decimal
    spread_ceiling: Decimal | None = None
    scheduled_events: tuple[NewsEvent, ...] = ()


@dataclass(frozen=True, slots=True)
class AccountState:
    """Equity and durable account-level safety state from the broker/core.

    The caller must persist ``drawdown_halt_active`` and clear it only through
    an explicit owner reset; the Guardian reports ``halt_system`` when it first
    observes a breached drawdown limit.
    """

    equity: Decimal
    day_start_equity: Decimal
    week_start_equity: Decimal
    peak_equity: Decimal
    daily_pnl: Decimal
    weekly_pnl: Decimal
    margin_used: Decimal
    notional_exposure: Decimal
    orders_last_hour: int
    consecutive_losses: int
    last_loss_at: datetime | None
    paused: bool = False
    kill_switch_active: bool = False
    drawdown_halt_active: bool = False


@dataclass(frozen=True, slots=True)
class OpenPosition:
    """Existing position's risk and capital use, supplied by reconciled state."""

    instrument: str
    risk_amount: Decimal
    margin_required: Decimal
    notional_exposure: Decimal


@dataclass(frozen=True, slots=True)
class SizingEstimate:
    """Position-size result from risk/sizing.py, never computed by this module."""

    size: Decimal
    risk_amount: Decimal
    margin_required: Decimal
    notional_exposure: Decimal


@dataclass(frozen=True, slots=True)
class RiskDecision:
    """Audited outcome from evaluating every configured risk rule."""

    approved: bool
    approved_size: Decimal | None
    reasons: tuple[str, ...]
    rules_checked: tuple[str, ...]
    halt_system: bool = False
    audit_written: bool = True


@dataclass(frozen=True, slots=True)
class _Context:
    intent: TradeIntent
    market: MarketSnapshot
    account: AccountState
    positions: Sequence[OpenPosition]
    sizing: SizingEstimate | None
    limits: RiskLimits
    now: datetime


@dataclass(frozen=True, slots=True)
class _RuleResult:
    passed: bool
    reason: str = ""


def _decimal(value: object) -> Decimal:
    if isinstance(value, bool):
        raise ValueError("boolean is not a numeric risk limit")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("risk limits must be finite")
    return result


def _integer(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("risk count limit must be an integer")
    return value


def _valid_positive(value: Decimal) -> bool:
    return value.is_finite() and value > 0


def _aware(value: datetime | None) -> bool:
    return value is not None and value.tzinfo is not None and value.utcoffset() is not None


def _result(condition: bool, reason: str) -> _RuleResult:
    return _RuleResult(condition, "" if condition else reason)


def _has_stop(ctx: _Context) -> _RuleResult:
    if not isinstance(ctx.intent.instrument, str) or not ctx.intent.instrument.strip():
        return _RuleResult(False, "instrument_required")
    return _result(ctx.intent.stop_loss is not None, "stop_loss_required")


def _stop_distance(ctx: _Context) -> _RuleResult:
    stop = ctx.intent.stop_loss
    atr = ctx.market.atr
    entry = ctx.intent.entry_price
    if stop is None or not all(_valid_positive(value) for value in (entry, stop, atr)):
        return _RuleResult(False, "stop_distance_or_atr_invalid")
    distance_atr = abs(entry - stop) / atr
    return _result(
        ctx.limits.min_stop_atr <= distance_atr <= ctx.limits.max_stop_atr,
        "stop_distance_outside_atr_limits",
    )


def _reward_risk(ctx: _Context) -> _RuleResult:
    stop, target = ctx.intent.stop_loss, ctx.intent.take_profit
    entry = ctx.intent.entry_price
    if (
        stop is None
        or target is None
        or not all(_valid_positive(value) for value in (entry, stop, target))
    ):
        return _RuleResult(False, "stop_and_target_required")
    if ctx.intent.direction == "buy":
        risk, reward = entry - stop, target - entry
    elif ctx.intent.direction == "sell":
        risk, reward = stop - entry, entry - target
    else:
        return _RuleResult(False, "direction_invalid")
    if risk <= 0 or reward <= 0:
        return _RuleResult(False, "stop_or_target_on_wrong_side")
    return _result(reward / risk >= ctx.limits.min_reward_risk, "reward_risk_below_minimum")


def _risk_per_trade(ctx: _Context) -> _RuleResult:
    sizing, equity = ctx.sizing, ctx.account.equity
    if (
        sizing is None
        or not _valid_positive(equity)
        or not _valid_positive(sizing.risk_amount)
        or not _valid_positive(sizing.size)
        or not sizing.margin_required.is_finite()
        or sizing.margin_required < 0
        or not sizing.notional_exposure.is_finite()
        or sizing.notional_exposure < 0
    ):
        return _RuleResult(False, "sizing_estimate_or_risk_invalid")
    budget = equity * ctx.limits.risk_per_trade_pct / Decimal(100)
    return _result(sizing.risk_amount <= budget, "risk_per_trade_limit_exceeded")


def _loss_rule(pnl: Decimal, baseline: Decimal, limit_pct: Decimal, reason: str) -> _RuleResult:
    if not _valid_positive(baseline):
        return _RuleResult(False, "loss_baseline_invalid")
    loss_pct = max(Decimal(0), -pnl) / baseline * Decimal(100)
    return _result(loss_pct < limit_pct, reason)


def _daily_loss(ctx: _Context) -> _RuleResult:
    return _loss_rule(
        ctx.account.daily_pnl,
        ctx.account.day_start_equity,
        ctx.limits.max_daily_loss_pct,
        "daily_loss_limit_reached",
    )


def _weekly_loss(ctx: _Context) -> _RuleResult:
    return _loss_rule(
        ctx.account.weekly_pnl,
        ctx.account.week_start_equity,
        ctx.limits.max_weekly_loss_pct,
        "weekly_loss_limit_reached",
    )


def _drawdown(ctx: _Context) -> _RuleResult:
    account = ctx.account
    if account.drawdown_halt_active:
        return _RuleResult(False, "drawdown_halt_requires_manual_reset")
    if not _valid_positive(account.peak_equity) or not _valid_positive(account.equity):
        return _RuleResult(False, "drawdown_equity_invalid")
    drawdown_pct = (
        max(Decimal(0), account.peak_equity - account.equity) / account.peak_equity * Decimal(100)
    )
    return _result(drawdown_pct < ctx.limits.max_drawdown_pct, "drawdown_limit_reached")


def _open_positions(ctx: _Context) -> _RuleResult:
    if any(
        not isinstance(position.instrument, str) or not position.instrument.strip()
        for position in ctx.positions
    ):
        return _RuleResult(False, "open_position_instrument_invalid")
    count = len(ctx.positions)
    instrument_count = sum(
        position.instrument.strip().upper() == ctx.intent.instrument.strip().upper()
        for position in ctx.positions
    )
    valid = (
        count < ctx.limits.max_open_positions_total
        and instrument_count < ctx.limits.max_open_positions_per_instrument
    )
    return _result(valid, "open_position_limit_reached")


def _combined_exposure(ctx: _Context) -> _RuleResult:
    sizing = ctx.sizing
    if sizing is None:
        return _RuleResult(False, "sizing_estimate_required")
    if any(
        not position.risk_amount.is_finite() or position.risk_amount < 0
        for position in ctx.positions
    ):
        return _RuleResult(False, "open_position_risk_invalid")
    instrument = ctx.intent.instrument.strip().upper()
    risk = sizing.risk_amount if instrument in COMBINED_EXPOSURE_INSTRUMENTS else Decimal(0)
    risk += sum(
        (
            position.risk_amount
            for position in ctx.positions
            if position.instrument.strip().upper() in COMBINED_EXPOSURE_INSTRUMENTS
        ),
        start=Decimal(0),
    )
    if not _valid_positive(ctx.account.equity):
        return _RuleResult(False, "equity_invalid")
    risk_pct = risk / ctx.account.equity * Decimal(100)
    return _result(risk_pct <= ctx.limits.max_combined_risk_pct, "combined_risk_limit_exceeded")


def _margin_usage(ctx: _Context) -> _RuleResult:
    sizing = ctx.sizing
    if sizing is None or not _valid_positive(ctx.account.equity):
        return _RuleResult(False, "sizing_estimate_or_equity_required")
    if (
        not ctx.account.margin_used.is_finite()
        or not sizing.margin_required.is_finite()
        or ctx.account.margin_used < 0
        or sizing.margin_required < 0
    ):
        return _RuleResult(False, "margin_value_invalid")
    usage_pct = (ctx.account.margin_used + sizing.margin_required) / ctx.account.equity * 100
    return _result(usage_pct <= ctx.limits.max_margin_usage_pct, "margin_usage_limit_exceeded")


def _leverage(ctx: _Context) -> _RuleResult:
    sizing = ctx.sizing
    if sizing is None or not _valid_positive(ctx.account.equity):
        return _RuleResult(False, "sizing_estimate_or_equity_required")
    if (
        not ctx.account.notional_exposure.is_finite()
        or not sizing.notional_exposure.is_finite()
        or ctx.account.notional_exposure < 0
        or sizing.notional_exposure < 0
    ):
        return _RuleResult(False, "notional_exposure_invalid")
    leverage = (ctx.account.notional_exposure + sizing.notional_exposure) / ctx.account.equity
    return _result(leverage <= ctx.limits.max_effective_leverage, "leverage_limit_exceeded")


def _inside_event_window(ctx: _Context, event_at: datetime) -> bool:
    if not _aware(event_at) or not _aware(ctx.now):
        return True
    return (
        ctx.now - timedelta(minutes=ctx.limits.blackout_before_min)
        <= event_at
        <= ctx.now + timedelta(minutes=ctx.limits.blackout_after_min)
    )


def _market_open(ctx: _Context) -> _RuleResult:
    if not ctx.market.market_open or ctx.market.in_break:
        return _RuleResult(False, "market_closed_or_in_break")
    if any(_inside_event_window(ctx, event.at) for event in ctx.market.scheduled_events):
        return _RuleResult(False, "scheduled_event_buffer")
    return _RuleResult(True)


def _news_blackout(ctx: _Context) -> _RuleResult:
    blocked = any(
        event.high_impact and _inside_event_window(ctx, event.at)
        for event in ctx.market.scheduled_events
    )
    return _result(not blocked, "high_impact_news_blackout")


def _data_fresh(ctx: _Context) -> _RuleResult:
    if not _aware(ctx.now) or not _aware(ctx.market.latest_data_at):
        return _RuleResult(False, "market_timestamp_timezone_invalid")
    age = (ctx.now - ctx.market.latest_data_at).total_seconds()
    return _result(
        0 <= age <= ctx.limits.stale_data_max_seconds,
        "market_data_stale_or_from_future",
    )


def _spread(ctx: _Context) -> _RuleResult:
    market = ctx.market
    if not all(_valid_positive(value) for value in (market.bid, market.ask, market.atr)):
        return _RuleResult(False, "quote_or_atr_invalid")
    if market.ask < market.bid:
        return _RuleResult(False, "crossed_quote")
    spread = market.ask - market.bid
    if spread / market.atr > ctx.limits.max_spread_atr_ratio:
        return _RuleResult(False, "spread_atr_limit_exceeded")
    if market.spread_ceiling is not None and (
        not _valid_positive(market.spread_ceiling) or spread > market.spread_ceiling
    ):
        return _RuleResult(False, "instrument_spread_ceiling_exceeded")
    return _RuleResult(True)


def _order_rate(ctx: _Context) -> _RuleResult:
    if ctx.account.orders_last_hour < 0:
        return _RuleResult(False, "order_count_invalid")
    return _result(
        ctx.account.orders_last_hour < ctx.limits.max_orders_per_hour,
        "order_rate_limit_reached",
    )


def _stage_cap(ctx: _Context) -> _RuleResult:
    sizing, cap = ctx.sizing, ctx.intent.stage_size_cap
    if (
        sizing is None
        or cap is None
        or not _valid_positive(cap)
        or not _valid_positive(sizing.size)
    ):
        return _RuleResult(False, "stage_cap_or_sizing_missing")
    return _result(sizing.size <= cap, "strategy_stage_size_cap_exceeded")


def _trade_quality(ctx: _Context) -> _RuleResult:
    score = ctx.intent.trade_quality_score
    if not score.is_finite() or score < 0 or score > 1:
        return _RuleResult(False, "trade_quality_score_invalid")
    return _result(score >= ctx.limits.min_trade_quality_score, "trade_quality_below_minimum")


def _loss_streak(ctx: _Context) -> _RuleResult:
    count = ctx.account.consecutive_losses
    if count < 0:
        return _RuleResult(False, "loss_streak_invalid")
    if count < ctx.limits.consecutive_loss_pause:
        return _RuleResult(True)
    last_loss_at = ctx.account.last_loss_at
    if last_loss_at is None or not _aware(last_loss_at) or not _aware(ctx.now):
        return _RuleResult(False, "loss_cooldown_timestamp_missing")
    cooldown_ends = last_loss_at + timedelta(minutes=ctx.limits.loss_cooldown_minutes)
    if last_loss_at > ctx.now:
        return _RuleResult(False, "last_loss_timestamp_from_future")
    return _result(ctx.now >= cooldown_ends, "consecutive_loss_cooldown_active")


def _kill_pause(ctx: _Context) -> _RuleResult:
    active = (
        ctx.account.kill_switch_active or ctx.account.paused or ctx.account.drawdown_halt_active
    )
    return _result(not active, "kill_switch_or_pause_active")


_RULES: tuple[tuple[RuleName, Callable[[_Context], _RuleResult]], ...] = (
    ("has_stop", _has_stop),
    ("stop_distance", _stop_distance),
    ("reward_risk", _reward_risk),
    ("risk_per_trade", _risk_per_trade),
    ("daily_loss", _daily_loss),
    ("weekly_loss", _weekly_loss),
    ("drawdown", _drawdown),
    ("open_positions", _open_positions),
    ("combined_exposure", _combined_exposure),
    ("margin_usage", _margin_usage),
    ("leverage", _leverage),
    ("market_open", _market_open),
    ("news_blackout", _news_blackout),
    ("data_fresh", _data_fresh),
    ("spread", _spread),
    ("order_rate", _order_rate),
    ("stage_cap", _stage_cap),
    ("trade_quality", _trade_quality),
    ("loss_streak", _loss_streak),
    ("kill_pause", _kill_pause),
)


class RiskGuardian:
    """Run all risk rules and persist every result before allowing execution."""

    def __init__(self, limits: RiskLimits, audit_writer: AuditWriter) -> None:
        self._limits = limits
        self._audit_writer = audit_writer

    def evaluate(
        self,
        intent: TradeIntent,
        market: MarketSnapshot,
        account: AccountState,
        open_positions: Sequence[OpenPosition],
        sizing: SizingEstimate | None,
        *,
        now: datetime,
        cycle_id: str | None = None,
    ) -> RiskDecision:
        """Evaluate every rule; audit failure always changes the result to reject."""
        context = _Context(
            intent=intent,
            market=market,
            account=account,
            positions=open_positions,
            sizing=sizing,
            limits=self._limits,
            now=now,
        )
        checked: list[str] = []
        failures: list[str] = []
        for name, rule in _RULES:
            checked.append(name)
            try:
                result = rule(context)
            except Exception:
                LOGGER.error("risk rule %s failed unexpectedly; rejecting entry", name)
                result = _RuleResult(False, "rule_evaluation_failed")
            if not result.passed:
                failures.append(f"{name}:{result.reason or 'rejected'}")
        approved = not failures
        halt_system = any(reason.startswith("drawdown:") for reason in failures)
        decision = RiskDecision(
            approved=approved,
            approved_size=sizing.size if approved and sizing is not None else None,
            reasons=tuple(failures),
            rules_checked=tuple(checked),
            halt_system=halt_system,
        )
        try:
            self._audit_writer.append(
                AuditEvent(
                    event_type="risk.decision",
                    cycle_id=cycle_id,
                    payload={
                        "approved": decision.approved,
                        "approved_size": str(decision.approved_size)
                        if decision.approved_size is not None
                        else None,
                        "halt_system": decision.halt_system,
                        "reasons": list(decision.reasons),
                        "rules_checked": list(decision.rules_checked),
                    },
                )
            )
        except Exception:
            LOGGER.error("risk decision audit write failed; rejecting entry")
            return RiskDecision(
                approved=False,
                approved_size=None,
                reasons=(*decision.reasons, "audit_write_failed"),
                rules_checked=decision.rules_checked,
                halt_system=decision.halt_system,
                audit_written=False,
            )
        return decision


def risk_rule_names() -> tuple[str, ...]:
    """Expose stable names for completeness checks and diagnostics."""
    return tuple(name for name, _ in _RULES)


def risk_decision_payload(decision: RiskDecision) -> Mapping[str, object]:
    """Return a JSON-safe representation for callers that need to display it."""
    return {
        "approved": decision.approved,
        "approved_size": str(decision.approved_size)
        if decision.approved_size is not None
        else None,
        "reasons": list(decision.reasons),
        "rules_checked": list(decision.rules_checked),
        "halt_system": decision.halt_system,
        "audit_written": decision.audit_written,
    }
