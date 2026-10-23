"""Build-only wiring for the owner-directed Tai demo experiment.

The wiring in this module is deliberately deterministic and dependency injected.  It
can consume recorder minute bars and recorded broker metadata in tests without opening
a broker connection.  The production adapter is still guarded by the T-185 settings
gate and defaults to dry-run.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict, deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from trader.data.instruments import InstrumentDataError
from trader.data.resample import Candle
from trader.execution.brokers.base import Direction, OrderRequest
from trader.experiments.tai_demo import (
    Alert,
    ClosedBar,
    Control,
    DemoOutcome,
    Journal,
    OrderManager,
    PositionGateway,
    TaiDemoRefused,
    TaiDemoRunner,
    TaiDemoSettings,
    TaiInstrumentRules,
    TaiSignal,
    _ManagedPosition,
)

NY = ZoneInfo("America/New_York")
LOGGER = logging.getLogger(__name__)
EMA_PERIOD = 200
ADX_PERIOD = 14
SWING_LOOKBACK = 5
MAX_RISK_ATR = 2.0
VWAP_DISTANCE_ATR = 1.5
GROUP_BARS = 18
REFRESH_BARS = 12
HA_MODES = frozenset(("closed", "forming"))
EXPERIMENT_LABEL = "Tai port, owner-directed demo experiment, loosely matched to the owner"


@dataclass(frozen=True, slots=True)
class TaiMinuteBar:
    """A complete recorder minute bar used by the 3-minute builder."""

    at: datetime
    epic: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    market_open: bool = True
    in_break: bool = False
    complete: bool = True
    volume: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.at.tzinfo is None or self.at.utcoffset() is None:
            raise ValueError("minute timestamp must be timezone-aware")
        if self.at.second or self.at.microsecond:
            raise ValueError("minute timestamp must be on a minute boundary")
        if not self.epic.strip() or not self.complete:
            raise ValueError("minute bar must be complete and have an epic")
        values = (self.open, self.high, self.low, self.close)
        if any(
            not isinstance(value, Decimal) or not value.is_finite() or value <= 0
            for value in values
        ):
            raise ValueError("minute prices must be positive finite decimals")
        if not isinstance(self.volume, Decimal) or not self.volume.is_finite() or self.volume < 0:
            raise ValueError("minute volume must be a finite non-negative Decimal")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError("minute OHLC is invalid")


def _as_decimal(value: object, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite positive number") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError(f"{field} must be a finite positive number")
    return result


def _minute_stamp(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("bar timestamps must be timezone-aware")
    return value.astimezone(UTC).replace(second=0, microsecond=0)


class ThreeMinuteBarBuilder:
    """Aggregate closed recorder minutes without filling gaps or partial buckets."""

    def __init__(
        self,
        epic: str,
        *,
        market_open: Callable[[datetime], bool] | None = None,
        in_break: Callable[[datetime], bool] | None = None,
    ) -> None:
        if not epic.strip():
            raise ValueError("epic must not be empty")
        self.epic = epic.strip()
        self._market_open = market_open or (lambda _at: True)
        self._in_break = in_break or (lambda _at: False)
        self._minutes: dict[datetime, TaiMinuteBar] = {}
        self._emitted: set[datetime] = set()

    def add(self, minute: TaiMinuteBar) -> ClosedBar | None:
        """Add one minute and emit a 3M bar only when all three minutes exist."""
        if minute.epic != self.epic:
            return None
        stamp = _minute_stamp(minute.at)
        self._minutes[stamp] = minute
        bucket = stamp - timedelta(minutes=stamp.minute % 3)
        expected = tuple(bucket + timedelta(minutes=offset) for offset in range(3))
        if bucket in self._emitted or any(item not in self._minutes for item in expected):
            return None
        members = tuple(self._minutes[item] for item in expected)
        if not all(item.complete for item in members):
            return None
        self._emitted.add(bucket)
        return ClosedBar(
            at=bucket,
            epic=self.epic,
            open=members[0].open,
            high=max(item.high for item in members),
            low=min(item.low for item in members),
            close=members[-1].close,
            timeframe_minutes=3,
            closed=True,
            market_open=all(self._market_open(item.at) and item.market_open for item in members),
            in_break=any(self._in_break(item.at) or item.in_break for item in members),
            volume=sum((item.volume for item in members), Decimal("0")),
        )

    def extend(self, minutes: Iterable[TaiMinuteBar]) -> tuple[ClosedBar, ...]:
        result: list[ClosedBar] = []
        for minute in sorted(minutes, key=lambda item: item.at):
            bar = self.add(minute)
            if bar is not None:
                result.append(bar)
        return tuple(result)

    @property
    def pending_minutes(self) -> tuple[datetime, ...]:
        return tuple(sorted(self._minutes))


def minute_bar_from_candle(
    candle: Candle,
    epic: str,
    *,
    market_open: bool = True,
    in_break: bool = False,
) -> TaiMinuteBar:
    """Convert the shared float candle type at the recorder boundary."""
    return TaiMinuteBar(
        at=_minute_stamp(candle.ts_utc),
        epic=epic,
        open=Decimal(str(candle.open)),
        high=Decimal(str(candle.high)),
        low=Decimal(str(candle.low)),
        close=Decimal(str(candle.close)),
        market_open=market_open,
        in_break=in_break,
        complete=candle.complete and candle.n_minutes == 1,
        volume=Decimal(str(candle.volume)),
    )


def _rma(values: Sequence[Any], period: int) -> tuple[float | None, ...]:
    result: list[float | None] = [None] * len(values)
    valid = [index for index, value in enumerate(values) if value is not None]
    if not valid or len(values) - valid[0] < period:
        return tuple(result)
    first = valid[0]
    current = math.fsum(float(values[index]) for index in range(first, first + period)) / period
    result[first + period - 1] = current
    for index in range(first + period, len(values)):
        value = values[index]
        if value is not None:
            current = (current * (period - 1) + float(value)) / period
            result[index] = current
    return tuple(result)


def _ema(values: Sequence[float], period: int) -> tuple[float | None, ...]:
    result: list[float | None] = [None] * len(values)
    if len(values) < period:
        return tuple(result)
    current = math.fsum(values[:period]) / period
    result[period - 1] = current
    alpha = 2.0 / (period + 1.0)
    for index in range(period, len(values)):
        current += alpha * (values[index] - current)
        result[index] = current
    return tuple(result)


def _features(bars: Sequence[ClosedBar], *, ha_mode: str = "closed") -> dict[str, tuple[Any, ...]]:
    if ha_mode not in HA_MODES:
        raise ValueError(f"ha_mode must be one of {sorted(HA_MODES)}")
    closes = [float(bar.close) for bar in bars]
    highs = [float(bar.high) for bar in bars]
    lows = [float(bar.low) for bar in bars]
    true_ranges: list[float] = []
    prior: float | None = None
    for high, low, close in zip(highs, lows, closes, strict=True):
        true_ranges.append(
            max(high - low, abs(high - prior), abs(low - prior))
            if prior is not None
            else high - low
        )
        prior = close
    plus = [0.0]
    minus = [0.0]
    for previous_high, previous_low, high, low in zip(
        highs[:-1], lows[:-1], highs[1:], lows[1:], strict=True
    ):
        up, down = high - previous_high, previous_low - low
        plus.append(up if up > down and up > 0 else 0.0)
        minus.append(down if down > up and down > 0 else 0.0)
    smooth_tr, smooth_plus, smooth_minus = (
        _rma(true_ranges, ADX_PERIOD),
        _rma(plus, ADX_PERIOD),
        _rma(minus, ADX_PERIOD),
    )
    plus_di: list[float | None] = [None] * len(bars)
    minus_di: list[float | None] = [None] * len(bars)
    dx: list[float | None] = [None] * len(bars)
    for index, tr in enumerate(smooth_tr):
        if tr is None or smooth_plus[index] is None or smooth_minus[index] is None:
            continue
        if tr <= 0:
            plus_di[index] = minus_di[index] = dx[index] = 0.0
            continue
        p = 100.0 * float(smooth_plus[index] or 0.0) / tr
        m = 100.0 * float(smooth_minus[index] or 0.0) / tr
        plus_di[index], minus_di[index] = p, m
        dx[index] = 0.0 if p + m <= 0 else 100.0 * abs(p - m) / (p + m)
    anchor_keys: list[datetime | None] = []
    for bar in bars:
        local = bar.at.astimezone(NY)
        if local.hour == 17:
            anchor_keys.append(None)
            continue
        start = datetime(local.year, local.month, local.day, 18, tzinfo=NY)
        if local.hour < 18:
            start -= timedelta(days=1)
        anchor_keys.append(start.astimezone(UTC))
    vwap: list[float | None] = []
    current: datetime | None = None
    weighted = volume = equal_sum = 0.0
    equal_count = 0
    for bar, key in zip(bars, anchor_keys, strict=True):
        if key != current:
            current, weighted, volume, equal_sum, equal_count = key, 0.0, 0.0, 0.0, 0
        typical = (float(bar.high) + float(bar.low) + float(bar.close)) / 3.0
        volume_value = float(bar.volume)
        if volume_value > 0:
            weighted += typical * volume_value
            volume += volume_value
            vwap.append(weighted / volume)
        else:
            # The recorder does not carry volume.  Equal weighting is the
            # deterministic fallback used by the diagnostic port for zero volume.
            equal_sum += typical
            equal_count += 1
            vwap.append(equal_sum / equal_count)
    ha_open: list[Any] = [None] * len(bars)
    ha_close: list[Any] = [None] * len(bars)
    groups: dict[tuple[datetime, int], list[int]] = defaultdict(list)
    for index, bar in enumerate(bars):
        local = bar.at.astimezone(NY)
        if local.hour == 17:
            continue
        session_date = local.date() if local.hour >= 18 else local.date() - timedelta(days=1)
        origin = datetime(
            session_date.year, session_date.month, session_date.day, 18, tzinfo=NY
        ).astimezone(UTC)
        elapsed = max(0, int((bar.at - origin).total_seconds() // 60))
        groups[(origin, elapsed // 54)].append(index)
    previous: tuple[float, float] | None = None
    for indices in groups.values():
        complete = len(indices) == GROUP_BARS and all(
            bars[a].at + timedelta(minutes=3) == bars[b].at
            for a, b in zip(indices[:-1], indices[1:], strict=True)
        )
        if ha_mode == "closed":
            # request.security(..., lookahead_off) exposes the completed prior
            # 54m value throughout the current group. This is the deterministic
            # backtest mode and preserves the pre-existing port behavior.
            if previous is not None:
                for index in indices:
                    ha_open[index], ha_close[index] = previous
            if not complete:
                continue
            raw_open, raw_close = float(bars[indices[0]].open), float(bars[indices[-1]].close)
            final_close = (
                raw_open
                + max(float(bars[i].high) for i in indices)
                + min(float(bars[i].low) for i in indices)
                + raw_close
            ) / 4.0
            open_price = (
                (raw_open + raw_close) / 2.0
                if previous is None
                else (previous[0] + previous[1]) / 2.0
            )
            previous = open_price, final_close
            continue

        # In realtime Pine request.security returns the developing higher-timeframe
        # value. Build the same prefix for each closed 3m bar so a live signal can
        # occur before the 54m group closes. Only a complete group advances the
        # confirmed state used as the next group's Heikin-Ashi open.
        for position, index in enumerate(indices):
            prefix = indices[: position + 1]
            raw_open, raw_close = float(bars[prefix[0]].open), float(bars[prefix[-1]].close)
            ha_open[index] = (
                (raw_open + raw_close) / 2.0
                if previous is None
                else (previous[0] + previous[1]) / 2.0
            )
            ha_close[index] = (
                raw_open
                + max(float(bars[i].high) for i in prefix)
                + min(float(bars[i].low) for i in prefix)
                + raw_close
            ) / 4.0
        if complete:
            raw_open, raw_close = float(bars[indices[0]].open), float(bars[indices[-1]].close)
            previous = (
                (raw_open + raw_close) / 2.0
                if previous is None
                else (previous[0] + previous[1]) / 2.0,
                (
                    raw_open
                    + max(float(bars[i].high) for i in indices)
                    + min(float(bars[i].low) for i in indices)
                    + raw_close
                )
                / 4.0,
            )
    buy = [False] * len(bars)
    sell = [False] * len(bars)
    for index in range(1, len(bars)):
        po, pc, co, cc = ha_open[index - 1], ha_close[index - 1], ha_open[index], ha_close[index]
        if None in (po, pc, co, cc):
            continue
        buy[index] = bool(pc <= po and cc > co)
        sell[index] = bool(pc >= po and cc < co)
    atr = _rma(true_ranges, ADX_PERIOD)
    return {
        "ema": _ema(closes, EMA_PERIOD),
        "atr": atr,
        "adx": _rma(dx, ADX_PERIOD),
        "plus_di": tuple(plus_di),
        "minus_di": tuple(minus_di),
        "vwap": tuple(vwap),
        "ha_open": tuple(ha_open),
        "ha_close": tuple(ha_close),
        "buy": tuple(buy),
        "sell": tuple(sell),
    }


class TaiSignalProvider:
    """Pure, session-anchored copy of the T-162 Tai signal gates."""

    def __init__(
        self,
        history: Iterable[ClosedBar] = (),
        *,
        minimum_history: int = 300,
        ha_mode: str = "closed",
    ) -> None:
        if minimum_history < EMA_PERIOD:
            raise ValueError("minimum_history must support EMA200 warmup")
        if ha_mode not in HA_MODES:
            raise ValueError(f"ha_mode must be one of {sorted(HA_MODES)}")
        self.minimum_history = minimum_history
        self._ha_mode = ha_mode
        # Keep the complete available series.  The closed T-162 port seeds EMA200
        # from the first 200 bars in its input and then carries that state forward.
        # Truncating this deque at 600 bars reseeded EMA200 (and the recursive
        # indicators) whenever a long-running session crossed the cutoff, which
        # made the runtime disagree with the port at otherwise identical bars.
        self._bars: deque[ClosedBar] = deque(history)
        self._last_exit_at: datetime | None = None
        self._last_failure_gate: str | None = None

    @property
    def last_failure_gate(self) -> str | None:
        """Return the most recent gate that prevented a signal."""
        return self._last_failure_gate

    @property
    def last_exit_at(self) -> datetime | None:
        """Return the persisted exit anchor used by the refresh trigger."""
        return self._last_exit_at

    @property
    def ha_mode(self) -> str:
        """Return the higher-timeframe Heikin-Ashi evaluation mode."""
        return self._ha_mode

    def note_exit(self, at: datetime) -> None:
        """Anchor the refresh countdown after a position closes."""
        self._last_exit_at = _minute_stamp(at)

    def restore_exit(self, at: datetime | None) -> None:
        """Restore the refresh anchor from the T-191 position state."""
        self._last_exit_at = _minute_stamp(at) if at is not None else None

    @classmethod
    def from_lake(
        cls,
        lake_root: str | Path,
        epic: str,
        *,
        minimum_history: int = 300,
        ha_mode: str = "closed",
    ) -> TaiSignalProvider:
        """Load recent 1m bid/ask mid bars and aggregate them for warm-up."""
        try:
            import pyarrow.parquet as pq  # type: ignore[import-untyped]
        except ImportError as exc:  # pragma: no cover - supplied by the runtime image
            raise RuntimeError("pyarrow is required to warm the Tai signal provider") from exc
        by_stamp: dict[datetime, dict[str, Mapping[str, Any]]] = defaultdict(dict)
        root = Path(lake_root) / epic / "1m_capital"
        paths = tuple(root.glob("**/*.parquet"))
        # The recorder stores by configured symbol (for example NAS100),
        # while this provider is keyed by the broker epic (for example NSXUSD).
        # Search the complete lake when those names differ so warm-up cannot
        # silently start empty.
        if not paths:
            paths = tuple(Path(lake_root).glob("*/1m_capital/*/*.parquet"))
        for path in paths:
            try:
                rows = pq.read_table(
                    path,
                    columns=["epic", "ts_utc", "side", "open", "high", "low", "close"],
                ).to_pylist()
            except (OSError, ValueError):
                continue
            for row in rows:
                stamp = row.get("ts_utc")
                side = row.get("side")
                recorded_epic = row.get("epic")
                if (
                    isinstance(stamp, datetime)
                    and side in {"bid", "ask"}
                    and (not recorded_epic or recorded_epic == epic)
                ):
                    by_stamp[stamp.astimezone(UTC).replace(second=0, microsecond=0)][side] = row
        minutes = []
        for stamp, sides in sorted(by_stamp.items()):
            bid, ask = sides.get("bid"), sides.get("ask")
            if bid is None or ask is None:
                continue
            minutes.append(
                TaiMinuteBar(
                    stamp,
                    epic,
                    Decimal(str((bid["open"] + ask["open"]) / 2)),
                    Decimal(str((bid["high"] + ask["high"]) / 2)),
                    Decimal(str((bid["low"] + ask["low"]) / 2)),
                    Decimal(str((bid["close"] + ask["close"]) / 2)),
                )
            )
        builder = ThreeMinuteBarBuilder(epic)
        bars = builder.extend(minutes)
        # Retain the full lake series so a restart resumes the same EMA/HA/ADX
        # state as the closed port.  ``minimum_history`` is a readiness gate, not
        # a truncation boundary; the feed tailer starts after the final bar below.
        return cls(bars, minimum_history=minimum_history, ha_mode=ha_mode)

    @property
    def history(self) -> tuple[ClosedBar, ...]:
        return tuple(self._bars)

    @property
    def warmup_ready(self) -> bool:
        return len(self._bars) >= self.minimum_history

    @property
    def warmup_bars(self) -> int:
        return len(self._bars)

    def __call__(self, bar: ClosedBar) -> TaiSignal | None:
        self._last_failure_gate = None
        self._bars.append(bar)
        if len(self._bars) < self.minimum_history:
            self._last_failure_gate = "ema200"
            return None
        bars = tuple(self._bars)
        index = len(bars) - 1
        features = _features(bars, ha_mode=self._ha_mode)
        atr = features["atr"][index]
        ema = features["ema"][index]
        adx = features["adx"][index]
        plus = features["plus_di"][index]
        minus = features["minus_di"][index]
        vwap = features["vwap"][index]
        if ema is None:
            self._last_failure_gate = "ema200"
            return None
        if adx is None or plus is None or minus is None:
            self._last_failure_gate = "adx"
            return None
        if atr is None or vwap is None or atr <= 0:
            self._last_failure_gate = "vwap_distance"
            return None

        def smart_ok(candidate: Direction) -> bool:
            if candidate is Direction.BUY:
                return (
                    bar.close > Decimal(str(ema))
                    and float(adx) >= 20
                    and float(plus) > float(minus)
                    and bar.close > Decimal(str(vwap))
                )
            return (
                bar.close < Decimal(str(ema))
                and float(adx) >= 20
                and float(minus) > float(plus)
                and bar.close < Decimal(str(vwap))
            )

        direction: Direction | None = None
        if features["buy"][index] and smart_ok(Direction.BUY):
            direction = Direction.BUY
        elif features["sell"][index] and smart_ok(Direction.SELL):
            direction = Direction.SELL
        if direction is None and self._last_exit_at is not None:
            exit_index = max(
                (i for i, item in enumerate(bars) if item.at <= self._last_exit_at),
                default=-1,
            )
            if exit_index >= 0 and index - exit_index >= REFRESH_BARS:
                for candidate in (Direction.BUY, Direction.SELL):
                    if smart_ok(candidate):
                        direction = candidate
                        break
        if direction is None:
            if features["buy"][index] or features["sell"][index]:
                if (features["buy"][index] and bar.close <= Decimal(str(ema))) or (
                    features["sell"][index] and bar.close >= Decimal(str(ema))
                ):
                    self._last_failure_gate = "ema200"
                elif (
                    float(adx) < 20
                    or (features["buy"][index] and float(plus) <= float(minus))
                    or (features["sell"][index] and float(minus) <= float(plus))
                ):
                    self._last_failure_gate = "adx"
                else:
                    self._last_failure_gate = "vwap_distance"
            else:
                self._last_failure_gate = "ha_cross"
            return None
        if abs(float(bar.close) - float(vwap)) / float(atr) > VWAP_DISTANCE_ATR:
            self._last_failure_gate = "vwap_distance"
            return None
        if index < SWING_LOOKBACK:
            self._last_failure_gate = "swing"
            return None
        previous = bars[index - SWING_LOOKBACK : index]
        stop = (
            min(item.low for item in previous) - Decimal(str(float(atr) * 0.20))
            if direction is Direction.BUY
            else max(item.high for item in previous) + Decimal(str(float(atr) * 0.20))
        )
        risk = bar.close - stop if direction is Direction.BUY else stop - bar.close
        if risk <= 0 or float(risk) / float(atr) > MAX_RISK_ATR:
            self._last_failure_gate = "risk"
            return None
        return TaiSignal(direction, bar.close, stop)


def _raw_rule(raw: Mapping[str, Any], *names: str) -> tuple[Decimal, str]:
    for name in names:
        value = raw.get(name)
        if isinstance(value, Mapping):
            number = value.get("value")
            unit = value.get("unit")
            if number is not None and isinstance(unit, str) and unit.strip():
                return _as_decimal(number, name), unit.strip().upper()
    raise InstrumentDataError(f"broker response is missing {names[0]}")


def parse_tai_instrument_rules(payload: Mapping[str, Any]) -> TaiInstrumentRules:
    """Parse Capital.com market details without inventing broker terms.

    Capital.com does not return ``tickSize`` or ``valuePerPoint`` in every
    ``/markets/{epic}`` response.  The search response supplies the tick and
    ``instrument.lotSize`` is the contract value-per-point fallback for the
    index CFDs used by this experiment.  The API also calls the protective
    distance ``minStopOrProfitDistance`` and commonly expresses it as a
    percentage of the current quote.
    """
    instrument = payload.get("instrument")
    rules = payload.get("dealingRules")
    if not isinstance(instrument, Mapping) or not isinstance(rules, Mapping):
        raise InstrumentDataError("broker market details require instrument and dealingRules")
    snapshot = payload.get("snapshot")
    tick_raw = instrument.get("tickSize")
    if tick_raw is None and isinstance(snapshot, Mapping):
        decimal_places = snapshot.get("decimalPlacesFactor")
        if isinstance(decimal_places, int) and decimal_places >= 0:
            tick_raw = Decimal("1").scaleb(-decimal_places)
    tick = _as_decimal(tick_raw, "tickSize")
    minimum, min_unit = _raw_rule(rules, "minDealSize")
    step, step_unit = _raw_rule(rules, "minSizeIncrement")
    stop, stop_unit = _raw_rule(
        rules,
        "minStopOrLimitDistance",
        "minNormalStopOrLimitDistance",
        "minStopOrProfitDistance",
        "minStopDistance",
        "minDistance",
    )
    try:
        limit, limit_unit = _raw_rule(rules, "minLimitDistance")
    except InstrumentDataError:
        limit, limit_unit = stop, stop_unit
    if min_unit != step_unit:
        raise InstrumentDataError("broker size rule units do not match")
    value_raw = instrument.get("valuePerPoint")
    if value_raw is None:
        value_raw = instrument.get("lotSize")
    value = _as_decimal(value_raw, "valuePerPoint")
    # Capital distance rules are normally POINTS.  Preserve a non-point unit
    # in the metadata and convert only known tick/point and quote-percentage
    # representations into absolute price distances.
    reference = None
    if isinstance(snapshot, Mapping):
        raw_bid = snapshot.get("bid")
        raw_offer = snapshot.get("offer", snapshot.get("ask"))
        if raw_offer is not None:
            reference = _as_decimal(raw_offer, "offer")
        elif raw_bid is not None:
            reference = _as_decimal(raw_bid, "bid")

    def distance(value: Decimal, unit: str, field: str) -> Decimal:
        if unit in {"POINT", "POINTS"}:
            return value * tick
        if unit in {"PERCENT", "PERCENTAGE"}:
            if reference is None:
                raise InstrumentDataError(f"broker {field} percentage needs a quote")
            return reference * value / Decimal("100")
        return value

    stop_distance = distance(stop, stop_unit, "minimum stop distance")
    limit_distance = distance(limit, limit_unit, "minimum limit distance")
    bid = (
        _as_decimal(snapshot["bid"], "bid")
        if isinstance(snapshot, Mapping) and snapshot.get("bid") is not None
        else None
    )
    ask_raw = snapshot.get("offer", snapshot.get("ask")) if isinstance(snapshot, Mapping) else None
    ask = _as_decimal(ask_raw, "offer") if ask_raw is not None else None
    return TaiInstrumentRules(
        min_size=minimum,
        size_step=step,
        value_per_point=value,
        tick_size=tick,
        min_stop_distance=stop_distance,
        min_limit_distance=limit_distance,
        bid=bid,
        ask=ask,
    )


class CapitalInstrumentRulesProvider:
    """Read-only Capital.com rules provider; refreshes before every entry."""

    def __init__(self, client: Any, epic: str) -> None:
        if not epic.strip():
            raise ValueError("epic must not be empty")
        self._client = client
        self.epic = epic.strip()

    def __call__(self, epic: str | None = None) -> TaiInstrumentRules:
        target = (epic or self.epic).strip()
        details = self._client.market_details(target)
        if not isinstance(details, Mapping):
            raise TaiDemoRefused("broker instrument rules are unavailable")
        # ``tickSize`` is present in the exact market-search result even when
        # the details endpoint omits it.  Enrich a copy so the parser remains
        # pure and we do not substitute a hard-coded instrument default.
        instrument = details.get("instrument")
        if isinstance(instrument, Mapping) and instrument.get("tickSize") is None:
            search = self._client.search_markets(target)
            matches = [item for item in search if isinstance(item, Mapping) and item.get("epic") == target]
            if len(matches) == 1 and matches[0].get("tickSize") is not None:
                enriched = dict(details)
                enriched_instrument = dict(instrument)
                enriched_instrument["tickSize"] = matches[0]["tickSize"]
                enriched["instrument"] = enriched_instrument
                details = enriched
        return parse_tai_instrument_rules(details)


class CapitalBalanceProvider:
    """Read the account balance before each potential entry, without persisting PII."""

    def __init__(self, client: Any, account_id: str | None = None) -> None:
        self._client = client
        self._account_id = account_id

    def __call__(self) -> Decimal:
        payload = self._client.account_info()
        accounts = payload.get("accounts") if isinstance(payload, Mapping) else None
        if not isinstance(accounts, Sequence) or isinstance(accounts, str):
            raise TaiDemoRefused("broker account balance is unavailable")
        candidates = [
            item
            for item in accounts
            if isinstance(item, Mapping)
            and (self._account_id is None or item.get("accountId") == self._account_id)
        ]
        if len(candidates) != 1:
            raise TaiDemoRefused("broker account balance is ambiguous")
        balance = candidates[0].get("balance")
        if isinstance(balance, Mapping):
            balance = balance.get("balance")
        return _as_decimal(balance, "account balance")


def _round_price(value: Decimal, tick: Decimal, direction: Direction, *, stop: bool) -> Decimal:
    """Round both levels away/toward price using the broker-safe side rule.

    BUY stops and targets both floor to a tick; SELL stops and targets both
    ceil.  The resulting stop remains on the protective side and targets move
    toward the entry rather than widening the requested structure.
    """
    del stop  # The direction determines the safe rounding side for both levels.
    ratio = value / tick
    mode = ROUND_FLOOR if direction is Direction.BUY else ROUND_CEILING
    return ratio.to_integral_value(rounding=mode) * tick


class TaiOrderFactory:
    """Create price-level orders with strict tick and minimum-distance checks."""

    allows_price_rounding = True

    def __init__(self, epic: str, rules_provider: Callable[[str], TaiInstrumentRules]) -> None:
        self.epic = epic
        self._rules = rules_provider

    def __call__(self, signal: TaiSignal, cycle_id: str, size: Decimal) -> OrderRequest:
        rules = self._rules(self.epic)
        if (
            rules.tick_size is None
            or rules.min_stop_distance is None
            or rules.min_limit_distance is None
        ):
            raise TaiDemoRefused("broker tick and minimum distance rules are required")
        stop = _round_price(signal.stop, rules.tick_size, signal.direction, stop=True)
        targets = signal.levels()
        target = _round_price(targets[2], rules.tick_size, signal.direction, stop=False)
        stop_distance = abs(signal.entry - stop)
        target_distance = abs(target - signal.entry)
        if stop_distance < rules.min_stop_distance:
            raise TaiDemoRefused("structure stop is closer than the broker minimum stop distance")
        if target_distance < rules.min_limit_distance:
            raise TaiDemoRefused("target is closer than the broker minimum limit distance")
        return OrderRequest(cycle_id, self.epic, signal.direction, size, signal.entry, stop, target)


class TaiDemoRuntimeAdapter:
    """Connect lake-fed closed minutes to the runner and schedule source."""

    def __init__(
        self,
        runner: TaiDemoRunner,
        source: Any,
        builder: ThreeMinuteBarBuilder,
        *,
        journal: Journal | None = None,
        alert: Alert | None = None,
        pending_bars: Mapping[str, ClosedBar] | None = None,
    ) -> None:
        self.runner = runner
        self.source = source
        self.builder = builder
        self._journal = journal or (lambda _event: None)
        self._alert = alert or (lambda message: LOGGER.info("tai demo: %s", message))
        self._feed_stale = False
        self._bars = dict(pending_bars or {})

    @property
    def feed_stale(self) -> bool:
        return self._feed_stale

    def snapshot_state(self) -> dict[str, _ManagedPosition]:
        """Return runner state for an in-process runtime composition retry."""
        return self.runner.snapshot_state()

    def pending_bars(self) -> dict[str, ClosedBar]:
        """Return queued closed bars that have not reached the cycle runtime."""
        return dict(self._bars)

    def health_snapshot(self) -> dict[str, dict[str, object]]:
        """Return the runner's secret-free outcome counters for /health."""
        return self.runner.health_snapshot()

    def mark_feed_stale(self) -> None:
        """Fail closed and emit one bounded stale-feed alert per outage."""
        if self._feed_stale:
            return
        self._feed_stale = True
        self._journal(
            {
                "event": "tai_feed_stale",
                "reason": "no_new_closed_minute_for_5_minutes",
            }
        )
        self._alert("Tai feed stale: no new closed minute for 5 minutes.")

    def mark_feed_healthy(self) -> None:
        """Allow decisions again after the tailer observes a new minute."""
        if not self._feed_stale:
            return
        self._feed_stale = False
        self._journal({"event": "tai_feed_recovered"})

    def on_minute_bar(self, minute: TaiMinuteBar) -> ClosedBar | None:
        bar = self.builder.add(minute)
        if bar is None:
            return None
        event_id = f"bar:3M:{bar.epic}:{bar.at.isoformat()}"
        self._bars[event_id] = bar
        from trader.runtime.composition import CycleTrigger

        self.source.emit(CycleTrigger("bar_close", event_id, bar.at))
        return bar

    def run(self, cycle: object) -> DemoOutcome:
        event_id = str(getattr(cycle, "event_id", ""))
        bar = self._bars.pop(event_id, None)
        if bar is None:
            return DemoOutcome("NO_TRADE", "bar_unavailable", event_id)
        signal = getattr(self.runner, "_signal", None)
        warmup_bars = getattr(signal, "warmup_bars", 0)
        LOGGER.info(
            "tai demo heartbeat event=%s at=%s warmup_bars=%s",
            event_id,
            bar.at.isoformat(),
            warmup_bars,
        )
        if self._feed_stale:
            return DemoOutcome("NO_TRADE", "feed_stale", event_id)
        return self.runner.on_bar(bar)


def build_tai_demo_runtime(
    settings: object,
    *,
    epic: str,
    order_manager: OrderManager,
    position_gateway: PositionGateway,
    control: Control,
    source: Any,
    builder: ThreeMinuteBarBuilder,
    balance_provider: Callable[[], Decimal],
    rules_provider: Callable[[str], TaiInstrumentRules],
    signal_provider: Callable[[ClosedBar], TaiSignal | None] | None = None,
    journal: Callable[[Mapping[str, object]], None] | None = None,
    alert: Callable[[str], None] | None = None,
    managed_state: Mapping[str, _ManagedPosition] | None = None,
    pending_bars: Mapping[str, ClosedBar] | None = None,
    state_path: Path | None = None,
) -> TaiDemoRuntimeAdapter | None:
    """Compose the guarded T-186 runtime only when the explicit experiment is on."""
    demo_settings = TaiDemoSettings.from_settings(settings)
    if not demo_settings.enabled:
        return None
    signal = signal_provider or TaiSignalProvider()
    if isinstance(signal, TaiSignalProvider) and journal is not None:
        journal(
            {
                "event": "tai_demo_warmup",
                "epic": epic,
                "bars": signal.warmup_bars,
                "required": signal.minimum_history,
                "ready": signal.warmup_ready,
                "ha_mode": signal.ha_mode,
            }
        )
    runner = TaiDemoRunner(
        settings=demo_settings,
        order_manager=order_manager,
        position_gateway=position_gateway,
        signal_provider=signal,
        order_factory=TaiOrderFactory(epic, rules_provider),
        control=control,
        balance_provider=balance_provider,
        instrument_rules_provider=rules_provider,
        dry_run=demo_settings.dry_run,
        journal=journal,
        alert=alert,
        managed_state=managed_state,
        state_path=state_path,
        epic=epic,
    )
    return TaiDemoRuntimeAdapter(
        runner,
        source,
        builder,
        journal=journal,
        alert=alert,
        pending_bars=pending_bars,
    )


__all__ = [
    "CapitalBalanceProvider",
    "CapitalInstrumentRulesProvider",
    "build_tai_demo_runtime",
    "TaiDemoRuntimeAdapter",
    "TaiMinuteBar",
    "TaiOrderFactory",
    "TaiSignalProvider",
    "ThreeMinuteBarBuilder",
    "REFRESH_BARS",
    "HA_MODES",
    "minute_bar_from_candle",
    "parse_tai_instrument_rules",
]
