from __future__ import annotations

import json
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trader.data.importers import histdata_range


def _report(*, status: str = "ok", rows: int = 2) -> list[dict[str, object]]:
    return [
        {
            "status": status,
            "rows_read": rows,
            "rows_written": rows if status == "ok" else 0,
            "invalid_rows": 0,
            "conflicting_duplicates": 1 if status == "conflicting_duplicates" else 0,
            "gaps": [],
        }
    ]


def _year_archive(path: Path, symbol: str = "NSXUSD") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        rows = "".join(
            f"2024{month:02d}03 120000;100;101;99;100;0\n"
            for month in range(1, 13)
        )
        archive.writestr(f"DAT_ASCII_{symbol}_M1_2024.csv", rows)
    return path


def test_month_range_excludes_current_and_future_months() -> None:
    months = histdata_range._months(2025, 2027, datetime(2026, 3, 15, tzinfo=UTC))
    assert months == [
        (2025, 1), (2025, 2), (2025, 3), (2025, 4), (2025, 5), (2025, 6),
        (2025, 7), (2025, 8), (2025, 9), (2025, 10), (2025, 11), (2025, 12),
        (2026, 1), (2026, 2),
    ]


def test_period_plan_prefers_monthly_and_reports_yearly_and_unavailable() -> None:
    plan = histdata_range._period_plan(
        [(2023, 1), (2024, 1), (2024, 2), (2025, 1), (2025, 2)],
        {2023: None, 2024: {2}, 2025: {1}},
    )
    assert plan == [
        {"year": 2023, "mode": "yearly", "months": ["202301"]},
        {"year": 2024, "mode": "monthly", "months": ["202402"]},
        {"year": 2025, "mode": "monthly", "months": ["202501"]},
    ]


def test_manifest_pins_first_seen_and_rejects_changed_cache(tmp_path: Path) -> None:
    manifest_path = tmp_path / "sha256-manifest.json"
    archive = tmp_path / "DAT_ASCII_NSXUSD_M1_202501.zip"
    archive.write_bytes(b"first downloaded archive")
    manifest = histdata_range._load_manifest(manifest_path)
    verification = histdata_range._verify_or_pin(archive, manifest, manifest_path)
    loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert verification == "first_seen_cache_integrity"
    assert loaded["archives"][archive.name]["verification"] == "first_seen_cache_integrity"
    archive.write_bytes(b"changed archive")
    with pytest.raises(ValueError, match="checksum mismatch"):
        histdata_range._verify_or_pin(archive, manifest, manifest_path)


def test_yearly_import_resumes_without_reimporting_cached_months(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    raw = tmp_path / "raw"
    archive = _year_archive(raw / "DAT_ASCII_NSXUSD_M1_2024.zip")
    downloads: list[tuple[int, int | None]] = []
    imports: list[str] = []

    def fake_download(symbol, year, month, raw_root, timeframe="M1", pause_seconds=0):
        downloads.append((year, month))
        return archive

    def fake_import(source: Path, symbol: str, lake: Path, lineage: Path):
        imports.append(source.name)
        return _report()

    monkeypatch.setattr(
        histdata_range, "list_histdata_periods", lambda *_args, **_kwargs: {2024: None}
    )
    monkeypatch.setattr(histdata_range, "download_histdata", fake_download)
    monkeypatch.setattr(histdata_range, "import_histdata", fake_import)
    args = ("NSXUSD", 2024, 2024, raw, tmp_path / "lake", tmp_path / "lineage")
    options = {"pause_seconds": 0, "as_of": datetime(2025, 1, 1, tzinfo=UTC)}

    first = histdata_range.import_histdata_range(*args, **options)
    second = histdata_range.import_histdata_range(*args, **options)
    manifest = json.loads((raw / "sha256-manifest.json").read_text(encoding="utf-8"))

    assert first["status"] == "ok"
    assert first["imported_months"] == [f"2024{month:02d}" for month in range(1, 13)]
    assert first["rows_written"] == 4
    assert second["skipped_months"] == [f"2024{month:02d}" for month in range(1, 13)]
    assert second["rows_written"] == 0
    assert downloads == [(2024, None)]
    assert imports == [f"DAT_ASCII_NSXUSD_M1_2024{month:02d}.csv" for month in range(1, 13)]
    assert manifest["completed_months"]["NSXUSD:2024-01"]["m1_archive"] == archive.name
    assert (
        manifest["completed_months"]["NSXUSD:2024-02"]["m1_sha256"]
        == manifest["archives"][archive.name]["sha256"]
    )


def test_yearly_conflict_uses_only_monthly_ticks_for_that_month(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    raw = tmp_path / "raw"
    yearly = _year_archive(raw / "DAT_ASCII_XAUUSD_M1_2024.zip", "XAUUSD")
    downloaded: list[tuple[int, int | None, str]] = []
    rebuilt: list[str] = []

    def fake_download(symbol, year, month, raw_root, timeframe="M1", pause_seconds=0):
        downloaded.append((year, month, timeframe))
        if month is None:
            return yearly
        path = Path(raw_root) / f"DAT_ASCII_{symbol}_{timeframe}_{year:04d}{month:02d}.zip"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"tick archive")
        return path

    def fake_import(source: Path, symbol: str, lake: Path, lineage: Path):
        return _report(status="conflicting_duplicates" if "202401" in source.name else "ok")

    def fake_rebuild(source: Path, symbol: str, lake: Path, lineage: Path):
        rebuilt.append(source.name)
        return _report(rows=3)

    monkeypatch.setattr(
        histdata_range, "list_histdata_periods", lambda *_args, **_kwargs: {2024: None}
    )
    monkeypatch.setattr(histdata_range, "download_histdata", fake_download)
    monkeypatch.setattr(histdata_range, "import_histdata", fake_import)
    monkeypatch.setattr(histdata_range, "rebuild_histdata_ticks", fake_rebuild)

    result = histdata_range.import_histdata_range(
        "XAUUSD", 2024, 2024, raw, tmp_path / "lake", tmp_path / "lineage",
        pause_seconds=0, as_of=datetime(2025, 1, 1, tzinfo=UTC),
    )
    manifest = json.loads((raw / "sha256-manifest.json").read_text(encoding="utf-8"))
    jan = manifest["completed_months"]["XAUUSD:2024-01"]
    feb = manifest["completed_months"]["XAUUSD:2024-02"]

    assert result["status"] == "ok"
    assert jan["method"] == "ticks" and jan["tick_sha256"]
    assert feb["method"] == "M1" and feb["tick_sha256"] is None
    assert jan["m1_sha256"] == feb["m1_sha256"]
    assert rebuilt == ["DAT_ASCII_XAUUSD_T_202401.zip"]
    assert downloaded == [(2024, None, "M1"), (2024, 1, "T")]


def test_monthly_archives_are_used_when_listed_and_missing_months_are_not_requested(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    downloads: list[tuple[int, int | None]] = []

    def fake_download(symbol, year, month, raw_root, timeframe="M1", pause_seconds=0):
        downloads.append((year, month))
        path = Path(raw_root) / f"DAT_ASCII_{symbol}_{timeframe}_{year:04d}{month:02d}.zip"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(path.name.encode())
        return path

    monkeypatch.setattr(
        histdata_range, "list_histdata_periods", lambda *_args, **_kwargs: {2024: {1}}
    )
    monkeypatch.setattr(histdata_range, "download_histdata", fake_download)
    monkeypatch.setattr(histdata_range, "import_histdata", lambda *_args: _report())

    result = histdata_range.import_histdata_range(
        "NSXUSD", 2024, 2024, tmp_path / "raw", tmp_path / "lake", tmp_path / "lineage",
        pause_seconds=0, as_of=datetime(2024, 3, 1, tzinfo=UTC),
    )
    assert result["period_plan"] == [{"year": 2024, "mode": "monthly", "months": ["202401"]}]
    assert result["imported_months"] == ["202401"]
    assert downloads == [(2024, 1)]


def test_dry_run_returns_provider_plan_without_touching_disk(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        histdata_range, "list_histdata_periods", lambda *_args, **_kwargs: {2024: None}
    )
    result = histdata_range.import_histdata_range(
        "NSXUSD", 2024, 2024, tmp_path / "raw", tmp_path / "lake", tmp_path / "lineage",
        pause_seconds=0, as_of=datetime(2025, 1, 1, tzinfo=UTC), dry_run=True,
    )
    assert result["status"] == "planned"
    assert result["period_plan"] == [
        {"year": 2024, "mode": "yearly", "months": [f"2024{m:02d}" for m in range(1, 13)]}
    ]
    assert not (tmp_path / "raw").exists()


def test_failed_year_is_reported_for_every_unfinished_month(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fail_download(*_args, **_kwargs):
        raise ValueError("year archive unavailable")

    monkeypatch.setattr(
        histdata_range, "list_histdata_periods", lambda *_args, **_kwargs: {2024: None}
    )
    monkeypatch.setattr(histdata_range, "download_histdata", fail_download)
    result = histdata_range.import_histdata_range(
        "NSXUSD", 2024, 2024, tmp_path / "raw", tmp_path / "lake", tmp_path / "lineage",
        pause_seconds=0, as_of=datetime(2024, 3, 1, tzinfo=UTC),
    )
    assert result["status"] == "partial"
    assert [item["month"] for item in result["rejected_months"]] == ["202401", "202402"]


def test_partial_first_year_marks_absent_edge_months_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    raw = tmp_path / "raw"
    archive_path = raw / "DAT_ASCII_NSXUSD_M1_2010.zip"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(
            "DAT_ASCII_NSXUSD_M1_2010.csv",
            "20101103 120000;100;101;99;100;0\n"
            "20101203 120000;100;101;99;100;0\n",
        )
    monkeypatch.setattr(
        histdata_range, "list_histdata_periods", lambda *_args, **_kwargs: {2010: None}
    )
    monkeypatch.setattr(
        histdata_range, "download_histdata", lambda *_args, **_kwargs: archive_path
    )
    imported: list[str] = []

    def fake_import(source: Path, symbol: str, lake: Path, lineage: Path):
        imported.append(source.name)
        return _report()

    monkeypatch.setattr(histdata_range, "import_histdata", fake_import)
    result = histdata_range.import_histdata_range(
        "NSXUSD", 2010, 2010, raw, tmp_path / "lake", tmp_path / "lineage",
        pause_seconds=0, as_of=datetime(2011, 1, 1, tzinfo=UTC),
    )
    assert result["status"] == "ok"
    assert result["imported_months"] == ["201011", "201012"]
    assert result["unavailable_months"] == [f"2010{month:02d}" for month in range(1, 11)]
    assert result["rejected_months"] == []
    assert imported == ["DAT_ASCII_NSXUSD_M1_201011.csv", "DAT_ASCII_NSXUSD_M1_201012.csv"]
