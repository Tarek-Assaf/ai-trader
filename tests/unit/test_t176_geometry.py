from datetime import UTC, datetime

from scripts.lab_t176_geometry import positions


def row(kind, number, stamp, signal, price, qty=1):
    return {
        "Type": kind,
        "Trade number": str(number),
        "dt": datetime.fromisoformat(stamp).replace(tzinfo=UTC),
        "Signal": signal,
        "price": price,
        "qty": qty,
    }


def test_positions_group_tranches_and_targets():
    entries = [
        row("Entry long", 1, "2026-01-01T00:00:00", "Buy market order", 100.0, 1),
        row("Entry long", 2, "2026-01-01T00:00:00", "Buy market order", 100.0, 3),
    ]
    rows = entries + [
        row("Exit long", 1, "2026-01-01T00:03:00", "Bracket Stop Loss", 98.0),
        row("Exit long", 2, "2026-01-01T00:06:00", "Bracket Take Profit", 102.0),
    ]
    result = positions(rows, entries)
    assert len(result) == 1
    assert result[0]["tranche_count"] == 2
    assert result[0]["quantity"] == 4
    assert result[0]["target_multiples"] == [1.0]


def test_positions_record_market_exit_as_unproducible_row():
    entries = [row("Entry short", 1, "2026-01-01T00:00:00", "Sell market order", 100.0)]
    rows = entries + [row("Exit short", 1, "2026-01-01T00:03:00", "Buy market order", 101.0)]
    result = positions(rows, entries)
    assert len(result[0]["market_exit_rows"]) == 1
