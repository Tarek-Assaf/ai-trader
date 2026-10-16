"""Coverage for the pre-outcome analysis journal."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine

from trader.analysis.journal import (
    AnalysisJournal,
    AnalysisJournalError,
    InMemoryAnalysisJournal,
)

BASE = datetime(2026, 10, 1, 12, tzinfo=UTC)


def add(journal: AnalysisJournal, index: int, confidence: float, hit: bool, r: float):
    call = journal.add_call(
        created_at=BASE + timedelta(minutes=index),
        symbol="NAS100",
        timeframe="3m",
        source="screenshot",
        image_ref=f"hash-{index}",
        market_state="trend",
        bias="long",
        key_levels=[18000 + index],
        plan={"entry": 18000, "stop": 17980, "targets": [18040]},
        reasons="moving averages aligned",
        confidence=confidence,
        call_id=f"call-{index}",
    )
    return journal.resolve(
        call.id,
        {"result": "win" if hit else "loss", "r": r, "at": call.created_at + timedelta(minutes=3)},
    )


def test_add_and_resolve_once_and_immutable_fields():
    journal = InMemoryAnalysisJournal()
    call = journal.add_call(
        created_at=BASE,
        symbol="XAUUSD",
        timeframe="1m",
        source="screenshot",
        image_ref="sha256:abc",
        market_state="range",
        bias="none",
        key_levels=[2650.0],
        plan={},
        reasons="unclear structure",
        confidence=0.2,
    )
    resolved = journal.resolve(
        call.id, {"result": "void", "r": 0, "at": BASE + timedelta(minutes=1)}
    )
    assert resolved.status == "void"
    assert resolved.symbol == "XAUUSD"
    with pytest.raises(AnalysisJournalError, match="once"):
        journal.resolve(call.id, {"result": "win", "r": 1, "at": BASE + timedelta(minutes=2)})


def test_refuses_call_dated_after_supplied_outcome():
    journal = InMemoryAnalysisJournal()
    with pytest.raises(AnalysisJournalError, match="after"):
        journal.add_call(
            created_at=BASE + timedelta(hours=1),
            symbol="NAS100",
            timeframe="3m",
            source="recorded_data",
            market_state="trend",
            bias="long",
            key_levels=[],
            plan={},
            reasons="before outcome",
            confidence=0.8,
            outcome={"result": "win", "at": BASE},
        )


def test_report_has_hand_checked_confidence_arithmetic():
    journal = InMemoryAnalysisJournal()
    # Four calls per bucket: low 1/4 hits, medium 2/4, high 3/4.
    for index, (confidence, hit, r) in enumerate(
        [
            (0.2, True, 1),
            (0.2, False, -1),
            (0.2, False, -1),
            (0.2, False, -1),
            (0.5, True, 2),
            (0.5, True, 1),
            (0.5, False, -1),
            (0.5, False, -1),
            (0.8, True, 3),
            (0.8, True, 1),
            (0.8, True, 2),
            (0.8, False, -1),
        ]
    ):
        add(journal, index, confidence, hit, r)
    report = journal.report()
    assert report["open_calls"] == 0
    assert report["by_confidence"]["low"]["hit_rate"] == pytest.approx(0.25)
    assert report["by_confidence"]["medium"]["hit_rate"] == pytest.approx(0.5)
    assert report["by_confidence"]["high"]["hit_rate"] == pytest.approx(0.75)
    assert report["by_confidence"]["high"]["mean_r"] == pytest.approx(1.25)
    assert "hit rate" in report.plain_text
    assert report.to_json()


def test_sqlite_repository_persists_outcome_only():
    engine = create_engine("sqlite://")
    journal = AnalysisJournal(engine)
    call = journal.add_call(
        created_at=BASE,
        symbol="NAS100",
        timeframe="5m",
        source="recorded_data",
        market_state="transition",
        bias="short",
        key_levels=[],
        plan={},
        reasons="test persistence",
        confidence=0.7,
    )
    journal.resolve(call.id, {"result": "win", "r": 1, "at": BASE + timedelta(minutes=1)})
    recovered = AnalysisJournal(engine).get(call.id)
    assert recovered is not None
    assert recovered.status == "resolved"
    assert recovered.symbol == "NAS100"


def test_journal_module_imports_no_broker_execution_or_risk() -> None:
    import ast
    from pathlib import Path

    import trader.analysis.journal as module

    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    banned = ("broker", "execution", "risk", "gate")
    assert not [n for n in names if n.startswith("trader") and any(b in n for b in banned)]
