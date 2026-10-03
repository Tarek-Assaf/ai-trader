from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pyarrow.parquet as pq

from trader.data.importers.histdata import import_histdata, parse_histdata_timestamp


def test_fixed_est_conversion_around_us_dst_boundaries() -> None:
    # Both dates are interpreted as UTC-5, including the spring-forward weekend.
    spring = parse_histdata_timestamp("20240310 023000")
    fall = parse_histdata_timestamp("20241103 013000")
    assert spring == datetime(2024, 3, 10, 7, 30, tzinfo=UTC)
    assert fall == datetime(2024, 11, 3, 6, 30, tzinfo=UTC)
    # HistData's fixed EST clock remains UTC-5 in July (it does not follow EDT).
    july_open = parse_histdata_timestamp("20240701 093000")
    assert july_open == datetime(2024, 7, 1, 14, 30, tzinfo=UTC)


def test_import_zip_reports_qa_and_is_idempotent(tmp_path: Path) -> None:
    archive = tmp_path / "NSXUSD_M1_2024-03.zip"
    body = (
        "20240308 215900;100;101;99;100.5;5\n"
        "20240308 215900;100;101;99;100.5;5\n"
        "20240308 220100;100;101;99;100.5;5\n"
        "20240310 023000;100;101;99;100.5;5\n"
        "20240310 023100;0;101;99;100;5\n"
    )
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("DAT_ASCII_NSXUSD_M1_2024.csv", body)
    lake, lineage = tmp_path / "lake", tmp_path / "lineage"

    first = import_histdata(archive, "NSXUSD", lake, lineage)[0]
    second = import_histdata(archive, "NSXUSD", lake, lineage)[0]
    out = lake / "NSXUSD" / "1m" / "2024" / "03.parquet"
    table = pq.read_table(out)
    report = json.loads(next(lineage.glob("*.json")).read_text())

    assert first["format"] == "generic_ascii"
    assert first["duplicates"] == 1
    assert first["invalid_rows"] == 1
    assert first["rows_written"] == 3
    assert first["coverage_start_utc"] == "2024-03-09T02:59:00+00:00"
    assert first["coverage_end_utc"] == "2024-03-10T07:30:00+00:00"
    assert second["rows_written"] == 0
    assert table.num_rows == 3
    assert table.schema.names == [
        "ts_utc",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "price_basis",
        "source",
    ]
    assert report["status"] == "ok"
    assert report["gaps"][0]["classification"] == "weekend"


def test_conflicting_duplicate_does_not_write_a_partial_partition(tmp_path: Path) -> None:
    source = tmp_path / "conflict.csv"
    source.write_text(
        "20240102 120000;100;101;99;100;1\n20240102 120000;100;102;99;100;1\n",
        encoding="utf-8",
    )
    lake = tmp_path / "lake"
    report = import_histdata(source, "XAUUSD", lake, tmp_path / "lineage")[0]
    assert report["status"] == "conflicting_duplicates"
    assert report["conflicting_duplicates"] == 1
    assert not list(lake.rglob("*.parquet"))


def test_mt4_layout_is_detected(tmp_path: Path) -> None:
    source = tmp_path / "mt4.txt"
    source.write_text("2024.03.10,02:30,100,101,99,100,4\n", encoding="utf-8")
    report = import_histdata(source, "NSXUSD", tmp_path / "lake", tmp_path / "lineage")[0]
    assert report["format"] == "mt4"
    assert report["invalid_rows"] == 0
