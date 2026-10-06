"""Deterministic Strategy Lab components."""

from trader.lab.allocator import StrategyAllocation, allocate
from trader.lab.backtest import BacktestResult, BacktestTrade, CostModel, run_backtest
from trader.lab.decay import DecayDecision, DecayMonitor
from trader.lab.ledger import ExperimentLedger, ExperimentRecord
from trader.lab.metrics import gate1_metrics
from trader.lab.miner import MiningResult, PatternCandidate, PatternMiner, mine_patterns
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
    "gate1_metrics",
    "MiningResult",
    "PatternCandidate",
    "PatternMiner",
    "PromotionDecision",
    "PromotionGate",
    "StrategyAllocation",
    "StrategyValidator",
    "ValidationResult",
    "allocate",
    "run_backtest",
    "mine_patterns",
    "validate_gate1",
]
