"""Fail-closed production inputs for the runtime Guardian.

The graph only supplies a proposal.  This module reads account, recorder,
position and instrument data and converts it to the typed Guardian inputs.  A
missing or stale value returns ``None`` so the approval boundary rejects the
proposal without inventing a market price, account baseline or size.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from trader.agents.graph import TradeIntent as GraphTradeIntent
from trader.data.instruments import InstrumentTerms
from trader.risk.guardian import (
    AccountState,
    MarketSnapshot,
    OpenPosition,
    SizingEstimate,
    TradeIntent,
)
from trader.risk.sizing import SizingDecision, SizingLimits, size_position
from trader.runtime.approval import GuardianInputs


class ContextUnavailable(RuntimeError):
    """A required deterministic input is missing or stale."""


def _decimal(value: object, field: str, *, positive: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        raise ContextUnavailable(f"{field} is unavailable") from None
    if not result.is_finite() or (positive and result <= 0):
        raise ContextUnavailable(f"{field} is unavailable")
    return result


def _aware(value: object, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ContextUnavailable(f"{field} timestamp is unavailable")
    return value.astimezone(UTC)


class AccountContextSource:
    """Normalize broker account data and durable journal baselines."""

    def __init__(self, broker: object, journal: object, *, clock: Callable[[], datetime]) -> None:
        self.broker = broker
        self.journal = journal
        self.clock = clock

    def __call__(self) -> AccountState:
        raw = self.broker.get_account()
        if not isinstance(raw, Mapping):
            raise ContextUnavailable("broker account is unavailable")

        equity = _decimal(raw.get("equity", raw.get("balance")), "equity", positive=True)
        snapshot = self._snapshot()

        def field(name: str, *, positive: bool = False) -> Decimal:
            value = raw.get(name, snapshot.get(name))
            return _decimal(value, name, positive=positive)

        day_start = field("day_start_equity", positive=True)
        week_start = field("week_start_equity", positive=True)
        peak = field("peak_equity", positive=True)
        daily = _decimal(raw.get("daily_pnl", equity - day_start), "daily_pnl")
        weekly = _decimal(raw.get("weekly_pnl", equity - week_start), "weekly_pnl")
        margin = field("margin_used")
        exposure = field("notional_exposure")
        orders = raw.get("orders_last_hour")
        if orders is None:
            orders = self._orders_last_hour()
        if not isinstance(orders, int) or isinstance(orders, bool) or orders < 0:
            raise ContextUnavailable("orders_last_hour is unavailable")
        consecutive = raw.get("consecutive_losses", snapshot.get("consecutive_losses"))
        if not isinstance(consecutive, int) or isinstance(consecutive, bool) or consecutive < 0:
            raise ContextUnavailable("consecutive_losses is unavailable")
        last_loss = raw.get("last_loss_at", snapshot.get("last_loss_at"))
        if last_loss is not None:
            if isinstance(last_loss, str):
                try:
                    last_loss = datetime.fromisoformat(last_loss)
                except ValueError:
                    raise ContextUnavailable("last_loss_at is unavailable") from None
            last_loss = _aware(last_loss, "last_loss_at")
        state = AccountState(
            equity=equity,
            day_start_equity=day_start,
            week_start_equity=week_start,
            peak_equity=peak,
            daily_pnl=daily,
            weekly_pnl=weekly,
            margin_used=margin,
            notional_exposure=exposure,
            orders_last_hour=orders,
            consecutive_losses=consecutive,
            last_loss_at=last_loss,
            paused=bool(raw.get("paused", False)),
            kill_switch_active=bool(raw.get("kill_switch_active", False)),
            drawdown_halt_active=bool(raw.get("drawdown_halt_active", False)),
        )
        saver = getattr(self.journal, "save_snapshot", None)
        if callable(saver):
            now = self.clock().astimezone(UTC)
            iso = now.date().isocalendar()
            saver(
                "runtime:account-baseline",
                {
                    "day": now.date().isoformat(),
                    "week": f"{iso.year}-W{iso.week:02d}",
                    "day_start_equity": str(day_start),
                    "week_start_equity": str(week_start),
                    "peak_equity": str(peak),
                    "consecutive_losses": consecutive,
                    "last_loss_at": last_loss.isoformat() if last_loss is not None else None,
                },
            )
        return state

    def _snapshot(self) -> Mapping[str, object]:
        getter = getattr(self.journal, "get_snapshot", None)
        if not callable(getter):
            return {}
        value = getter("runtime:account-baseline")
        if not isinstance(value, Mapping):
            return {}
        now = self.clock().astimezone(UTC)
        iso = now.date().isocalendar()
        current_day = now.date().isoformat()
        current_week = f"{iso.year}-W{iso.week:02d}"
        if value.get("day") != current_day or value.get("week") != current_week:
            return {}
        return value

    def _orders_last_hour(self) -> int:
        records = getattr(self.journal, "records", None)
        if not callable(records):
            raise ContextUnavailable("orders_last_hour is unavailable")
        start = self.clock().astimezone(UTC) - timedelta(hours=1)
        count = 0
        for row in records():
            if not isinstance(row, Mapping):
                continue
            stamp = row.get("submitted_at", row.get("reserved_at"))
            if stamp is None:
                continue
            try:
                when = _aware(
                    datetime.fromisoformat(stamp) if isinstance(stamp, str) else stamp,
                    "trade timestamp",
                )
            except ContextUnavailable:
                continue
            if when >= start and row.get("execution_state") in {"submitted", "reserved"}:
                count += 1
        return count


class MarketContextSource:
    """Build a fresh market snapshot from the recorder or an injected feed."""

    def __init__(
        self,
        recorder: object,
        *,
        clock: Callable[[], datetime],
        stale_after_seconds: float = 180.0,
        market_open: Callable[[str, datetime], bool | None] | None = None,
        in_break: Callable[[str, datetime], bool] | None = None,
    ) -> None:
        if stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be positive")
        self.recorder = recorder
        self.clock = clock
        self.stale_after = timedelta(seconds=stale_after_seconds)
        self.market_open = market_open
        self.in_break = in_break

    def __call__(self, instrument: str) -> MarketSnapshot:
        getter = getattr(self.recorder, "latest_snapshot", None)
        if not callable(getter):
            getter = getattr(self.recorder, "latest_bar", None)
        if not callable(getter):
            raise ContextUnavailable("recorder market snapshot is unavailable")
        value = getter(instrument)
        if not isinstance(value, Mapping):
            raise ContextUnavailable("recorder market snapshot is unavailable")
        stamp = _aware(
            value.get("latest_data_at", value.get("timestamp", value.get("ts_utc"))),
            "latest_data_at",
        )
        age = self.clock().astimezone(UTC) - stamp
        if age < timedelta(0) or age > self.stale_after:
            raise ContextUnavailable("market data is stale")
        bid = _decimal(value.get("bid"), "bid", positive=True)
        ask = _decimal(value.get("ask"), "ask", positive=True)
        atr = _decimal(value.get("atr"), "atr", positive=True)
        if ask < bid:
            raise ContextUnavailable("market quote is inverted")
        opened = value.get("market_open")
        if opened is None and self.market_open is not None:
            opened = self.market_open(instrument, stamp)
        if not isinstance(opened, bool):
            raise ContextUnavailable("market_open is unavailable")
        paused = value.get("in_break")
        if paused is None and self.in_break is not None:
            paused = self.in_break(instrument, stamp)
        if not isinstance(paused, bool):
            raise ContextUnavailable("in_break is unavailable")
        ceiling = value.get("spread_ceiling")
        return MarketSnapshot(
            latest_data_at=stamp,
            market_open=opened,
            in_break=paused,
            bid=bid,
            ask=ask,
            atr=atr,
            spread_ceiling=_decimal(ceiling, "spread_ceiling", positive=True)
            if ceiling is not None
            else None,
        )


class SizingContextSource:
    """Call the deterministic risk sizer with broker instrument terms."""

    def __init__(
        self,
        terms_provider: Callable[[str], InstrumentTerms],
        limits: SizingLimits,
        *,
        account_currency: Callable[[AccountState], str],
        quote_rate_provider: Callable[[InstrumentTerms], Decimal | None] | None = None,
        sizer: Callable[..., SizingDecision] = size_position,
    ) -> None:
        self.terms_provider = terms_provider
        self.limits = limits
        self.account_currency = account_currency
        self.quote_rate_provider = quote_rate_provider
        self.sizer = sizer

    def __call__(
        self, intent: GraphTradeIntent, account: AccountState, positions: tuple[OpenPosition, ...]
    ) -> SizingEstimate:
        del positions
        terms = self.terms_provider(intent.instrument)
        legacy = TradeIntent(
            instrument=intent.instrument,
            direction="buy" if intent.direction == "BUY" else "sell",
            entry_price=intent.entry_level,
            stop_loss=intent.stop_level,
            take_profit=intent.target_levels[0] if intent.target_levels else None,
        )
        decision = self.sizer(
            legacy,
            terms,
            account_equity=account.equity,
            account_currency=self.account_currency(account),
            current_margin_used=account.margin_used,
            current_notional_exposure=account.notional_exposure,
            limits=self.limits,
            quote_to_account_rate=self.quote_rate_provider(terms)
            if self.quote_rate_provider is not None
            else None,
        )
        if not decision.approved or any(
            value is None
            for value in (
                decision.size,
                decision.estimated_risk,
                decision.margin_required,
                decision.projected_effective_leverage,
            )
        ):
            raise ContextUnavailable(decision.reason or "position sizing rejected")
        return SizingEstimate(
            decision.size,
            decision.estimated_risk,
            decision.margin_required,
            decision.projected_effective_leverage * account.equity,
        )


class RuntimeContextProvider:
    """Combine production sources into one GuardianInputs callback."""

    def __init__(
        self,
        account: Callable[[], AccountState],
        market: Callable[[str], MarketSnapshot],
        sizing: Callable[
            [GraphTradeIntent, AccountState, tuple[OpenPosition, ...]], SizingEstimate
        ],
        positions: Callable[[], Sequence[OpenPosition]],
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.account = account
        self.market = market
        self.sizing = sizing
        self.positions = positions
        self.clock = clock

    def __call__(self, _cycle_id: str, intent: GraphTradeIntent) -> GuardianInputs | None:
        try:
            account = self.account()
            positions = tuple(self.positions())
            return GuardianInputs(
                market=self.market(intent.instrument),
                account=account,
                open_positions=positions,
                sizing=self.sizing(intent, account, positions),
                trade_quality_score=Decimal("1"),
                now=self.clock(),
            )
        except (
            ArithmeticError,
            ContextUnavailable,
            KeyError,
            RuntimeError,
            TypeError,
            ValueError,
        ):
            return None


def position_source(
    broker: object,
    *,
    valuation: Callable[[object], OpenPosition | None] | None = None,
) -> Callable[[], tuple[OpenPosition, ...]]:
    """Expose broker positions only when each has explicit risk valuations."""

    def read() -> tuple[OpenPosition, ...]:
        result: list[OpenPosition] = []
        for position in broker.get_positions():
            value = valuation(position) if valuation is not None else None
            if value is None:
                risk = getattr(position, "risk_amount", None)
                margin = getattr(position, "margin_required", None)
                exposure = getattr(position, "notional_exposure", None)
                if not all(isinstance(item, Decimal) for item in (risk, margin, exposure)):
                    raise ContextUnavailable("position valuation is unavailable")
                value = OpenPosition(position.epic, risk, margin, exposure)
            result.append(value)
        return tuple(result)

    return read


__all__ = [
    "AccountContextSource",
    "ContextUnavailable",
    "MarketContextSource",
    "RuntimeContextProvider",
    "SizingContextSource",
    "position_source",
]
