from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

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
    TaiSignalProvider,
    ThreeMinuteBarBuilder,
    _features,
    build_tai_demo_runtime,
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
    result = builder.add(
        TaiMinuteBar(
            start + timedelta(minutes=1),
            "NSXUSD",
            Decimal("100"),
            Decimal("101"),
            Decimal("99"),
            Decimal("100"),
            market_open=False,
        )
    )
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
    with pytest.raises(ValueError, match="missing minDealSize"):
        parse_tai_instrument_rules({"instrument": payload["instrument"], "dealingRules": {}})


def test_capital_rules_parser_preserves_snapshot_quote() -> None:
    payload = {
        "instrument": {"epic": "NSXUSD", "tickSize": "0.1", "valuePerPoint": "1"},
        "dealingRules": {
            "minDealSize": {"value": "0.1", "unit": "AMOUNT"},
            "minSizeIncrement": {"value": "0.1", "unit": "AMOUNT"},
            "minStopOrLimitDistance": {"value": "10", "unit": "POINTS"},
        },
        "snapshot": {"bid": "19999.8", "offer": "20000.0"},
    }
    rules = parse_tai_instrument_rules(payload)
    assert rules.bid == Decimal("19999.8")
    assert rules.ask == Decimal("20000.0")


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


def test_order_factory_rounds_sell_levels_up_and_requires_tick_distance() -> None:
    rules = TaiInstrumentRules(
        Decimal("0.1"), Decimal("0.1"), Decimal("1"), Decimal("0.1"), Decimal("1"), Decimal("1")
    )
    factory = TaiOrderFactory("NSXUSD", lambda _epic: rules)
    signal = TaiSignal(Direction.SELL, Decimal("100.03"), Decimal("101.09"))
    order = factory(signal, "sell-cycle", Decimal("0.1"))
    assert order.stop_level == Decimal("101.1")
    assert order.profit_level == Decimal("96.9")
    missing = TaiInstrumentRules(Decimal("0.1"), Decimal("0.1"), Decimal("1"))
    with pytest.raises(TaiDemoRefused, match="tick and minimum distance"):
        TaiOrderFactory("NSXUSD", lambda _epic: missing)(signal, "missing", Decimal("0.1"))


def test_from_lake_reports_warmup_for_tiny_bid_ask_fixture(tmp_path: Path) -> None:
    pq = pytest.importorskip("pyarrow.parquet")
    import pyarrow as pa

    start = datetime(2026, 10, 19, 8, 0, tzinfo=UTC)
    rows = []
    for offset in range(3):
        stamp = start + timedelta(minutes=offset)
        for side, base in (("bid", 100.0), ("ask", 101.0)):
            rows.append(
                {
                    "epic": "NSXUSD",
                    "ts_utc": stamp,
                    "side": side,
                    "open": base,
                    "high": base + 1,
                    "low": base - 1,
                    "close": base + 0.5,
                }
            )
    path = tmp_path / "NSXUSD" / "1m_capital" / "2026" / "10.parquet"
    path.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(rows), path)
    provider = TaiSignalProvider.from_lake(tmp_path, "NSXUSD")
    assert provider.warmup_bars == 1
    assert provider.warmup_ready is False


def test_runtime_gate_does_not_register_on_unfed_api_recorder() -> None:
    class Settings:
        broker_env = "demo"
        trading_mode = "paper"
        live_trading_enabled = False
        tai_demo_experiment = True
        tai_demo_dry_run = True
        tai_demo_start_balance = "1000"
        tai_demo_base_lot = "0.1"

    class Recorder:
        def __init__(self) -> None:
            self.callback = None

        def register_closed_minute_callback(self, callback: object) -> None:
            self.callback = callback

    class Source:
        def emit(self, _event: object) -> None:
            pass

    recorder = Recorder()
    rules = TaiInstrumentRules(
        Decimal("0.1"), Decimal("0.1"), Decimal("1"), Decimal("0.1"), Decimal("1"), Decimal("1")
    )
    runtime = build_tai_demo_runtime(
        Settings(),
        epic="NSXUSD",
        order_manager=object(),
        position_gateway=object(),
        control=object(),
        source=Source(),
        builder=ThreeMinuteBarBuilder("NSXUSD"),
        balance_provider=lambda: Decimal("1000"),
        rules_provider=lambda _epic: rules,
        signal_provider=lambda _bar: None,
    )
    assert runtime is not None
    assert recorder.callback is None

    disabled_experiment = Settings()
    disabled_experiment.tai_demo_experiment = False
    assert (
        build_tai_demo_runtime(
            disabled_experiment,
            epic="NSXUSD",
            order_manager=object(),
            position_gateway=object(),
            control=object(),
            source=Source(),
            builder=ThreeMinuteBarBuilder("NSXUSD"),
            balance_provider=lambda: Decimal("1000"),
            rules_provider=lambda _epic: rules,
        )
        is None
    )

    for field, value in (
        ("broker_env", "live"),
        ("trading_mode", "auto"),
        ("live_trading_enabled", True),
    ):
        disabled = Settings()
        setattr(disabled, field, value)
        assert (
            build_tai_demo_runtime(
                disabled,
                epic="NSXUSD",
                order_manager=object(),
                position_gateway=object(),
                control=object(),
                source=Source(),
                builder=ThreeMinuteBarBuilder("NSXUSD"),
                balance_provider=lambda: Decimal("1000"),
                rules_provider=lambda _epic: rules,
            )
            is None
        )


def test_signal_features_match_t162_closed_fixture_with_buy_and_sell() -> None:
    from scripts import lab_t162_pine_port as port

    start = datetime(2026, 10, 1, 22, 0, tzinfo=UTC)
    bars = []
    for index in range(400):
        group = index // 18
        level = Decimal("100") if group % 2 == 0 else Decimal("120")
        drift = Decimal(index % 18) / Decimal("50")
        open_price = level + (drift if group % 2 == 0 else -drift)
        close = open_price + (Decimal("0.5") if group % 2 == 0 else Decimal("-0.5"))
        high = max(open_price, close) + Decimal("0.2")
        low = min(open_price, close) - Decimal("0.2")
        bars.append(
            ClosedBar(
                start + timedelta(minutes=index * 3),
                "NSXUSD",
                open_price,
                high,
                low,
                close,
            )
        )
    ours = _features(bars)
    candles = tuple(
        port.Candle(
            item.at,
            float(item.open),
            float(item.high),
            float(item.low),
            float(item.close),
            0.0,
            ("NSXUSD", "fixture"),
            True,
            3,
        )
        for item in bars
    )
    expected = port.compute_features(candles, "closed", "session_1800_ny")
    for name in ("ema", "atr", "adx", "plus_di", "minus_di", "vwap"):
        actual = ours[name]
        reference = getattr(expected, name)
        assert all(
            left == right or (left is not None and right is not None and abs(left - right) < 1e-9)
            for left, right in zip(actual, reference, strict=True)
        )
    assert ours["buy"] == expected.buy_cross
    assert ours["sell"] == expected.sell_cross
    assert any(ours["buy"])
    assert any(ours["sell"])


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
