"""Config-driven instrument aliases and cached broker trading terms."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol


class InstrumentDataError(ValueError):
    """Raised when instrument configuration or broker metadata is invalid."""


class UnknownInstrumentError(InstrumentDataError):
    """Raised when an alias is not configured."""


class MarketDetailsClient(Protocol):
    """Minimal read-only subset needed from the Capital.com client."""

    def search_markets(self, search_term: str) -> list[Mapping[str, Any]]: ...

    def market_details(self, epic: str) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class InstrumentDefinition:
    """Static, non-sensitive instrument identity from instruments.yaml."""

    symbol: str
    display_name: str
    aliases: tuple[str, ...]
    capital_epic: str
    histdata_code: str
    asset_class: str
    value_per_point: Decimal | None


@dataclass(frozen=True, slots=True)
class InstrumentTerms:
    """Validated trading terms from Capital.com, plus explicit config lineage."""

    symbol: str
    capital_epic: str
    tick_size: Decimal
    contract_size: Decimal
    min_size: Decimal
    max_size: Decimal
    size_step: Decimal
    size_unit: str
    margin_factor: Decimal
    margin_factor_unit: str
    value_per_point: Decimal | None
    currency: str | None
    market_status: str | None
    updated_at: datetime


def _positive_decimal(value: object, field: str, *, optional: bool = False) -> Decimal | None:
    if value is None and optional:
        return None
    if isinstance(value, bool):
        raise InstrumentDataError(f"broker field {field} must be a positive number")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise InstrumentDataError(f"broker field {field} must be a positive number") from exc
    if not result.is_finite() or result <= 0:
        raise InstrumentDataError(f"broker field {field} must be a positive number")
    return result


def _required_mapping(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise InstrumentDataError(f"broker response is missing {field}")
    return value


def _rule_value(rules: Mapping[str, Any], name: str) -> tuple[Decimal, str]:
    rule = _required_mapping(rules.get(name), f"dealingRules.{name}")
    value = _positive_decimal(rule.get("value"), f"dealingRules.{name}.value")
    unit = rule.get("unit")
    if not isinstance(unit, str) or not unit.strip():
        raise InstrumentDataError(f"broker field dealingRules.{name}.unit is invalid")
    assert value is not None
    return value, unit.strip().upper()


def _config_decimal(value: object, field: str) -> Decimal | None:
    if value is None:
        return None
    return _positive_decimal(value, field)


class InstrumentMapper:
    """Resolve configured aliases and lazily refresh cached broker terms.

    Call refresh_all during core startup when Capital.com credentials are
    available. get() refreshes stale entries on demand using the same TTL.
    """

    def __init__(
        self,
        config: Mapping[str, Any],
        client: MarketDetailsClient,
        *,
        refresh_interval_seconds: float = 3600,
        monotonic: Callable[[], float] = time.monotonic,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if not math.isfinite(refresh_interval_seconds) or refresh_interval_seconds <= 0:
            raise ValueError("refresh_interval_seconds must be positive")
        raw_instruments = config.get("instruments")
        if not isinstance(raw_instruments, Mapping) or not raw_instruments:
            raise InstrumentDataError("instrument config must contain an instruments mapping")
        self._client = client
        self._refresh_interval_seconds = refresh_interval_seconds
        self._monotonic = monotonic
        self._clock = clock
        self._lock = threading.RLock()
        self._definitions: dict[str, InstrumentDefinition] = {}
        self._aliases: dict[str, str] = {}
        self._cache: dict[str, tuple[InstrumentTerms, float]] = {}
        for symbol_value, raw in raw_instruments.items():
            if not isinstance(symbol_value, str) or not symbol_value.strip():
                raise InstrumentDataError("instrument symbol must be a non-empty string")
            symbol = symbol_value.strip().upper()
            if symbol in self._definitions:
                raise InstrumentDataError(f"duplicate instrument symbol {symbol}")
            if not isinstance(raw, Mapping):
                raise InstrumentDataError(f"instrument {symbol} must be a mapping")
            display_name = raw.get("display_name")
            epic = raw.get("capital_epic")
            histdata_code = raw.get("histdata_code")
            asset_class = raw.get("asset_class")
            aliases_raw = raw.get("aliases", [])
            if not all(
                isinstance(value, str) and value.strip()
                for value in (display_name, epic, histdata_code, asset_class)
            ):
                raise InstrumentDataError(f"instrument {symbol} has invalid identity fields")
            if not isinstance(aliases_raw, list | tuple) or any(
                not isinstance(value, str) or not value.strip() for value in aliases_raw
            ):
                raise InstrumentDataError(f"instrument {symbol} aliases must be non-empty strings")
            aliases = tuple(
                dict.fromkeys((symbol, *(value.strip().upper() for value in aliases_raw)))
            )
            definition = InstrumentDefinition(
                symbol=symbol,
                display_name=display_name.strip(),
                aliases=aliases,
                capital_epic=epic.strip(),
                histdata_code=histdata_code.strip().upper(),
                asset_class=asset_class.strip().lower(),
                value_per_point=_config_decimal(
                    raw.get("value_per_point"), f"{symbol}.value_per_point"
                ),
            )
            self._definitions[symbol] = definition
            for alias in aliases:
                owner = self._aliases.get(alias)
                if owner is not None and owner != symbol:
                    raise InstrumentDataError(
                        f"instrument alias {alias} is assigned more than once"
                    )
                self._aliases[alias] = symbol

    @property
    def definitions(self) -> tuple[InstrumentDefinition, ...]:
        """Return configured instrument definitions in YAML order."""
        return tuple(self._definitions.values())

    def resolve(self, alias: str) -> InstrumentDefinition:
        """Resolve a symbol or alias case-insensitively."""
        if not isinstance(alias, str) or not alias.strip():
            raise UnknownInstrumentError("instrument alias must be a non-empty string")
        normalized = alias.strip().upper()
        symbol = self._aliases.get(normalized)
        if symbol is None:
            raise UnknownInstrumentError(f"unknown instrument alias: {normalized}")
        return self._definitions[symbol]

    def get(self, alias: str) -> InstrumentTerms:
        """Return cached broker terms, refreshing the entry when its TTL expires."""
        definition = self.resolve(alias)
        now = self._monotonic()
        with self._lock:
            cached = self._cache.get(definition.symbol)
            if cached is not None and now - cached[1] < self._refresh_interval_seconds:
                return cached[0]
            terms = self._fetch_terms(definition)
            self._cache[definition.symbol] = (terms, now)
            return terms

    def refresh_all(self) -> dict[str, InstrumentTerms]:
        """Fetch every configured market before atomically replacing the cache."""
        now = self._monotonic()
        with self._lock:
            refreshed = {
                definition.symbol: self._fetch_terms(definition)
                for definition in self._definitions.values()
            }
            self._cache.update({symbol: (terms, now) for symbol, terms in refreshed.items()})
            return refreshed

    def _fetch_terms(self, definition: InstrumentDefinition) -> InstrumentTerms:
        matches = [
            market
            for market in self._client.search_markets(definition.capital_epic)
            if isinstance(market, Mapping) and market.get("epic") == definition.capital_epic
        ]
        if len(matches) != 1:
            raise InstrumentDataError(
                "broker market search returned "
                f"{len(matches)} exact matches for {definition.capital_epic}"
            )
        search_market = matches[0]
        details = self._client.market_details(definition.capital_epic)
        instrument = _required_mapping(details.get("instrument"), "instrument")
        if instrument.get("epic") != definition.capital_epic:
            raise InstrumentDataError(
                f"broker market details epic does not match {definition.capital_epic}"
            )
        dealing_rules = _required_mapping(details.get("dealingRules"), "dealingRules")
        tick_raw = instrument.get("tickSize", search_market.get("tickSize"))
        tick_size = _positive_decimal(tick_raw, "tickSize")
        contract_size = _positive_decimal(instrument.get("lotSize"), "instrument.lotSize")
        min_size, min_unit = _rule_value(dealing_rules, "minDealSize")
        max_size, max_unit = _rule_value(dealing_rules, "maxDealSize")
        size_step, step_unit = _rule_value(dealing_rules, "minSizeIncrement")
        if min_unit != max_unit or min_unit != step_unit:
            raise InstrumentDataError("broker size rule units do not match")
        if min_size > max_size or size_step > max_size:
            raise InstrumentDataError("broker size rules are inconsistent")
        margin_factor = _positive_decimal(instrument.get("marginFactor"), "instrument.marginFactor")
        margin_unit = instrument.get("marginFactorUnit")
        if not isinstance(margin_unit, str) or not margin_unit.strip():
            raise InstrumentDataError("broker field instrument.marginFactorUnit is invalid")
        currency = instrument.get("currency")
        if currency is not None and (not isinstance(currency, str) or len(currency) > 8):
            raise InstrumentDataError("broker field instrument.currency is invalid")
        snapshot = details.get("snapshot")
        status: str | None = None
        if isinstance(snapshot, Mapping):
            raw_status = snapshot.get("marketStatus")
            if isinstance(raw_status, str) and raw_status.strip():
                status = raw_status.strip().upper()
        assert tick_size is not None and contract_size is not None and margin_factor is not None
        return InstrumentTerms(
            symbol=definition.symbol,
            capital_epic=definition.capital_epic,
            tick_size=tick_size,
            contract_size=contract_size,
            min_size=min_size,
            max_size=max_size,
            size_step=size_step,
            size_unit=min_unit,
            margin_factor=margin_factor,
            margin_factor_unit=margin_unit.strip().upper(),
            value_per_point=definition.value_per_point,
            currency=currency,
            market_status=status,
            updated_at=self._clock().astimezone(UTC),
        )
