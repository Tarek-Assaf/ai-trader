from sqlalchemy import create_engine, insert

from trader.core.db import metadata, tables
from trader.execution.control import ControlMode
from trader.reports.entry_state import entry_state_lines


def test_active_when_nothing_is_latched() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    lines = entry_state_lines(engine, lambda: ControlMode.RUN)
    assert lines[0].startswith("Control mode:")
    assert lines[1] == "Entries: active (no pause latched)"


def test_paused_shows_the_reason() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(
            insert(tables["snapshots"]).values(
                id="execution-pause", payload={"reason": "execution_lease_lost"}
            )
        )
    lines = entry_state_lines(engine, lambda: ControlMode.RUN)
    assert "PAUSED (execution_lease_lost)" in lines[1]
    assert "/resume" in lines[1]


def test_unknown_without_a_database_or_with_a_failing_mode() -> None:
    def broken() -> ControlMode:
        raise RuntimeError("no")

    lines = entry_state_lines(None, broken)
    assert lines == ["Control mode: unknown", "Entries: unknown (database unavailable)"]
