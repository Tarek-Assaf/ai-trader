"""Deterministic Strategy Lab components."""

from trader.lab.allocator import StrategyAllocation, allocate
from trader.lab.backtest import BacktestResult, BacktestTrade, CostModel, run_backtest
from trader.lab.decay import DecayDecision, DecayMonitor
from trader.lab.ledger import ExperimentLedger, ExperimentRecord
from trader.lab.metrics import gate1_metrics
from trader.lab.miner import MiningResult, PatternCandidate, PatternMiner, mine_patterns
from trader.lab.pipeline import IdeaGenerator, PipelineDecision, PostMortem, ValidationPipeline
from trader.lab.promotion import PromotionDecision, PromotionGate
from trader.lab.runner import (
    Coverage,
    LabAttempt,
    LabJobManager,
    LabRunner,
    LabRunResult,
    coverage_report,
    holdout_months,
    load_histdata_candles,
    pessimistic_cost_model,
    prepare_holdout,
    tune_variants,
)
from trader.lab.seeds import SEED_LIBRARY, SeedStrategy, seed_library, seed_specs
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
    "IdeaGenerator",
    "MiningResult",
    "PatternCandidate",
    "PatternMiner",
    "PipelineDecision",
    "PostMortem",
    "PromotionDecision",
    "PromotionGate",
    "StrategyAllocation",
    "StrategyValidator",
    "ValidationResult",
    "allocate",
    "run_backtest",
    "mine_patterns",
    "validate_gate1",
    "ValidationPipeline",
    "Coverage",
    "LabAttempt",
    "LabJobManager",
    "LabRunResult",
    "LabRunner",
    "SEED_LIBRARY",
    "SeedStrategy",
    "coverage_report",
    "holdout_months",
    "load_histdata_candles",
    "prepare_holdout",
    "pessimistic_cost_model",
    "seed_library",
    "seed_specs",
    "tune_variants",
]
