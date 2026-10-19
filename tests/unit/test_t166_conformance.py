from datetime import UTC, datetime, timedelta

from scripts import lab_t162_pine_port as port

from trader.data.resample import Candle


def _bars(
    count: int, *, day: int = 6, close: float = 100.0, start: datetime | None = None
) -> tuple[Candle, ...]:
    first = start or datetime(2026, 10, day, tzinfo=UTC)
    return tuple(
        Candle(first + timedelta(minutes=3 * i), close, close + 1.0, close - 1.0, close, 1.0)
        for i in range(count)
    )


def _aligned_start() -> datetime:
    # First 54-minute bucket boundary (epoch-aligned, as the port groups bars) after 2026-10-06.
    minutes = int(datetime(2026, 10, 6, tzinfo=UTC).timestamp() // 60)
    return datetime.fromtimestamp((minutes // 54 + 1) * 54 * 60, UTC)


def _features(
    count: int, *, atr: float = 1.0, ema: float = 99.0, vwap: float = 100.0
) -> port.Features:
    return port.Features(
        ema=(ema,) * count,
        atr=(atr,) * count,
        adx=(25.0,) * count,
        plus_di=(20.0,) * count,
        minus_di=(10.0,) * count,
        vwap=(vwap,) * count,
        ha_open=(99.0,) * count,
        ha_close=(101.0,) * count,
        buy_cross=(False,) * count,
        sell_cross=(False,) * count,
    )


def test_t166_vwap_resets_each_chart_day_and_uses_typical_price_for_zero_volume():
    bars = (
        Candle(datetime(2026, 10, 6, 23, 57, tzinfo=UTC), 9.0, 12.0, 6.0, 9.0, 0.0),
        Candle(datetime(2026, 10, 7, 0, 0, tzinfo=UTC), 19.0, 22.0, 16.0, 19.0, 0.0),
    )
    assert port._vwap(bars) == (9.0, 19.0)


def test_t166_structure_risk_uses_previous_bars_and_atr_buffer():
    bars = _bars(6)
    bars = tuple(
        Candle(bar.ts_utc, bar.open, bar.high, 95.0 if i < 5 else 99.0, bar.close, bar.volume)
        for i, bar in enumerate(bars)
    )
    ok, risk = port._risk(bars, _features(6, atr=1.0), 5, "long")
    # Pine uses low[1]..low[5], so the current bar's low=99 is excluded:
    # stop=95-0.20 and entry=100, giving 5.20R, above the 2 ATR cap.
    assert abs(risk - 5.2) < 1e-9
    assert not ok


def test_t166_smart_filter_requires_direction_and_location():
    bars = _bars(1, close=100.0)
    assert port._smart_ok(bars, _features(1, vwap=99.0), 0, "long")
    assert not port._smart_ok(bars, _features(1, vwap=98.0), 0, "long")
    assert not port._smart_ok(bars, _features(1, ema=101.0, vwap=99.0), 0, "long")


def test_t166_closed_ha_values_are_lookahead_off():
    bars = _bars(36, start=_aligned_start())
    opens, closes = port._ha_series(bars, "closed")
    # Pine request.security(..., lookahead_off) exposes the prior confirmed
    # 54-minute value throughout the next group; the first group is unconfirmed.
    assert opens[:18] == (None,) * 18
    assert closes[:18] == (None,) * 18
    assert all(value is not None for value in opens[18:])
    assert all(value is not None for value in closes[18:])


def test_t166_scope_records_unsupported_setup_and_source_defaults():
    # The port's public contract is deliberately Open/Close only until the owner
    # supplies the Renko and Inputs/Style settings; this prevents guessed rules.
    assert port.TF_MULT == 18
    assert port.REFRESH_BARS == 12
    assert port.START_DATE.isoformat() == "2023-01-01T00:00:00+00:00"
