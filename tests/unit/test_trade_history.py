from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine

from trader.core.audit import AuditEvent, SqlAuditWriter
from trader.core.db import metadata
from trader.reports.trade_history import summarize, trade_history_text

T0 = datetime(2026, 10, 29, 6, 0, tzinfo=UTC)


def _rows():
    entry = {
        "deal_id": "D1",
        "epic": "US100",
        "direction": "BUY",
        "size": "2",
        "entry_price": "20000",
        "stop": "19950",
        "tp1": "20050",
        "tp2": "20100",
        "tp3": "20150",
        "one_r_value": "100",
    }
    return [
        (T0, "entry", entry),
        (
            T0 + timedelta(minutes=5),
            "scale_out",
            {"deal_id": "D1", "target": 1, "amount": "0.8", "fraction": "0.4"},
        ),
        (T0 + timedelta(minutes=5), "stop_to_entry", {"deal_id": "D1"}),
        (T0 + timedelta(minutes=9), "reconcile_closed", {"deal_id": "D1"}),
    ]


def test_summary_shows_levels_partials_and_banked_r() -> None:
    text = summarize(_rows())
    assert "US100 BUY 2" in text
    assert "09:00" in text  # Riyadh is UTC+3
    assert "entry 20000  SL 19950  TP 20050 / 20100 / 20150" in text
    assert "TP1 hit (0.8)" in text
    assert "stop moved to entry" in text
    assert "+0.40R banked" in text
    assert "+40.00 USD" in text
    assert "closed by the broker" in text


def test_open_trade_and_empty_history() -> None:
    assert summarize([]) == "No trades recorded yet."
    assert "OPEN" in summarize(_rows()[:1])


def test_reads_back_from_audit_log() -> None:
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    writer = SqlAuditWriter(engine)
    for _at, event, payload in _rows():
        writer.append(AuditEvent(event, payload))
    assert "US100 BUY 2" in trade_history_text(engine)
