"""Owner-directed, mockable experiment runners."""

from trader.experiments.tai_demo import (
    EXPERIMENT_LABEL,
    ClosedBar,
    DemoOutcome,
    TaiDemoRefused,
    TaiDemoRunner,
    TaiDemoSettings,
    TaiInstrumentRules,
    TaiSignal,
)
from trader.experiments.tai_feed import TaiLakeTailer, read_closed_lake_minutes
from trader.experiments.tai_wiring import (
    HA_MODES,
    CapitalBalanceProvider,
    CapitalInstrumentRulesProvider,
    TaiDemoRuntimeAdapter,
    TaiMinuteBar,
    TaiOrderFactory,
    TaiSignalProvider,
    ThreeMinuteBarBuilder,
    build_tai_demo_runtime,
    minute_bar_from_candle,
    parse_tai_instrument_rules,
)

__all__ = [
    "ClosedBar",
    "DemoOutcome",
    "EXPERIMENT_LABEL",
    "TaiDemoRefused",
    "TaiDemoRunner",
    "TaiDemoSettings",
    "TaiInstrumentRules",
    "TaiSignal",
    "CapitalBalanceProvider",
    "CapitalInstrumentRulesProvider",
    "HA_MODES",
    "TaiDemoRuntimeAdapter",
    "TaiMinuteBar",
    "TaiOrderFactory",
    "TaiSignalProvider",
    "ThreeMinuteBarBuilder",
    "build_tai_demo_runtime",
    "minute_bar_from_candle",
    "parse_tai_instrument_rules",
    "TaiLakeTailer",
    "read_closed_lake_minutes",
]
