from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trader.data.importers import histdata_range


def test_month_range_excludes_current_and_future_months() -> None:
    months = histdata_range._months(2025, 2027, datetime(2026, 3, 15, tzinfo=UTC))

    assert months == [
        (2025, 1),
        (2025, 2),
        (2025, 3),
        (2025, 4),
        (2025, 5),
        (2025, 6),
        (2025, 7),
        (2025, 8),
        (2025, 9),
        (2025, 10),
        (2025, 11),
        (2025, 12),
        (2026, 1),
        (2026, 2),
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


def test_range_import_is_resumable_and_idempotent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    raw = tmp_path / "raw"
    downloaded: list[str] = []
    imported: list[str] = []

    def fake_download(
        symbol: str, year: int, month: int, raw_root: str | Path, timeframe: str, pause: float
    ) -> Path:
        path = Path(raw_root) / f"DAT_ASCII_{symbol}_{timeframe}_{year:04d}{month:02d}.zip"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(path.name.encode())
        downloaded.append(path.name)
        return path

    def fake_import(
        source: Path, symbol: str, lake: Path, lineage: Path
    ) -> list[dict[str, object]]:
        imported.append(source.name)
        return [
            {
                "status": "ok",
                "rows_read": 2,
                "rows_written": 2,
                "invalid_rows": 0,
                "conflicting_duplicates": 0,
                "gaps": [{"missing_minutes": 1}],
            }
        ]

    monkeypatch.setattr(histdata_range, "download_histdata", fake_download)
    monkeypatch.setattr(histdata_range, "import_histdata", fake_import)
    as_of = datetime(2026, 3, 15, tzinfo=UTC)

    first = histdata_range.import_histdata_range(
        "NSXUSD",
        2026,
        2026,
        raw,
        tmp_path / "lake",
        tmp_path / "lineage",
        pause_seconds=0,
        as_of=as_of,
    )
    second = histdata_range.import_histdata_range(
        "NSXUSD",
        2026,
        2026,
        raw,
        tmp_path / "lake",
        tmp_path / "lineage",
        pause_seconds=0,
        as_of=as_of,
    )

    assert first["status"] == "ok"
    assert first["imported_months"] == ["202601", "202602"]
    assert first["rows_written"] == 4
    assert first["gaps"] == 2
    assert second["skipped_months"] == ["202601", "202602"]
    assert second["rows_written"] == 0
    assert len(downloaded) == 2
    assert len(imported) == 2


def test_conflicted_m1_month_uses_ticks_and_records_both_hashes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    raw = tmp_path / "raw"
    calls: list[str] = []

    def fake_download(
        symbol: str, year: int, month: int, raw_root: str | Path, timeframe: str, pause: float
    ) -> Path:
        path = Path(raw_root) / f"DAT_ASCII_{symbol}_{timeframe}_{year:04d}{month:02d}.zip"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(path.name.encode())
        return path

    def fake_import(
        source: Path, symbol: str, lake: Path, lineage: Path
    ) -> list[dict[str, object]]:
        return [{"status": "conflicting_duplicates", "rows_written": 0}]

    def fake_rebuild(
        source: Path, symbol: str, lake: Path, lineage: Path
    ) -> list[dict[str, object]]:
        calls.append(source.name)
        return [
            {
                "status": "ok",
                "rows_read": 3,
                "rows_written": 2,
                "invalid_rows": 0,
                "conflicting_duplicates": 0,
                "gaps": [],
            }
        ]

    monkeypatch.setattr(histdata_range, "download_histdata", fake_download)
    monkeypatch.setattr(histdata_range, "import_histdata", fake_import)
    monkeypatch.setattr(histdata_range, "rebuild_histdata_ticks", fake_rebuild)

    summary = histdata_range.import_histdata_range(
        "XAUUSD",
        2026,
        2026,
        raw,
        tmp_path / "lake",
        tmp_path / "lineage",
        pause_seconds=0,
        as_of=datetime(2026, 2, 1, tzinfo=UTC),
    )
    manifest = json.loads((raw / "sha256-manifest.json").read_text(encoding="utf-8"))
    completed = manifest["completed_months"]["XAUUSD:2026-01"]

    assert summary["status"] == "ok"
    assert summary["imported_months"] == ["202601"]
    assert completed["method"] == "ticks"
    assert completed["tick_sha256"]
    assert completed["m1_sha256"]
    assert calls == ["DAT_ASCII_XAUUSD_T_202601.zip"]


def test_failed_month_is_reported_and_not_marked_complete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def fail_download(*args: object, **kwargs: object) -> Path:
        raise ValueError("archive unavailable")

    monkeypatch.setattr(histdata_range, "download_histdata", fail_download)
    raw = tmp_path / "raw"

    summary = histdata_range.import_histdata_range(
        "NSXUSD",
        2026,
        2026,
        raw,
        tmp_path / "lake",
        tmp_path / "lineage",
        pause_seconds=0,
        as_of=datetime(2026, 2, 1, tzinfo=UTC),
    )
    manifest = json.loads((raw / "sha256-manifest.json").read_text(encoding="utf-8"))

    assert summary["status"] == "partial"
    assert summary["rejected_months"] == [{"month": "202601", "reason": "archive unavailable"}]
    assert manifest["completed_months"] == {}
