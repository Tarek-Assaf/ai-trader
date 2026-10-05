"""Deterministic account-currency P&L, equity, exposure and reconciliation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from types import MappingProxyType

from trader.execution.brokers.base import Direction

_ZERO = Decimal("0")
_HUNDRED = Decimal("100")


def _finite_decimal(value: object, name: str, *, allow_zero: bool = True) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{name} must be a finite Decimal")
    if value < 0 or (not allow_zero and value == 0):
        qualifier = "positive" if not allow_zero else "non-negative"
        raise ValueError(f"{name} must be {qualifier}")
    return value


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class PnlTrade:
    """One complete or open trade with all valuation inputs in account currency.

    value_per_point is the account-currency value of one price point for one
    broker size unit. Callers must obtain it from validated broker terms and
    apply any required quote-to-account currency conversion. This engine never
    infers contract size, FX conversion, fees, financing, exposure, or margin.
    """

    trade_id: str
    epic: str
    direction: Direction
    size: Decimal
    entry_price: Decimal
    stop_level: Decimal
    value_per_point: Decimal
    # These are mandatory even for closed trades (pass zero); for open trades
    # they must come from the normalized broker position, never a guessed value.
    notional_exposure: Decimal
    margin_used: Decimal
    exit_price: Decimal | None = None
    mark_price: Decimal | None = None
    fees: Decimal = _ZERO
    financing: Decimal = _ZERO

    def __post_init__(self) -> None:
        if not isinstance(self.trade_id, str) or not self.trade_id.strip():
            raise ValueError("trade_id must be non-empty")
        if not isinstance(self.epic, str) or not self.epic.strip():
            raise ValueError("epic must be non-empty")
        if not isinstance(self.direction, Direction):
            raise ValueError("direction must be BUY or SELL")
        _finite_decimal(self.size, "size", allow_zero=False)
        _finite_decimal(self.entry_price, "entry_price", allow_zero=False)
        _finite_decimal(self.stop_level, "stop_level", allow_zero=False)
        _finite_decimal(self.value_per_point, "value_per_point", allow_zero=False)
        _finite_decimal(self.fees, "fees")
        if not isinstance(self.financing, Decimal) or not self.financing.is_finite():
            raise ValueError("financing must be a finite Decimal cash flow")
        _finite_decimal(self.notional_exposure, "notional_exposure")
        _finite_decimal(self.margin_used, "margin_used")

        if self.direction is Direction.BUY and self.stop_level >= self.entry_price:
            raise ValueError("BUY stop_level must be below entry_price")
        if self.direction is Direction.SELL and self.stop_level <= self.entry_price:
            raise ValueError("SELL stop_level must be above entry_price")

        if self.exit_price is not None:
            _finite_decimal(self.exit_price, "exit_price", allow_zero=False)
            if self.mark_price is not None:
                raise ValueError("closed trades cannot have a mark_price")
            if self.notional_exposure != 0 or self.margin_used != 0:
                raise ValueError("closed trades cannot have open exposure or margin")
        else:
            if self.mark_price is None:
                raise ValueError("open trades require a current mark_price")
            _finite_decimal(self.mark_price, "mark_price", allow_zero=False)


@dataclass(frozen=True, slots=True)
class BrokerAccountFigures:
    """Normalized account figures from the broker, expressed in one currency."""

    balance: Decimal
    equity: Decimal
    currency: str
    margin_used: Decimal
    notional_exposure: Decimal

    def __post_init__(self) -> None:
        _finite_decimal(self.balance, "broker balance")
        _finite_decimal(self.equity, "broker equity", allow_zero=False)
        _finite_decimal(self.margin_used, "broker margin_used")
        _finite_decimal(self.notional_exposure, "broker notional_exposure")
        if not isinstance(self.currency, str) or not self.currency.strip():
            raise ValueError("broker currency must be non-empty")
        object.__setattr__(self, "currency", self.currency.strip().upper())


@dataclass(frozen=True, slots=True)
class EquityPoint:
    """A previously persisted account-currency equity observation."""

    timestamp: datetime
    equity: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", _aware(self.timestamp, "equity timestamp"))
        _finite_decimal(self.equity, "equity", allow_zero=False)


@dataclass(frozen=True, slots=True)
class TradeRMultiple:
    trade_id: str
    net_profit: Decimal
    initial_risk: Decimal
    r_multiple: Decimal


@dataclass(frozen=True, slots=True)
class PnlReconciliation:
    """Calculated-minus-broker deltas; consumers alert when alert_required is true."""

    balance_delta: Decimal
    equity_delta: Decimal
    margin_delta: Decimal
    exposure_delta: Decimal
    tolerance: Decimal

    @property
    def alert_required(self) -> bool:
        return any(
            abs(delta) > self.tolerance
            for delta in (
                self.balance_delta,
                self.equity_delta,
                self.margin_delta,
                self.exposure_delta,
            )
        )


@dataclass(frozen=True, slots=True)
class PnlReport:
    currency: str
    realized_gross: Decimal
    realized_net: Decimal
    unrealized_gross: Decimal
    unrealized_net: Decimal
    fees: Decimal
    financing: Decimal
    balance: Decimal
    equity: Decimal
    peak_equity: Decimal
    drawdown: Decimal
    drawdown_pct: Decimal
    exposure_by_epic: Mapping[str, Decimal]
    total_exposure: Decimal
    margin_used: Decimal
    margin_usage_pct: Decimal
    effective_leverage: Decimal
    r_multiples: tuple[TradeRMultiple, ...]
    equity_curve: tuple[EquityPoint, ...]
    reconciliation: PnlReconciliation


class PnlEngine:
    """Pure P&L calculator; callers provide persisted trades and normalized broker data."""

    @staticmethod
    def calculate(
        trades: Sequence[PnlTrade],
        *,
        starting_balance: Decimal,
        currency: str,
        broker: BrokerAccountFigures,
        as_of: datetime,
        equity_history: Sequence[EquityPoint] = (),
        reconciliation_tolerance: Decimal = Decimal("0.01"),
    ) -> PnlReport:
        """Calculate account figures and return explicit reconciliation deltas.

        The caller owns persistence and alert delivery. An out-of-tolerance
        reconciliation is surfaced as reconciliation.alert_required; this
        method does not hide the mismatch or perform network/database IO.
        """
        _finite_decimal(starting_balance, "starting_balance", allow_zero=False)
        _finite_decimal(reconciliation_tolerance, "reconciliation_tolerance")
        account_currency = currency.strip().upper() if isinstance(currency, str) else ""
        if not account_currency:
            raise ValueError("currency must be non-empty")
        if broker.currency != account_currency:
            raise ValueError("broker and P&L currencies must match")
        current_time = _aware(as_of, "as_of")

        if any(not isinstance(point, EquityPoint) for point in equity_history):
            raise ValueError("equity_history must contain EquityPoint values")
        previous_points = tuple(equity_history)
        if any(
            earlier.timestamp > later.timestamp
            for earlier, later in zip(previous_points, previous_points[1:], strict=True)
        ):
            raise ValueError("equity_history must be chronological")
        if previous_points and previous_points[-1].timestamp > current_time:
            raise ValueError("as_of cannot precede the latest equity observation")

        trade_ids = [trade.trade_id for trade in trades]
        if len(set(trade_ids)) != len(trade_ids):
            raise ValueError("trade_id values must be unique")

        realized_gross = _ZERO
        realized_net = _ZERO
        unrealized_gross = _ZERO
        unrealized_net = _ZERO
        total_fees = _ZERO
        total_financing = _ZERO
        exposure_by_epic: dict[str, Decimal] = {}
        margin_used = _ZERO
        r_multiples: list[TradeRMultiple] = []
        sign = {Direction.BUY: Decimal("1"), Direction.SELL: Decimal("-1")}

        for trade in trades:
            total_fees += trade.fees
            total_financing += trade.financing
            if trade.exit_price is not None:
                gross = (
                    (trade.exit_price - trade.entry_price)
                    * trade.size
                    * trade.value_per_point
                    * sign[trade.direction]
                )
                net = gross - trade.fees + trade.financing
                risk = (
                    abs(trade.entry_price - trade.stop_level) * trade.size * trade.value_per_point
                )
                realized_gross += gross
                realized_net += net
                r_multiples.append(
                    TradeRMultiple(
                        trade_id=trade.trade_id,
                        net_profit=net,
                        initial_risk=risk,
                        r_multiple=net / risk,
                    )
                )
            else:
                assert trade.mark_price is not None
                gross = (
                    (trade.mark_price - trade.entry_price)
                    * trade.size
                    * trade.value_per_point
                    * sign[trade.direction]
                )
                unrealized_gross += gross
                unrealized_net += gross - trade.fees + trade.financing
                exposure_by_epic[trade.epic] = (
                    exposure_by_epic.get(trade.epic, _ZERO) + trade.notional_exposure
                )
                margin_used += trade.margin_used

        balance = starting_balance + realized_net
        equity = balance + unrealized_net
        if equity <= 0:
            raise ValueError("calculated equity must be positive")

        exposure_by_epic = dict(sorted(exposure_by_epic.items()))
        total_exposure = sum(exposure_by_epic.values(), start=_ZERO)
        peak_equity = max((starting_balance, *(point.equity for point in previous_points), equity))
        drawdown = peak_equity - equity
        drawdown_pct = drawdown / peak_equity * _HUNDRED
        margin_usage_pct = margin_used / equity * _HUNDRED
        effective_leverage = total_exposure / equity

        reconciliation = PnlReconciliation(
            balance_delta=balance - broker.balance,
            equity_delta=equity - broker.equity,
            margin_delta=margin_used - broker.margin_used,
            exposure_delta=total_exposure - broker.notional_exposure,
            tolerance=reconciliation_tolerance,
        )
        current_point = EquityPoint(timestamp=current_time, equity=equity)
        return PnlReport(
            currency=account_currency,
            realized_gross=realized_gross,
            realized_net=realized_net,
            unrealized_gross=unrealized_gross,
            unrealized_net=unrealized_net,
            fees=total_fees,
            financing=total_financing,
            balance=balance,
            equity=equity,
            peak_equity=peak_equity,
            drawdown=drawdown,
            drawdown_pct=drawdown_pct,
            exposure_by_epic=MappingProxyType(exposure_by_epic),
            total_exposure=total_exposure,
            margin_used=margin_used,
            margin_usage_pct=margin_usage_pct,
            effective_leverage=effective_leverage,
            r_multiples=tuple(r_multiples),
            equity_curve=(*previous_points, current_point),
            reconciliation=reconciliation,
        )
