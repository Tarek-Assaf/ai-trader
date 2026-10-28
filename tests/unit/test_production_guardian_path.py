"""A Tai entry through the production wiring: real risk.yaml, real Guardian, real sources.

Earlier guarded-stack tests hand-built the Guardian inputs, so missing pieces of the
production context (loss streak, stage cap, point value) only showed up on live signals.
This test builds the inputs the way the runtime does and walks one realistic US100 entry
through all twenty Guardian rules.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml
from sqlalchemy import create_engine
from test_capital_runtime import FIELDS, FakeCapital

from trader.core.db import tables
from trader.data.instruments import InstrumentMapper
from trader.execution.brokers.base import Direction, OrderRequest
from trader.execution.brokers.capital_runtime import CapitalRuntimeBroker
from trader.execution.journal import ExecutionJournal
from trader.execution.manager import ExecutionPaused
from trader.experiments.tai_wiring import TaiMarketSource
from trader.risk.guardian import RiskGuardian, RiskLimits
from trader.risk.sizing import SizingLimits
from trader.runtime.approval import GuardedEntrySubmitter, RuntimeApproval
from trader.runtime.context import (
    AccountContextSource,
    RuntimeContextProvider,
    SizingContextSource,
    position_source,
)
from trader.runtime.services import _paper_stage_cap

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 10, 28, 15, 0, tzinfo=UTC)
BID, ASK = Decimal("29812.6"), Decimal("29814.4")  # live-shaped US100 quote (1.8 wide)


class _Audit:
    def append(self, _event: object) -> None: ...


class _Manager:
    def __init__(self) -> None:
        self.orders: list[OrderRequest] = []

    def submit(self, _cycle_id: str, order: OrderRequest) -> str:
        self.orders.append(order)
        return "receipt"


class _MarketDetails:
    def search_markets(self, _term: str) -> list[dict[str, Any]]:
        return [{"epic": "US100", "tickSize": 0.1}]

    def market_details(self, epic: str) -> dict[str, Any]:
        return {
            "instrument": {
                "epic": epic,
                "lotSize": 1,
                "marginFactor": 5,
                "marginFactorUnit": "PERCENTAGE",
                "currency": "USD",
            },
            "dealingRules": {
                "minDealSize": {"value": 0.001, "unit": "POINTS"},
                "maxDealSize": {"value": 100, "unit": "POINTS"},
                "minSizeIncrement": {"value": 0.001, "unit": "POINTS"},
            },
            "snapshot": {"marketStatus": "TRADEABLE"},
        }


def _stack(tmp_path: Path, atr: Decimal) -> tuple[GuardedEntrySubmitter, _Manager]:
    risk = yaml.safe_load((ROOT / "config" / "risk.yaml").read_text())
    instruments = yaml.safe_load((ROOT / "config" / "instruments.yaml").read_text())
    engine = create_engine(f"sqlite:///{tmp_path / 'ctx.db'}")
    tables["trade_intents"].create(engine)
    tables["snapshots"].create(engine)
    capital = FakeCapital()
    capital.balance = capital.available = Decimal("10999.78")
    broker = CapitalRuntimeBroker(capital, FIELDS, contract_size=lambda _epic: Decimal("1"))
    mapper = InstrumentMapper(instruments, _MarketDetails())
    account = AccountContextSource(
        broker, ExecutionJournal(engine), clock=lambda: NOW, default_consecutive_losses=0
    )
    market = TaiMarketSource(
        lambda _epic: SimpleNamespace(bid=BID, ask=ASK),
        SimpleNamespace(latest_atr=atr),
        "US100",
        clock=lambda: NOW,
    )
    sizing = SizingContextSource(
        mapper.get,
        SizingLimits(
            Decimal(str(risk["risk_per_trade_pct"])),
            Decimal(str(risk["max_margin_usage_pct"])),
            Decimal(str(risk["max_effective_leverage"])),
        ),
        account_currency=lambda _account: "USD",
    )
    provider = RuntimeContextProvider(
        account,
        market,
        sizing,
        position_source(broker, valuation=lambda _p: None),
        stage_cap=_paper_stage_cap(SimpleNamespace(trading_mode="paper", broker_env="demo")),
        clock=lambda: NOW,
    )
    guardian = RiskGuardian(
        RiskLimits.from_mapping(risk), _Audit(), canonical_symbols={"NAS100", "XAUUSD"}
    )
    approval = RuntimeApproval(guardian, context_provider=provider)
    manager = _Manager()
    return GuardedEntrySubmitter(approval, manager, lambda _epic: "NAS100"), manager


def _long(stop_distance: Decimal) -> OrderRequest:
    return OrderRequest(
        "cycle-1",
        "US100",
        Direction.BUY,
        Decimal("0.002"),
        ASK,
        ASK - stop_distance,
        ASK + stop_distance * 3,  # Tai's broker target is TP3 = 3R
    )


@pytest.mark.parametrize(
    ("atr", "stop_distance"),
    [
        ("8", "8"),
        ("12", "12"),
        ("20", "14"),
        ("30", "45"),
        ("40", "20"),
    ],
)
def test_a_realistic_tai_long_passes_every_guardian_rule(
    tmp_path: Path, atr: str, stop_distance: str
) -> None:
    submitter, manager = _stack(tmp_path, Decimal(atr))
    assert submitter.submit("cycle-1", _long(Decimal(stop_distance))) == "receipt"
    assert manager.orders[0].size == Decimal("0.002")


def test_the_stage_cap_is_absent_outside_paper_demo(tmp_path: Path) -> None:
    assert _paper_stage_cap(SimpleNamespace(trading_mode="auto", broker_env="demo")) is None
    submitter, _manager = _stack(tmp_path, Decimal("12"))
    assert submitter is not None


def test_a_too_tight_stop_is_still_refused_by_the_guardian(tmp_path: Path) -> None:
    submitter, manager = _stack(tmp_path, Decimal("40"))
    with pytest.raises(ExecutionPaused, match="stop_distance_outside_atr_limits"):
        submitter.submit("cycle-1", _long(Decimal("5")))  # 0.125 ATR, below the 0.5 minimum
    assert manager.orders == []
