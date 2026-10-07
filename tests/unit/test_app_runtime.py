"""Lifespan runtime composition gates."""

from __future__ import annotations

from types import SimpleNamespace

from trader.api import app as app_module
from trader.runtime import RuntimeComponents


class _Lease:
    def acquire(self) -> None: ...

    def check(self) -> None: ...

    def release(self) -> None: ...


class _Loop:
    def run(self, _cycle: object) -> None:
        return None


def _state(*, eligible: bool, factory: object = None) -> SimpleNamespace:
    state = SimpleNamespace(
        runtime_eligible=eligible,
        runtime=None,
        execution_control_source=SimpleNamespace(read=lambda: "run"),
    )
    if factory is not None:
        state.runtime_components_factory = factory
    return state


def test_lifespan_gate_stays_stopped_without_complete_dependencies() -> None:
    app = SimpleNamespace(state=_state(eligible=True))
    app_module._start_runtime_if_configured(app, object(), {})
    assert app.state.runtime is None


def test_lifespan_builds_runtime_from_complete_injected_composition() -> None:
    components = RuntimeComponents(
        leader=_Lease(),
        decision_loop=_Loop(),
        guardian=object(),
        broker=object(),
        order_manager=object(),
        reconciler=object(),
        report_scheduler=object(),
        settings_service=object(),
    )
    app = SimpleNamespace(
        state=_state(eligible=True, factory=lambda _settings, _configs: components)
    )
    app_module._start_runtime_if_configured(app, object(), {})
    assert app.state.runtime is not None
    assert app.state.runtime.components is components
