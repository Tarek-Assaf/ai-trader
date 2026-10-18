"""Known-answer checks for the bounded T-159 time-alignment study."""

from datetime import UTC, datetime, timedelta

from scripts.lab_t159_time_alignment import Trade, _correlation, read_trades, score_shift


def test_correlation_and_shift_score_use_the_expected_offset() -> None:
    start = datetime(2026, 10, 6, 12, tzinfo=UTC)
    trades = tuple(
        Trade(
            str(index),
            start + timedelta(minutes=index * 5),
            start + timedelta(minutes=index * 5 + 5),
            100.0 + index,
            101.0 + index,
        )
        for index in range(3)
    )
    prices = {
        trade.entry_time + timedelta(minutes=240): 200.0 + index
        for index, trade in enumerate(trades)
    }
    prices.update(
        {
            trade.exit_time + timedelta(minutes=240): 201.0 + index
            for index, trade in enumerate(trades)
        }
    )
    result = score_shift(trades, prices, 240)
    assert result["comparable"] == 3
    assert result["agreement"] == 1.0
    assert _correlation(((1.0, 1.0), (2.0, 2.0))) == 1.0


def test_read_trades_collapses_entry_and_exit_rows(tmp_path) -> None:
    path = tmp_path / "owner.csv"
    path.write_text(
        "Trade number,Type,Date and time,Price USD\n"
        "1,Entry long,2026-10-06 12:00,100\n"
        "1,Exit long,2026-10-06 12:05,101\n"
        "2,Entry short,2026-10-06 13:00,200\n"
        "2,Exit short,2026-10-06 13:05,199\n",
        encoding="utf-8",
    )
    trades = read_trades(path)
    assert len(trades) == 2
    assert trades[0].entry_time.tzinfo is UTC
    assert trades[1].exit_price == 199.0
