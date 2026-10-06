"""Deterministic Strategy Lab components."""

from trader.lab.allocator import StrategyAllocation, allocate
from trader.lab.backtest import BacktestResult, BacktestTrade, CostModel, run_backtest
from trader.lab.decay import DecayDecision, DecayMonitor
from trader.lab.ledger import ExperimentLedger, ExperimentRecord
from trader.lab.promotion import PromotionDecision, PromotionGate
from trader.lab.validator import Gate1Config, StrategyValidator, ValidationResult, validate_gate1

__all__ = [
    "BacktestResult",
    "BacktestTrade",
    "CostModel",
    "DecayDecision",
    "DecayMonitor",
    "ExperimentLedger",
    "ExperimentRecord",
    "Gate1Config",
    "PromotionDecision",
    "PromotionGate",
    "StrategyAllocation",
    "StrategyValidator",
    "ValidationResult",
    "allocate",
    "run_backtest",
    "validate_gate1",
]
