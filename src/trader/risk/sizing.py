"""Fail-closed broker-aware position sizing using Decimal arithmetic."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN

from trader.data.instruments import InstrumentTerms
from trader.risk.guardian import TradeIntent


@dataclass(frozen=True, slots=True)
class SizingLimits:
    """Risk limits required to size one proposed position."""

    risk_per_trade_pct: Decimal
    max_margin_usage_pct: Decimal
    max_effective_leverage: Decimal


@dataclass(frozen=True, slots=True)
class SizingDecision:
    """Sizing outcome; rejected proposals never contain an executable size."""

    approved: bool
    size: Decimal | None
    risk_budget: Decimal | None
    estimated_risk: Decimal | None
    margin_required: Decimal | None
    projected_margin_usage_pct: Decimal | None
    projected_effective_leverage: Decimal | None
    reason: str | None = None


def _positive(value: Decimal) -> bool:
    return isinstance(value, Decimal) and value.is_finite() and value > 0


def _rejected(reason: str, risk_budget: Decimal | None = None) -> SizingDecision:
    return SizingDecision(
        approved=False,
        size=None,
        risk_budget=risk_budget,
        estimated_risk=None,
        margin_required=None,
        projected_margin_usage_pct=None,
        projected_effective_leverage=None,
        reason=reason,
    )


def size_position(
    intent: TradeIntent,
    terms: InstrumentTerms,
    *,
    account_equity: Decimal,
    account_currency: str,
    current_margin_used: Decimal,
    current_notional_exposure: Decimal,
    limits: SizingLimits,
    quote_to_account_rate: Decimal | None = None,
) -> SizingDecision:
    """Return a safe size from current broker terms and account limits.

    terms.value_per_point must be the quote-currency value of one price point
    for one broker size unit. It is never inferred from lot size. If quote and
    account currencies differ, the caller must supply a validated current
    quote-to-account rate. This function performs no network IO.
    """
    if not _positive(account_equity):
        return _rejected("account equity must be positive")
    if not _positive(limits.risk_per_trade_pct) or limits.risk_per_trade_pct > 100:
        return _rejected("risk_per_trade_pct must be in (0, 100]")
    if (
        not _positive(limits.max_margin_usage_pct)
        or limits.max_margin_usage_pct > 100
    ):
        return _rejected("max_margin_usage_pct must be in (0, 100]")
    if not _positive(limits.max_effective_leverage):
        return _rejected("max_effective_leverage must be positive")

    risk_budget = account_equity * limits.risk_per_trade_pct / Decimal("100")

    if intent.stop_loss is None:
        return _rejected("a stop-loss is required", risk_budget)
    if not _positive(intent.entry_price) or not _positive(intent.stop_loss):
        return _rejected("entry and stop prices must be positive", risk_budget)
    if intent.direction == "buy" and intent.stop_loss >= intent.entry_price:
        return _rejected("buy stop-loss must be below entry", risk_budget)
    if intent.direction == "sell" and intent.stop_loss <= intent.entry_price:
        return _rejected("sell stop-loss must be above entry", risk_budget)
    if intent.direction not in {"buy", "sell"}:
        return _rejected("trade direction is invalid", risk_budget)

    stop_distance = abs(intent.entry_price - intent.stop_loss)
    if not _positive(stop_distance):
        return _rejected("stop distance must be positive", risk_budget)

    if terms.value_per_point is None or not _positive(terms.value_per_point):
        return _rejected("broker value_per_point is unavailable or invalid", risk_budget)
    if not _positive(terms.size_step) or not _positive(terms.min_size):
        return _rejected("broker minimum size and size step must be positive", risk_budget)
    if not _positive(terms.max_size) or terms.min_size > terms.max_size:
        return _rejected("broker maximum size is invalid", risk_budget)
    if not _positive(terms.contract_size):
        return _rejected("broker contract_size is invalid", risk_budget)
    if not _positive(terms.margin_factor):
        return _rejected("broker margin_factor is invalid", risk_budget)
    if (
        not isinstance(terms.margin_factor_unit, str)
        or terms.margin_factor_unit.upper() != "PERCENTAGE"
    ):
        return _rejected("unsupported broker margin factor unit", risk_budget)
    if not _positive(current_margin_used) and current_margin_used != 0:
        return _rejected("current margin used must be non-negative", risk_budget)
    if not _positive(current_notional_exposure) and current_notional_exposure != 0:
        return _rejected("current notional exposure must be non-negative", risk_budget)

    quote_currency = terms.currency
    normalized_account_currency = (
        account_currency.strip().upper() if isinstance(account_currency, str) else ""
    )
    if (
        not isinstance(quote_currency, str)
        or not quote_currency.strip()
        or not normalized_account_currency
    ):
        return _rejected("quote and account currencies are required", risk_budget)
    if quote_currency.strip().upper() == normalized_account_currency:
        conversion_rate = Decimal("1")
    elif quote_to_account_rate is not None and _positive(quote_to_account_rate):
        conversion_rate = quote_to_account_rate
    else:
        return _rejected(
            "a positive quote-to-account conversion rate is required", risk_budget
        )

    risk_per_size_unit = stop_distance * terms.value_per_point * conversion_rate
    if not _positive(risk_per_size_unit):
        return _rejected("risk per size unit must be positive", risk_budget)

    raw_size = risk_budget / risk_per_size_unit
    size = (raw_size / terms.size_step).to_integral_value(rounding=ROUND_DOWN) * terms.size_step
    max_step_aligned = (
        terms.max_size / terms.size_step
    ).to_integral_value(rounding=ROUND_DOWN) * terms.size_step
    size = min(size, max_step_aligned)
    if size < terms.min_size:
        return _rejected("minimum broker size exceeds the risk budget", risk_budget)

    estimated_risk = size * risk_per_size_unit
    notional = intent.entry_price * terms.contract_size * size * conversion_rate
    margin_required = notional * terms.margin_factor / Decimal("100")
    projected_margin = current_margin_used + margin_required
    margin_usage_pct = projected_margin * Decimal("100") / account_equity
    projected_notional = current_notional_exposure + notional
    effective_leverage = projected_notional / account_equity

    if margin_usage_pct > limits.max_margin_usage_pct:
        return SizingDecision(
            False,
            None,
            risk_budget,
            estimated_risk,
            margin_required,
            margin_usage_pct,
            effective_leverage,
            "projected margin usage exceeds the configured limit",
        )
    if effective_leverage > limits.max_effective_leverage:
        return SizingDecision(
            False,
            None,
            risk_budget,
            estimated_risk,
            margin_required,
            margin_usage_pct,
            effective_leverage,
            "projected effective leverage exceeds the configured limit",
        )

    return SizingDecision(
        approved=True,
        size=size,
        risk_budget=risk_budget,
        estimated_risk=estimated_risk,
        margin_required=margin_required,
        projected_margin_usage_pct=margin_usage_pct,
        projected_effective_leverage=effective_leverage,
    )
