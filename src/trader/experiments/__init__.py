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
from trader.experiments.tai_wiring import (
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
    "TaiDemoRuntimeAdapter",
    "TaiMinuteBar",
    "TaiOrderFactory",
    "TaiSignalProvider",
    "ThreeMinuteBarBuilder",
    "build_tai_demo_runtime",
    "minute_bar_from_candle",
    "parse_tai_instrument_rules",
]
