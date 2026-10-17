from __future__ import annotations

import pytest

from trader.lab.backtest import CostModel
from trader.lab.positive_control import generate_minutes, planted_strategy
from trader.lab.spec import StopExit, TargetExit
from trader.lab.staged import (
    MAX_CHILDREN_PER_PARENT,
    StagedSearch,
    WindowStats,
    mutations,
    spec_hash,
    split_windows,
    window_evaluator,
)


def _exits(spec):  # type: ignore[no-untyped-def]
    stop = next(r for r in spec.exit_rules if isinstance(r, StopExit))
    target = next(r for r in spec.exit_rules if isinstance(r, TargetExit))
    return stop.distance, target.distance


def test_mutations_are_one_change_unique_and_bounded() -> None:
    parent = planted_strategy()
    children = mutations(parent)
    hashes = {spec_hash(spec) for _, spec in children}
    assert 10 <= len(children) <= MAX_CHILDREN_PER_PARENT
    assert len(hashes) == len(children) and spec_hash(parent) not in hashes
    labels = {label for label, _ in children}
    assert {"exit_atr_2_4", "time_stop_24", "session_london", "trend_with"} <= labels
    assert all(len(spec.entry_conditions) <= 5 for _, spec in children)


def _fake_evaluator(good_exits=(2.0, 4.0)):  # type: ignore[no-untyped-def]
    def evaluate(spec, window):  # type: ignore[no-untyped-def]
        wins = _exits(spec) == good_exits
        net = 1.0 if wins else -1.0
        if window == "other" and not wins:
            net = -1.0
        return WindowStats(150, net, net + 3.0)

    return evaluate


def test_planted_mutation_is_found_and_dropped_variants_count_as_trials() -> None:
    parent = planted_strategy()
    search = StagedSearch(_fake_evaluator(), max_generations=2)
    outcome = search.run([parent])
    assert any(_exits(c.spec) == (2.0, 4.0) for c in outcome.survivors)
    survivors = {c.hash for c in outcome.survivors}
    evaluated = {c.hash for c in outcome.evaluated}
    assert survivors < evaluated  # dropped variants were evaluated and counted too
    assert outcome.trials == len(evaluated | {spec_hash(parent)})  # every distinct spec, once
    assert outcome.generations[0].trials_so_far <= outcome.generations[-1].trials_so_far


def test_prior_trials_are_carried_into_the_global_count() -> None:
    parent = planted_strategy()
    prior = {f"prior-{n}" for n in range(500)}
    outcome = StagedSearch(_fake_evaluator(), max_generations=1, prior_hashes=prior).run([parent])
    assert outcome.trials >= 500 + len(outcome.evaluated)


def test_a_variant_must_hold_on_later_years_and_the_other_market() -> None:
    def evaluate(spec, window):  # type: ignore[no-untyped-def]
        # Looks great early, collapses later: must never survive.
        return WindowStats(200, 2.0 if window == "early" else -0.5, 3.0)

    outcome = StagedSearch(evaluate, max_generations=1).run([planted_strategy()])
    assert outcome.survivors == [] and outcome.trials > 1


def test_stop_request_ends_the_search() -> None:
    outcome = StagedSearch(_fake_evaluator(), should_stop=lambda: True).run([planted_strategy()])
    assert outcome.generations == [] and outcome.survivors == []


def test_generation_cap_is_three() -> None:
    with pytest.raises(ValueError):
        StagedSearch(_fake_evaluator(), max_generations=4)


def test_real_engine_evaluator_runs_and_windows_split_by_time() -> None:
    minutes = generate_minutes(11, 40, drift=0.2)
    from trader.data.resample import resample_ohlc

    bars = [b for b in resample_ohlc(minutes, 5) if b.complete]
    windows = split_windows(bars, bars)
    assert windows["early"][-1].ts_utc < windows["later"][0].ts_utc
    cost = CostModel(spread_points={"NSXUSD": 2.0}, slippage_points={"NSXUSD": 1.0})
    evaluate = window_evaluator(windows, cost, other_instrument="NSXUSD")
    early = evaluate(planted_strategy(), "early")
    assert early.trades > 20 and early.gross_per_trade > early.net_per_trade


def _load_script():  # type: ignore[no-untyped-def]
    import importlib.util
    import sys
    from pathlib import Path

    path = Path(__file__).parents[2] / "scripts" / "lab_staged_search.py"
    spec = importlib.util.spec_from_file_location("lab_staged_search", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["lab_staged_search"] = module
    spec.loader.exec_module(module)
    return module


def test_script_picks_parents_with_trades_and_gross_edge_and_seeds_the_ledger() -> None:
    from datetime import UTC, datetime

    script = _load_script()
    good, flat, thin = planted_strategy(), planted_strategy(), planted_strategy()
    pool = {"good": good, "flat": flat, "thin": thin}
    attempts = [
        {
            "strategy_hash": "good",
            "timeframe": 5,
            "metrics": {
                "trades": 400,
                "diag_avg_gross_per_trade": 1.7,
                "out_of_sample_profit_factor": 1.2,
            },
        },
        {
            "strategy_hash": "flat",
            "timeframe": 5,
            "metrics": {"trades": 400, "diag_avg_gross_per_trade": -0.2},
        },
        {
            "strategy_hash": "thin",
            "timeframe": 5,
            "metrics": {"trades": 50, "diag_avg_gross_per_trade": 3},
        },
        {"strategy_hash": "other_tf", "timeframe": 15, "metrics": {"trades": 900}},
    ]
    assert script.pick_parents(attempts, pool, 5, 10) == [good]
    ledger = script.trial_ledger(
        {"a", "b"}, datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 2, 1, tzinfo=UTC)
    )
    assert ledger.count() == 2


def test_generation_payload_is_json_ready_for_slots_dataclass() -> None:
    import json

    from trader.lab.staged import GenerationReport, generation_payload

    row = generation_payload(GenerationReport(1, 10, 120, 2, 2506))
    assert row == {
        "generation": 1,
        "parents": 10,
        "children_evaluated": 120,
        "survivors": 2,
        "trials_so_far": 2506,
    }
    assert json.loads(json.dumps(row)) == row
