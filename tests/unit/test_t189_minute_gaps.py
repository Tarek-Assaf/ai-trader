from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from scripts.lab_t189_minute_gaps import read_report


def _market_details() -> dict[str, object]:
    return {
        "instrument": {
            "openingHours": {
                "zone": "UTC",
                "mon": ["00:00 - 00:00"],
                "tue": ["00:00 - 00:00"],
                "wed": ["00:00 - 00:00"],
                "thu": ["00:00 - 00:00"],
                "fri": ["00:00 - 00:00"],
                "sat": [],
                "sun": [],
            }
        }
    }


def test_t189_classifies_missing_and_single_sides(tmp_path: Path) -> None:
    path = tmp_path / "NSXUSD" / "1m_capital" / "2026" / "10.parquet"
    path.parent.mkdir(parents=True)
    stamp = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)
    rows = [
        {"epic": "NSXUSD", "ts_utc": stamp, "side": "bid"},
        {"epic": "NSXUSD", "ts_utc": stamp, "side": "ask"},
        {"epic": "NSXUSD", "ts_utc": stamp.replace(minute=1), "side": "bid"},
    ]
    table = pa.Table.from_pylist(
        rows,
        schema=pa.schema(
            [
                ("epic", pa.string()),
                ("ts_utc", pa.timestamp("us", tz="UTC")),
                ("side", pa.string()),
            ]
        ),
    )
    pq.write_table(table, path)
    status_path = tmp_path / "recorder-status.json"
    status_path.write_text(json.dumps({"rejected_candles": 2, "last_reject_reason": "bad_spread"}))

    report = read_report(
        tmp_path,
        "NSXUSD",
        stamp,
        stamp.replace(minute=3),
        _market_details(),
        status_path=status_path,
    )

    assert len(report.open_minutes) == 3
    assert report.missing_minutes == (stamp.replace(minute=1), stamp.replace(minute=2))
    assert report.bid_only_minutes == (stamp.replace(minute=1),)
    assert report.ask_only_minutes == ()
    assert report.rejected_count == 2
