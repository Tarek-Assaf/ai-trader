from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from hypothesis import given
from hypothesis import strategies as st

from trader.risk.guardian import (
    AccountState,
    MarketSnapshot,
    NewsEvent,
    OpenPosition,
    RiskDecision,
    RiskGuardian,
    RiskLimits,
    SizingEstimate,
    TradeIntent,
    risk_rule_names,
)

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


class _Audit:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def append(self, event: Any) -> None:
        self.events.append(event)


class _BrokenAudit:
    def append(self, event: Any) -> None:
        raise RuntimeError("database detail must not leak")


def _limits() -> RiskLimits:
    return RiskLimits(
        risk_per_trade_pct=Decimal("0.5"),
        max_daily_loss_pct=Decimal("2"),
        max_weekly_loss_pct=Decimal("5"),
        max_drawdown_pct=Decimal("10"),
        max_open_positions_total=3,
        max_open_positions_per_instrument=2,
        max_combined_risk_pct=Decimal("1"),
        max_margin_usage_pct=Decimal("40"),
        max_effective_leverage=Decimal("5"),
        min_reward_risk=Decimal("1.5"),
        min_stop_atr=Decimal("0.5"),
        max_stop_atr=Decimal("3"),
        max_spread_atr_ratio=Decimal("0.2"),
        stale_data_max_seconds=20,
        max_orders_per_hour=600,
        blackout_before_min=30,
        blackout_after_min=15,
        consecutive_loss_pause=4,
        loss_cooldown_minutes=30,
        min_trade_quality_score=Decimal("0.7"),
    )


def _intent(**changes: Any) -> TradeIntent:
    values: dict[str, Any] = {
        "instrument": "NAS100",
        "direction": "buy",
        "entry_price": Decimal("100"),
        "stop_loss": Decimal("99"),
        "take_profit": Decimal("102"),
        "stage_size_cap": Decimal("2"),
        "trade_quality_score": Decimal("0.9"),
    }
    values.update(changes)
    return TradeIntent(**values)


def _market(**changes: Any) -> MarketSnapshot:
    values: dict[str, Any] = {
        "latest_data_at": NOW,
        "market_open": True,
        "in_break": False,
        "bid": Decimal("99.9"),
        "ask": Decimal("100.1"),
        "atr": Decimal("1"),
    }
    values.update(changes)
    return MarketSnapshot(**values)


def _account(**changes: Any) -> AccountState:
    values: dict[str, Any] = {
        "equity": Decimal("10000"),
        "day_start_equity": Decimal("10000"),
        "week_start_equity": Decimal("10000"),
        "peak_equity": Decimal("10000"),
        "daily_pnl": Decimal("0"),
        "weekly_pnl": Decimal("0"),
        "margin_used": Decimal("500"),
        "notional_exposure": Decimal("10000"),
        "orders_last_hour": 10,
        "consecutive_losses": 0,
        "last_loss_at": None,
    }
    values.update(changes)
    return AccountState(**values)


def _sizing(**changes: Any) -> SizingEstimate:
    values: dict[str, Any] = {
        "size": Decimal("1"),
        "risk_amount": Decimal("10"),
        "margin_required": Decimal("100"),
        "notional_exposure": Decimal("1000"),
    }
    values.update(changes)
    return SizingEstimate(**values)


def _evaluate(
    *,
    intent: TradeIntent | None = None,
    market: MarketSnapshot | None = None,
    account: AccountState | None = None,
    positions: tuple[OpenPosition, ...] = (),
    sizing: SizingEstimate | None = None,
    audit: Any | None = None,
) -> tuple[RiskDecision, _Audit]:
    writer = audit or _Audit()
    decision = RiskGuardian(_limits(), writer).evaluate(
        intent or _intent(),
        market or _market(),
        account or _account(),
        positions,
        _sizing() if sizing is None and audit is None else sizing,
        now=NOW,
        cycle_id="cycle-test",
    )
    return decision, writer


def test_all_risk_rules_pass_and_decision_is_audited() -> None:
    decision, audit = _evaluate(sizing=_sizing())

    assert decision.approved
    assert decision.approved_size == Decimal("1")
    assert decision.reasons == ()
    assert decision.rules_checked == risk_rule_names()
    assert len(audit.events) == 1
    assert audit.events[0].event_type == "risk.decision"
    assert audit.events[0].cycle_id == "cycle-test"


def test_limits_load_from_the_checked_in_risk_config() -> None:
    values = yaml.safe_load(Path("config/risk.yaml").read_text(encoding="utf-8"))

    limits = RiskLimits.from_mapping(values)

    assert limits.risk_per_trade_pct == Decimal("0.5")
    assert limits.max_open_positions_total == 3
    assert limits.max_orders_per_hour == 600


def test_every_rule_is_evaluated_and_a_failed_rule_rejects() -> None:
    decision, audit = _evaluate(intent=_intent(stop_loss=None), sizing=_sizing())

    assert not decision.approved
    assert decision.approved_size is None
    assert "has_stop:stop_loss_required" in decision.reasons
    assert len(decision.rules_checked) == 20
    assert len(audit.events) == 1
    assert audit.events[0].payload["approved"] is False


def test_stop_distance_rejects_stops_below_or_above_atr_limits() -> None:
    too_tight, _ = _evaluate(intent=_intent(stop_loss=Decimal("99.9")), sizing=_sizing())
    too_wide, _ = _evaluate(intent=_intent(stop_loss=Decimal("96")), sizing=_sizing())

    assert any(reason.startswith("stop_distance:") for reason in too_tight.reasons)
    assert any(reason.startswith("stop_distance:") for reason in too_wide.reasons)


@given(
    risk=st.decimals(min_value="50.01", max_value="1000", places=2, allow_nan=False),
    stop=st.one_of(st.none(), st.decimals(min_value="1", max_value="99", places=2)),
)
def test_property_never_approves_without_stop_or_over_trade_risk(
    risk: Decimal, stop: Decimal | None
) -> None:
    decision, _ = _evaluate(
        intent=_intent(stop_loss=stop),
        sizing=_sizing(risk_amount=risk),
    )

    assert not decision.approved
    assert decision.approved_size is None


def test_rejects_missing_sizing_estimate() -> None:
    decision, _ = _evaluate(sizing=None, audit=_Audit())

    assert not decision.approved
    assert decision.approved_size is None
    assert any("sizing" in reason for reason in decision.reasons)


def test_rejects_and_does_not_leak_database_error_if_audit_fails(caplog: Any) -> None:
    decision, _ = _evaluate(sizing=_sizing(), audit=_BrokenAudit())

    assert not decision.approved
    assert decision.approved_size is None
    assert decision.reasons[-1] == "audit_write_failed"
    assert not decision.audit_written
    assert "database detail must not leak" not in caplog.text


def test_risk_per_trade_rule_enforces_equity_percentage() -> None:
    decision, _ = _evaluate(sizing=_sizing(risk_amount=Decimal("50.01")))
    assert any(reason.startswith("risk_per_trade:") for reason in decision.reasons)


def test_daily_and_weekly_losses_reject_at_threshold() -> None:
    daily, _ = _evaluate(account=_account(daily_pnl=Decimal("-200")), sizing=_sizing())
    weekly, _ = _evaluate(account=_account(weekly_pnl=Decimal("-500")), sizing=_sizing())
    assert any(reason.startswith("daily_loss:") for reason in daily.reasons)
    assert any(reason.startswith("weekly_loss:") for reason in weekly.reasons)


def test_drawdown_halts_until_durable_manual_reset_state_is_cleared() -> None:
    threshold, _ = _evaluate(account=_account(equity=Decimal("9000")), sizing=_sizing())
    persisted_halt, _ = _evaluate(account=_account(drawdown_halt_active=True), sizing=_sizing())

    assert threshold.halt_system
    assert persisted_halt.halt_system
    assert any(reason.startswith("drawdown:") for reason in persisted_halt.reasons)


def test_open_position_caps_apply_total_and_per_instrument() -> None:
    positions = (
        OpenPosition("NAS100", Decimal(10), Decimal(100), Decimal(1000)),
        OpenPosition("XAUUSD", Decimal(10), Decimal(100), Decimal(1000)),
        OpenPosition("EURUSD", Decimal(10), Decimal(100), Decimal(1000)),
    )
    decision, _ = _evaluate(positions=positions, sizing=_sizing())
    assert any(reason.startswith("open_positions:") for reason in decision.reasons)

    per_symbol, _ = _evaluate(
        positions=(
            OpenPosition("NAS100", Decimal(10), Decimal(100), Decimal(1000)),
            OpenPosition("NAS100", Decimal(10), Decimal(100), Decimal(1000)),
        ),
        sizing=_sizing(),
    )
    assert any(reason.startswith("open_positions:") for reason in per_symbol.reasons)


def test_combined_risk_margin_and_leverage_caps() -> None:
    combined, _ = _evaluate(
        positions=(OpenPosition("XAUUSD", Decimal(91), Decimal(100), Decimal(1000)),),
        sizing=_sizing(risk_amount=Decimal(10)),
    )
    margin, _ = _evaluate(
        account=_account(margin_used=Decimal(4000)),
        sizing=_sizing(margin_required=Decimal(1)),
    )
    leverage, _ = _evaluate(
        account=_account(notional_exposure=Decimal(50000)),
        sizing=_sizing(notional_exposure=Decimal(1)),
    )
    assert any(reason.startswith("combined_exposure:") for reason in combined.reasons)
    assert any(reason.startswith("margin_usage:") for reason in margin.reasons)
    assert any(reason.startswith("leverage:") for reason in leverage.reasons)


def test_market_closed_break_and_scheduled_event_reject() -> None:
    closed, _ = _evaluate(market=_market(market_open=False), sizing=_sizing())
    in_break, _ = _evaluate(market=_market(in_break=True), sizing=_sizing())
    scheduled, _ = _evaluate(
        market=_market(scheduled_events=(NewsEvent(NOW + timedelta(minutes=10), False),)),
        sizing=_sizing(),
    )
    assert any(reason.startswith("market_open:") for reason in closed.reasons)
    assert any(reason.startswith("market_open:") for reason in in_break.reasons)
    assert any(reason.startswith("market_open:") for reason in scheduled.reasons)


def test_high_impact_news_blackout_rejects() -> None:
    decision, _ = _evaluate(
        market=_market(scheduled_events=(NewsEvent(NOW - timedelta(minutes=5), True),)),
        sizing=_sizing(),
    )
    assert any(reason.startswith("news_blackout:") for reason in decision.reasons)


def test_stale_future_or_naive_market_time_rejects() -> None:
    stale, _ = _evaluate(
        market=_market(latest_data_at=NOW - timedelta(seconds=21)), sizing=_sizing()
    )
    future, _ = _evaluate(
        market=_market(latest_data_at=NOW + timedelta(seconds=1)), sizing=_sizing()
    )
    naive, _ = _evaluate(
        market=_market(latest_data_at=datetime(2026, 10, 3, 12, 0)), sizing=_sizing()
    )
    assert any(reason.startswith("data_fresh:") for reason in stale.reasons)
    assert any(reason.startswith("data_fresh:") for reason in future.reasons)
    assert any(reason.startswith("data_fresh:") for reason in naive.reasons)


def test_spread_ratio_and_instrument_spread_ceiling_reject() -> None:
    ratio, _ = _evaluate(market=_market(bid=Decimal("99"), ask=Decimal("102")), sizing=_sizing())
    ceiling, _ = _evaluate(market=_market(spread_ceiling=Decimal("0.05")), sizing=_sizing())
    assert any(reason.startswith("spread:") for reason in ratio.reasons)
    assert any(reason.startswith("spread:") for reason in ceiling.reasons)


def test_order_rate_and_stage_size_caps_reject() -> None:
    order_rate, _ = _evaluate(account=_account(orders_last_hour=600), sizing=_sizing())
    stage_cap, _ = _evaluate(
        sizing=_sizing(size=Decimal("2.01")),
    )
    assert any(reason.startswith("order_rate:") for reason in order_rate.reasons)
    assert any(reason.startswith("stage_cap:") for reason in stage_cap.reasons)


def test_quality_and_reward_risk_reject() -> None:
    quality, _ = _evaluate(intent=_intent(trade_quality_score=Decimal("0.69")), sizing=_sizing())
    reward_risk, _ = _evaluate(intent=_intent(take_profit=Decimal("100.5")), sizing=_sizing())
    assert any(reason.startswith("trade_quality:") for reason in quality.reasons)
    assert any(reason.startswith("reward_risk:") for reason in reward_risk.reasons)


def test_loss_streak_cooldown_and_manual_pause_reject() -> None:
    cooldown, _ = _evaluate(
        account=_account(
            consecutive_losses=4,
            last_loss_at=NOW - timedelta(minutes=10),
        ),
        sizing=_sizing(),
    )
    paused, _ = _evaluate(account=_account(paused=True), sizing=_sizing())
    assert any(reason.startswith("loss_streak:") for reason in cooldown.reasons)
    assert any(reason.startswith("kill_pause:") for reason in paused.reasons)


def test_loss_streak_allows_after_cooldown_expires() -> None:
    decision, _ = _evaluate(
        account=_account(
            consecutive_losses=4,
            last_loss_at=NOW - timedelta(minutes=31),
        ),
        sizing=_sizing(),
    )
    assert decision.approved


def test_short_direction_reward_risk_is_calculated_correctly() -> None:
    decision, _ = _evaluate(
        intent=_intent(
            direction="sell",
            stop_loss=Decimal("101"),
            take_profit=Decimal("98"),
        ),
        sizing=_sizing(),
    )
    assert decision.approved
