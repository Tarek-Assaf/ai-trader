from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from trader.execution.brokers.base import Direction
from trader.execution.control import ControlMode
from trader.experiments.tai_demo import (
    ClosedBar,
    TaiDemoRefused,
    TaiDemoRunner,
    TaiDemoSettings,
    TaiInstrumentRules,
    TaiSignal,
)
from trader.experiments.tai_wiring import (
    TaiMinuteBar,
    TaiOrderFactory,
    ThreeMinuteBarBuilder,
    parse_tai_instrument_rules,
)


def minute(at: datetime, close: str = "100") -> TaiMinuteBar:
    value = Decimal(close)
    return TaiMinuteBar(at, "NSXUSD", value, value + 1, value - 1, value)


def test_three_minute_builder_requires_all_members_and_preserves_flags() -> None:
    start = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)
    builder = ThreeMinuteBarBuilder("NSXUSD")
    assert builder.add(minute(start)) is None
    assert builder.add(minute(start + timedelta(minutes=2))) is None
    result = builder.add(TaiMinuteBar(
        start + timedelta(minutes=1),
        "NSXUSD",
        Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100"),
        market_open=False,
    ))
    assert result is not None
    assert result.at == start
    assert result.open == Decimal("100")
    assert result.high == Decimal("101")
    assert result.low == Decimal("99")
    assert result.market_open is False
    assert builder.add(minute(start + timedelta(minutes=3))) is None


def test_three_minute_builder_does_not_fill_gaps() -> None:
    start = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)
    builder = ThreeMinuteBarBuilder("NSXUSD")
    assert builder.extend((minute(start), minute(start + timedelta(minutes=2)))) == ()


def test_capital_rules_parser_requires_distance_and_preserves_values() -> None:
    payload = {
        "instrument": {"epic": "NSXUSD", "tickSize": "0.1", "valuePerPoint": "1"},
        "dealingRules": {
            "minDealSize": {"value": "0.1", "unit": "AMOUNT"},
            "minSizeIncrement": {"value": "0.1", "unit": "AMOUNT"},
            "minStopOrLimitDistance": {"value": "10", "unit": "POINTS"},
        },
    }
    rules = parse_tai_instrument_rules(payload)
    assert rules.min_size == Decimal("0.1")
    assert rules.size_step == Decimal("0.1")
    assert rules.tick_size == Decimal("0.1")
    assert rules.min_stop_distance == Decimal("1.0")
    with pytest.raises(ValueError, match="missing minStop"):
        parse_tai_instrument_rules({"instrument": payload["instrument"], "dealingRules": {}})


def test_order_factory_rounds_price_levels_and_rejects_short_stop() -> None:
    rules = TaiInstrumentRules(
        Decimal("0.1"), Decimal("0.1"), Decimal("1"), Decimal("0.1"), Decimal("1"), Decimal("1")
    )
    factory = TaiOrderFactory("NSXUSD", lambda _epic: rules)
    signal = TaiSignal(Direction.BUY, Decimal("100.03"), Decimal("98.94"))
    order = factory(signal, "cycle-1", Decimal("0.1"))
    assert order.stop_level == Decimal("98.9")
    assert order.profit_level == Decimal("103.3")
    close_rules = TaiInstrumentRules(
        Decimal("0.1"), Decimal("0.1"), Decimal("1"), Decimal("0.1"), Decimal("2"), Decimal("2")
    )
    with pytest.raises(TaiDemoRefused, match="minimum stop"):
        TaiOrderFactory("NSXUSD", lambda _epic: close_rules)(signal, "cycle-1", Decimal("0.1"))


def test_dry_run_journals_without_submitting() -> None:
    class Control:
        def read(self) -> ControlMode:
            return ControlMode.RUN

    class Positions:
        def get_positions(self) -> tuple[object, ...]:
            return ()

    class Orders:
        def __init__(self) -> None:
            self.calls = 0

        def submit(self, _cycle_id: str, _order: object) -> None:
            self.calls += 1

    events: list[dict[str, object]] = []
    orders = Orders()
    rules = TaiInstrumentRules(
        Decimal("0.1"), Decimal("0.1"), Decimal("1"), Decimal("0.1"), Decimal("1"), Decimal("1")
    )
    runner = TaiDemoRunner(
        settings=TaiDemoSettings("demo", "paper", False, True, dry_run=True),
        order_manager=orders,
        position_gateway=Positions(),
        signal_provider=lambda _bar: TaiSignal(Direction.BUY, Decimal("100.03"), Decimal("98.94")),
        order_factory=TaiOrderFactory("NSXUSD", lambda _epic: rules),
        control=Control(),
        balance_provider=lambda: Decimal("1000"),
        instrument_rules_provider=lambda _epic: rules,
        journal=events.append,
    )
    bar = ClosedBar(
        datetime(2026, 10, 19, 8, 0, tzinfo=UTC),
        "NSXUSD",
        Decimal("100"),
        Decimal("101"),
        Decimal("99"),
        Decimal("100"),
    )
    outcome = runner.on_bar(bar)
    assert outcome.action == "DRY_RUN"
    assert orders.calls == 0
    assert any(event["event"] == "dry_run_would_submit" for event in events)
