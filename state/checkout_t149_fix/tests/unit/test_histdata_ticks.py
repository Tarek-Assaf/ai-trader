from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from trader.data.importers.histdata_ticks import rebuild_histdata_ticks


def _zip(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "DAT_ASCII_NSXUSD_T_202401.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("DAT_ASCII_NSXUSD_T_202401.csv", body)
    return path


def test_timestamp_order_rebuilds_full_minutes_and_repeat_is_idempotent(tmp_path: Path) -> None:
    archive = _zip(
        tmp_path,
        "20240102 120000001,100,101,0\n"
        "20240102 120030000,102,103,0\n"
        "20240102 120100000,105,106,0\n"
        "20240102 120059999,103,104,0\n"
        "20240102 120020000,99,100,0\n",
    )
    lake, lineage = tmp_path / "lake", tmp_path / "lineage"
    first = rebuild_histdata_ticks(archive, "NSXUSD", lake, lineage)[0]
    second = rebuild_histdata_ticks(archive, "NSXUSD", lake, lineage)[0]
    bars = pq.read_table(lake / "NSXUSD" / "1m" / "2024" / "01.parquet").to_pylist()
    assert first["rows_written"] == 2
    assert second["rows_written"] == 0
    assert len(bars) == 2
    assert bars[0]["ts_utc"] == datetime(2024, 1, 2, 17, tzinfo=UTC)
    assert tuple(bars[0][key] for key in ("open", "high", "low", "close")) == (100, 103, 99, 103)
    assert bars[0]["price_basis"] == "bid"
    assert "rebuilt_bid_m1" in bars[0]["source"]
    metadata = json.loads(next(lineage.glob("*_reconstruction.json")).read_text())
    assert metadata["tick_rows"] == 5
    assert metadata["out_of_order_quotes"] == 2
    assert metadata["tied_endpoint_minutes"] == 0


def test_equal_timestamp_quotes_retain_provider_archive_sequence(tmp_path: Path) -> None:
    archive = _zip(
        tmp_path,
        "20240102 120000001,100,101,0\n"
        "20240102 120059999,101,102,0\n"
        "20240102 120059999,102,103,0\n",
    )
    report = rebuild_histdata_ticks(archive, "NSXUSD", tmp_path / "lake", tmp_path / "lineage")[0]
    bars = pq.read_table(tmp_path / "lake" / "NSXUSD" / "1m" / "2024" / "01.parquet").to_pylist()
    assert bars[0]["close"] == 102
    assert report["reconstruction"]["tied_endpoint_minutes"] == 1


@pytest.mark.parametrize(
    "bad_row",
    [
        "20240102 120000000,nan,101,0",
        "20240102 120000000,100,99,0",
        "20240102 126000000,100,101,0",
        "20241302 120000000,100,101,0",
        "broken",
    ],
)
def test_invalid_tick_source_never_partially_commits(tmp_path: Path, bad_row: str) -> None:
    archive = _zip(tmp_path, "20240102 120000001,100,101,0\n" + bad_row + "\n")
    with pytest.raises(ValueError):
        rebuild_histdata_ticks(archive, "NSXUSD", tmp_path / "lake", tmp_path / "lineage")
    assert not list((tmp_path / "lake").rglob("*.parquet"))
