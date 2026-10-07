"""Fail-safe runtime composition for the core demo process."""

from trader.runtime.approval import (
    GuardianInputs,
    PnlLossGuard,
    ProtectivePositionManager,
    RuntimeApproval,
    RuntimeExecutor,
    RuntimeReconciler,
)
from trader.runtime.composition import (
    CycleTrigger,
    Lease,
    RuntimeComponents,
    RuntimeConfig,
    RuntimeResult,
    RuntimeService,
    RuntimeState,
    ScheduleCycleSource,
    build_runtime,
    runtime_enabled,
)
from trader.runtime.context import (
    AccountContextSource,
    ContextUnavailable,
    MarketContextSource,
    RuntimeContextProvider,
    SizingContextSource,
    position_source,
)
from trader.runtime.factory import (
    OrderManagerDecisionLoop,
    RuntimeDependencyUnavailable,
    RuntimeFactories,
    build_default_factories,
    build_runtime_components,
)
from trader.runtime.feeds import RuntimeFeedAdapters
from trader.runtime.services import build_runtime_services
from trader.runtime.soak import LogScanner, RuntimeSoakFeed
from trader.scaling.runtime import CoreAutoscaler, build_core_autoscaler

__all__ = [
    "CycleTrigger",
    "Lease",
    "RuntimeComponents",
    "RuntimeConfig",
    "RuntimeResult",
    "RuntimeService",
    "RuntimeState",
    "ScheduleCycleSource",
    "RuntimeFactories",
    "RuntimeDependencyUnavailable",
    "OrderManagerDecisionLoop",
    "RuntimeFeedAdapters",
    "GuardianInputs",
    "PnlLossGuard",
    "ProtectivePositionManager",
    "RuntimeApproval",
    "RuntimeContextProvider",
    "AccountContextSource",
    "ContextUnavailable",
    "MarketContextSource",
    "SizingContextSource",
    "position_source",
    "RuntimeExecutor",
    "RuntimeReconciler",
    "build_runtime_components",
    "build_default_factories",
    "build_runtime_services",
    "build_runtime",
    "runtime_enabled",
    "CoreAutoscaler",
    "build_core_autoscaler",
    "RuntimeSoakFeed",
    "LogScanner",
]
