"""Fail-safe runtime composition for the core demo process."""

from trader.runtime.approval import (
    GuardianInputs,
    PnlLossGuard,
    ProtectivePositionManager,
    RuntimeApproval,
    RuntimeContextProvider,
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
from trader.runtime.factory import (
    OrderManagerDecisionLoop,
    RuntimeDependencyUnavailable,
    RuntimeFactories,
    build_default_factories,
    build_runtime_components,
)
from trader.runtime.feeds import RuntimeFeedAdapters
from trader.runtime.services import build_runtime_services

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
    "RuntimeExecutor",
    "RuntimeReconciler",
    "build_runtime_components",
    "build_default_factories",
    "build_runtime_services",
    "build_runtime",
    "runtime_enabled",
]
