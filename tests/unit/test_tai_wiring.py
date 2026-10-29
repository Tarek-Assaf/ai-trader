from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from scripts import lab_t162_pine_port as port

import trader.experiments.tai_wiring as tai_wiring
from trader.data.resample import Candle
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
    REFRESH_BARS,
    CapitalInstrumentRulesProvider,
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


def test_capital_rules_parser_accepts_live_capital_us100_shape() -> None:
    payload = {
        "instrument": {"epic": "US100", "lotSize": 1},
        "dealingRules": {
            "minDealSize": {"value": "0.001", "unit": "POINTS"},
            "minSizeIncrement": {"value": "0.001", "unit": "POINTS"},
            "minStopOrProfitDistance": {"value": "0.01", "unit": "PERCENTAGE"},
        },
        "snapshot": {
            "bid": "28928.1",
            "offer": "28929.9",
            "decimalPlacesFactor": 1,
        },
    }

    rules = parse_tai_instrument_rules(payload)

    assert rules.tick_size == Decimal("0.1")
    assert rules.value_per_point == Decimal("1")
    assert rules.min_stop_distance == Decimal("2.89299")
    assert rules.min_limit_distance == Decimal("2.89299")


def test_capital_rules_provider_enriches_tick_from_market_search() -> None:
    class Client:
        def search_markets(self, epic: str) -> list[dict[str, object]]:
            assert epic == "US100"
            return [{"epic": epic, "tickSize": "0.1"}]

        def market_details(self, epic: str) -> dict[str, object]:
            assert epic == "US100"
            return {
                "instrument": {"epic": epic, "lotSize": 1},
                "dealingRules": {
                    "minDealSize": {"value": "0.001", "unit": "POINTS"},
                    "minSizeIncrement": {"value": "0.001", "unit": "POINTS"},
                    "minStopOrProfitDistance": {"value": "0.01", "unit": "PERCENTAGE"},
                },
                "snapshot": {"bid": "28928.1", "offer": "28929.9"},
            }

    rules = CapitalInstrumentRulesProvider(Client(), "US100")()

    assert rules.tick_size == Decimal("0.1")
    assert rules.min_stop_distance == Decimal("2.89299")


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


def test_signal_features_forming_ha_exposes_current_54m_prefix() -> None:
    """Realtime mode updates the 54m HA close before the group is complete."""
    start = datetime(2026, 10, 1, 22, 0, tzinfo=UTC)
    bars = tuple(
        ClosedBar(
            start + timedelta(minutes=index * 3),
            "NSXUSD",
            Decimal("100") + Decimal(index) / Decimal("10"),
            Decimal("101") + Decimal(index) / Decimal("10"),
            Decimal("99") + Decimal(index) / Decimal("10"),
            Decimal("100.5") + Decimal(index) / Decimal("10"),
        )
        for index in range(36)
    )

    closed = _features(bars)
    forming = _features(bars, ha_mode="forming")

    assert closed["buy"][0:18] == (False,) * 18
    assert closed["sell"][0:18] == (False,) * 18
    assert all(value is None for value in _ha_values(closed, "open", 18))
    assert all(value is not None for value in _ha_values(forming, "close", 18))
    assert len(set(_ha_values(forming, "close", 18))) > 1


def test_forming_ha_can_cross_before_the_54m_group_closes() -> None:
    """A live-style SELL can appear in the current group before closed mode can."""
    start = datetime(2026, 10, 1, 22, 0, tzinfo=UTC)
    first_group = tuple(
        ClosedBar(
            start + timedelta(minutes=index * 3),
            "NSXUSD",
            Decimal("100"),
            Decimal("112"),
            Decimal("99"),
            Decimal("110"),
        )
        for index in range(18)
    )
    second_group = tuple(
        ClosedBar(
            start + timedelta(minutes=(18 + index) * 3),
            "NSXUSD",
            Decimal("110"),
            Decimal("111"),
            Decimal("108"),
            Decimal("110"),
        )
        for index in range(17)
    ) + (
        ClosedBar(
            start + timedelta(minutes=35 * 3),
            "NSXUSD",
            Decimal("110"),
            Decimal("111"),
            Decimal("90"),
            Decimal("91"),
        ),
    )
    bars = first_group + second_group

    closed = _features(bars)
    forming = _features(bars, ha_mode="forming")

    assert not any(closed["sell"][18:])
    assert forming["sell"][35]


def _group_bars(skip: set[int], count: int = 36) -> tuple[ClosedBar, ...]:
    start = datetime(2026, 10, 1, 22, 0, tzinfo=UTC)
    return tuple(
        ClosedBar(
            start + timedelta(minutes=index * 3),
            "NSXUSD",
            Decimal("100") + Decimal(index) / Decimal("10"),
            Decimal("101") + Decimal(index) / Decimal("10"),
            Decimal("99") + Decimal(index) / Decimal("10"),
            Decimal("100.5") + Decimal(index) / Decimal("10"),
        )
        for index in range(count)
        if index not in skip
    )


def test_ha_group_with_small_recorder_hole_still_advances_ha_state() -> None:
    """One missing 3m bar must not freeze the 54m HA chain (TradingView has no holes)."""
    holed = _features(_group_bars({5}))
    assert all(value is not None for value in holed["ha_open"][17:])
    assert all(value is not None for value in holed["ha_close"][17:])


def test_ha_group_with_big_hole_is_still_incomplete() -> None:
    """A group missing more than two bars is not a full 54m block."""
    holed = _features(_group_bars({3, 4, 5}))
    assert all(value is None for value in holed["ha_open"][15:])


def _ha_values(
    features: dict[str, tuple[object, ...]], name: str, count: int
) -> tuple[object, ...]:
    """Read the private HA arrays exposed by the forming-mode test fixture."""
    # The production feature map intentionally keeps these values private. This
    # test only needs a stable projection to check the mode's prefix behavior.
    return tuple(features[f"ha_{name}"][:count])


def test_signal_provider_rejects_unknown_ha_mode() -> None:
    with pytest.raises(ValueError, match="ha_mode"):
        TaiSignalProvider(ha_mode="unknown")


def test_signal_provider_keeps_ema_seed_across_long_series_and_restart() -> None:
    start = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    bars = []
    for index in range(850):
        close = Decimal("100") + Decimal(index % 37) / Decimal("10")
        bars.append(
            ClosedBar(
                start + timedelta(minutes=index * 3),
                "NSXUSD",
                close,
                close + Decimal("1"),
                close - Decimal("1"),
                close,
            )
        )

    continuous = TaiSignalProvider(bars[:300], minimum_history=300)
    for bar in bars[300:]:
        continuous(bar)
    reference = _features(bars)["ema"][-1]

    assert len(continuous.history) == len(bars)
    assert continuous.history == tuple(bars)
    assert continuous.history and _features(continuous.history)["ema"][-1] == pytest.approx(
        reference
    )

    restarted = TaiSignalProvider(continuous.history[:650], minimum_history=300)
    for bar in continuous.history[650:]:
        restarted(bar)
    assert restarted.history == tuple(bars)
    assert _features(restarted.history)["ema"][-1] == pytest.approx(reference)


def _chain_bar(start: datetime, index: int, *, low: str = "100") -> ClosedBar:
    return ClosedBar(
        start + timedelta(minutes=index * 3),
        "NSXUSD",
        Decimal("101"),
        Decimal("102"),
        Decimal(low),
        Decimal("101"),
    )


def _patch_chain_features(monkeypatch: pytest.MonkeyPatch, crosses: set[int]) -> None:
    """Constant bullish features; a Heikin-Ashi buy cross only at the given indexes."""

    def features(
        current: tuple[ClosedBar, ...], *, ha_mode: str = "closed"
    ) -> dict[str, tuple[object, ...]]:
        assert ha_mode == "closed"
        size = len(current)
        return {
            "ema": (99.0,) * size,
            "atr": (1.0,) * size,
            "adx": (30.0,) * size,
            "plus_di": (30.0,) * size,
            "minus_di": (10.0,) * size,
            "vwap": (100.0,) * size,
            "buy": tuple(index in crosses for index in range(size)),
            "sell": (False,) * size,
        }

    monkeypatch.setattr(tai_wiring, "_features", features)


def test_signal_provider_refreshes_after_the_virtual_chain_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-223: the refresh anchor is the indicator's simulated exit, not our own fills."""
    start = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    history = tuple(_chain_bar(start, index) for index in range(300))
    _patch_chain_features(monkeypatch, {300})
    provider = TaiSignalProvider(history, minimum_history=300)

    entry = provider(_chain_bar(start, 300))
    assert entry is not None
    assert entry.direction is Direction.BUY
    assert entry.stop == Decimal("99.8")

    # The next bar trades through the stop, so the simulated trade exits at index 301.
    assert provider(_chain_bar(start, 301, low="99")) is None
    for index in range(302, 301 + REFRESH_BARS):
        assert provider(_chain_bar(start, index)) is None
        assert provider.last_failure_gate == "ha_cross"
    refresh = provider(_chain_bar(start, 301 + REFRESH_BARS))
    assert refresh is not None
    assert refresh.direction is Direction.BUY


def test_signal_provider_blocks_a_second_signal_while_the_virtual_trade_is_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    history = tuple(_chain_bar(start, index) for index in range(300))
    _patch_chain_features(monkeypatch, {300, 301})
    provider = TaiSignalProvider(history, minimum_history=300)
    assert provider(_chain_bar(start, 300)) is not None
    assert provider(_chain_bar(start, 301)) is None
    assert provider.last_failure_gate == "chain_in_trade"


def test_signal_provider_does_not_refresh_from_a_real_exit_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    start = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    history = tuple(_chain_bar(start, index) for index in range(300))
    _patch_chain_features(monkeypatch, set())
    provider = TaiSignalProvider(history, minimum_history=300)
    provider.note_exit(history[-1].at)
    for index in range(300, 300 + REFRESH_BARS + 5):
        assert provider(_chain_bar(start, index)) is None
        assert provider.last_failure_gate == "ha_cross"


def test_signal_provider_replays_the_chain_over_warmup_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A restart must land on the same chain: an exit inside history enables refresh."""
    start = datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    bars = [_chain_bar(start, index) for index in range(300)]
    bars[290] = _chain_bar(start, 290, low="99")  # simulated stop-out of the entry at 289
    _patch_chain_features(monkeypatch, {289})
    provider = TaiSignalProvider(tuple(bars), minimum_history=300)
    # Refresh is due at 290 + REFRESH_BARS = 302; indexes 300 and 301 are still too early.
    assert provider(_chain_bar(start, 300)) is None
    assert provider(_chain_bar(start, 301)) is None
    refresh = provider(_chain_bar(start, 302))
    assert refresh is not None
    assert refresh.direction is Direction.BUY


def test_signal_provider_chain_matches_t162_port_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T-223: with identical indicator inputs the runtime chain equals the closed port.

    Indicator maths is covered by the T-166 conformance tests; here the feature arrays are
    injected (long-only, random Heikin-Ashi crosses) so the test compares only the entry,
    12-bar refresh and target/stop management logic over many simulated trades.
    """
    import random

    rnd = random.Random(7)
    count = 1500
    start = datetime(2026, 3, 2, 0, 0, tzinfo=UTC)
    raw = []
    price = 20000.0
    for index in range(count):
        open_ = price
        close = open_ + rnd.gauss(0, 3.0)
        high = max(open_, close) + abs(rnd.gauss(0, 2.0))
        low = min(open_, close) - abs(rnd.gauss(0, 2.0))
        raw.append((start + timedelta(minutes=3 * index), open_, high, low, close))
        price = close
    flags = tuple(rnd.random() < 0.04 for _ in range(count))
    candles = tuple(Candle(at, o, h, low_, c, 0.0) for at, o, h, low_, c in raw)
    closed = tuple(
        ClosedBar(at, "X", Decimal(str(o)), Decimal(str(h)), Decimal(str(low_)), Decimal(str(c)))
        for at, o, h, low_, c in raw
    )

    def port_features(bars: tuple[Candle, ...], *_args: object) -> port.Features:
        size = len(bars)
        return port.Features(
            ema=tuple(bar.close - 5 for bar in bars),
            atr=(2.0,) * size,
            adx=(30.0,) * size,
            plus_di=(30.0,) * size,
            minus_di=(10.0,) * size,
            vwap=tuple(bar.close - 0.5 for bar in bars),
            ha_open=(None,) * size,
            ha_close=(None,) * size,
            buy_cross=flags[:size],
            sell_cross=(False,) * size,
        )

    def wiring_features(
        bars: tuple[ClosedBar, ...], *, ha_mode: str = "closed"
    ) -> dict[str, tuple[object, ...]]:
        size = len(bars)
        return {
            "ema": tuple(float(bar.close) - 5 for bar in bars),
            "atr": (2.0,) * size,
            "adx": (30.0,) * size,
            "plus_di": (30.0,) * size,
            "minus_di": (10.0,) * size,
            "vwap": tuple(float(bar.close) - 0.5 for bar in bars),
            "buy": flags[:size],
            "sell": (False,) * size,
        }

    monkeypatch.setattr(port, "compute_features", port_features)
    monkeypatch.setattr(tai_wiring, "_features", wiring_features)
    trades, _ = port.replay(candles, "closed", "session_1800_ny", "session_1800_ny")
    expected = [trade.entry_time for trade in trades if trade.entry_time >= closed[299].at]
    provider = TaiSignalProvider(minimum_history=300)
    actual = [bar.at for bar in closed if provider(bar) is not None]
    assert len(expected) > 20
    assert actual == expected


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
        Decimal("0.1"),
        Decimal("0.1"),
        Decimal("1"),
        Decimal("0.1"),
        Decimal("1"),
        Decimal("1"),
        bid=Decimal("100"),
        ask=Decimal("100.1"),
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


def _bars_with_lows(lows: list[str], first: datetime) -> list[ClosedBar]:
    result = []
    for index, low in enumerate(lows):
        low_value = Decimal(low)
        result.append(
            ClosedBar(
                first + timedelta(minutes=3 * index),
                "US100",
                low_value + 2,
                low_value + 6,
                low_value,
                low_value + 3,
            )
        )
    return result


def _quote_rules(bid: str, ask: str) -> TaiInstrumentRules:
    return TaiInstrumentRules(
        Decimal("0.001"),
        Decimal("0.001"),
        Decimal("1"),
        Decimal("0.1"),
        Decimal("1"),
        Decimal("1"),
        bid=Decimal(bid),
        ask=Decimal(ask),
    )


def test_external_signal_uses_the_five_bars_before_the_signal_bar_for_the_stop() -> None:
    first = datetime(2026, 10, 28, 11, 30, tzinfo=UTC)
    # 13 bars from 11:30 to 12:06; the last five (11:54..12:06) decide the stop
    lows = [
        "500",
        "400",
        "300",
        "290",
        "280",
        "270",
        "260",
        "250",
        "260",
        "250",
        "240",
        "235",
        "999",
    ]
    provider = TaiSignalProvider(_bars_with_lows(lows, first))
    provider._latest_atr = Decimal("10")
    # 12:12:05 -> the signal bar started 12:09, so the bars before it end at 12:06
    now = datetime(2026, 10, 28, 12, 12, 5, tzinfo=UTC)
    buy = provider.external_signal(Direction.BUY, _quote_rules("600", "601.8"), now)
    assert buy.entry == Decimal("601.8")
    assert buy.stop == Decimal("235") - Decimal("2.0")
    sell = provider.external_signal(Direction.SELL, _quote_rules("600", "601.8"), now)
    assert sell.entry == Decimal("600")
    assert sell.stop == Decimal("999") + Decimal("6") + Decimal("2.0")


def test_external_signal_refuses_when_atr_or_history_is_missing() -> None:
    first = datetime(2026, 10, 28, 11, 30, tzinfo=UTC)
    now = datetime(2026, 10, 28, 12, 12, 5, tzinfo=UTC)
    no_atr = TaiSignalProvider(_bars_with_lows(["500"] * 12 + ["650"], first))
    with pytest.raises(ValueError, match="ATR"):
        no_atr.external_signal(Direction.BUY, _quote_rules("1", "2"), now)
    short = TaiSignalProvider(_bars_with_lows(["500"] * 3, first))
    short._latest_atr = Decimal("10")
    with pytest.raises(ValueError, match="closed bars"):
        short.external_signal(Direction.BUY, _quote_rules("1", "2"), now)


def test_epic_aliases_cover_both_instruments_and_unknown_epics() -> None:
    from trader.experiments.tai_wiring import epic_aliases

    assert {"US100", "NAS100", "NSXUSD"} <= epic_aliases("US100")
    assert {"GOLD", "XAUUSD"} <= epic_aliases("GOLD")
    assert epic_aliases("DAX") == frozenset({"DAX"})


def test_runtime_group_routes_each_bar_to_its_instrument_and_merges_health() -> None:
    from types import SimpleNamespace

    from trader.experiments.tai_wiring import TaiRuntimeGroup

    def adapter(epic: str):
        calls = []

        def run(cycle):
            calls.append(cycle.event_id)
            return SimpleNamespace(action="NO_TRADE", reason=epic, cycle_id=cycle.event_id)

        return SimpleNamespace(
            run=run,
            runner=f"runner-{epic}",
            health_snapshot=lambda: {epic: {"evaluated": 1}},
            snapshot_state=lambda: {f"deal-{epic}": epic},
            pending_bars=lambda: {f"bar:{epic}": epic},
            calls=calls,
        )

    us100, gold = adapter("US100"), adapter("GOLD")
    group = TaiRuntimeGroup({"US100": us100, "GOLD": gold})
    stamp = "2026-10-28T20:12:00+00:00"
    assert group.run(SimpleNamespace(event_id=f"bar:3M:GOLD:{stamp}")).reason == "GOLD"
    assert group.run(SimpleNamespace(event_id=f"bar:3M:US100:{stamp}")).reason == "US100"
    assert group.run(SimpleNamespace(event_id=f"bar:3M:DAX:{stamp}")).reason == "bar_unavailable"
    assert gold.calls == [f"bar:3M:GOLD:{stamp}"] and us100.calls == [f"bar:3M:US100:{stamp}"]
    assert set(group.health_snapshot()) == {"US100", "GOLD"}
    assert set(group.snapshot_state()) == {"deal-US100", "deal-GOLD"}
    assert group.runner == "runner-US100" and group.runner_for("GOLD") == "runner-GOLD"
    assert group.runner_for("DAX") is None


def test_external_signal_levels_are_whole_ticks() -> None:
    first = datetime(2026, 10, 28, 11, 30, tzinfo=UTC)
    provider = TaiSignalProvider(_bars_with_lows(["500"] * 12 + ["650"], first))
    provider._latest_atr = Decimal("7.77")
    now = datetime(2026, 10, 28, 12, 12, 5, tzinfo=UTC)
    rules = _quote_rules("600", "601.8")
    assert rules.tick_size == Decimal("0.1")
    for direction in (Direction.BUY, Direction.SELL):
        signal = provider.external_signal(direction, rules, now)
        for value in (signal.stop, *signal.levels()):
            assert value % Decimal("0.1") == 0
        assert len(signal.levels()) == 3
    sell = provider.external_signal(Direction.SELL, rules, now)
    assert sell.stop >= sell.entry  # never tighter than the indicator's stop


def test_external_signal_falls_back_to_one_atr_when_structure_is_on_the_wrong_side() -> None:
    first = datetime(2026, 10, 28, 11, 30, tzinfo=UTC)
    provider = TaiSignalProvider(_bars_with_lows(["500"] * 13, first))
    provider._latest_atr = Decimal("10")
    now = datetime(2026, 10, 28, 12, 12, 5, tzinfo=UTC)
    # Price has fallen far below the five-bar low: the structure stop would sit above a BUY.
    buy = provider.external_signal(Direction.BUY, _quote_rules("399", "400"), now)
    assert buy.stop == Decimal("390.0")
    assert "1 ATR" in buy.note
    # SELL with price far above the five-bar high.
    sell = provider.external_signal(Direction.SELL, _quote_rules("700", "701"), now)
    assert sell.stop == Decimal("710.0")
    assert sell.note
    # Normal case keeps the structure stop and has no note.
    normal = provider.external_signal(Direction.BUY, _quote_rules("600", "601.8"), now)
    assert normal.note == ""


def test_external_signal_targets_follow_the_tradingview_close_when_usable() -> None:
    first = datetime(2026, 10, 28, 11, 30, tzinfo=UTC)
    provider = TaiSignalProvider(_bars_with_lows(["500"] * 12 + ["650"], first))
    provider._latest_atr = Decimal("10")
    now = datetime(2026, 10, 28, 12, 12, 5, tzinfo=UTC)
    rules = _quote_rules("600", "601.8")
    base = provider.external_signal(Direction.SELL, rules, now)
    stop = base.stop
    reference = Decimal("610")  # TradingView sold 10 points above the bot's quote
    risk = stop - reference
    signal = provider.external_signal(Direction.SELL, rules, now, reference=reference)
    assert signal.stop == stop
    expected = tuple(
        ((reference - risk * multiple) / Decimal("0.1")).to_integral_value() * Decimal("0.1")
        for multiple in (1, 2, 3)
    )
    assert signal.levels() == expected
    assert "TradingView" in signal.note
    # Unusable references fall back to the bot's own multiples.
    own = base.levels()
    assert (
        provider.external_signal(Direction.SELL, rules, now, reference=Decimal("-1")).levels()
        == own
    )
    far = Decimal("900")  # more than 3 ATR from the quote
    assert provider.external_signal(Direction.SELL, rules, now, reference=far).levels() == own
    wrong_side = stop + 5
    assert (
        provider.external_signal(Direction.SELL, rules, now, reference=wrong_side).levels() == own
    )
    # TP1 already behind the live price: fall back.
    assert (
        provider.external_signal(Direction.SELL, rules, now, reference=Decimal("585")).levels()
        == own
    )
