"""Resumable, rate-limited month-range importer for HistData M1 archives."""

from __future__ import annotations

import hashlib
import json
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from trader.data.importers.histdata import import_histdata
from trader.data.importers.histdata_download import download_histdata
from trader.data.importers.histdata_ticks import rebuild_histdata_ticks

_TRUSTED_SEEDS = {
    "DAT_ASCII_NSXUSD_M1_202608.zip": "b8d8cf3f1599ac2ae018883995df88ad552b790dd2a64c14c11d63b8883b8b8e",
    "DAT_ASCII_XAUUSD_M1_202608.zip": "b6bdb6989098437df2df11cb2e0f16d145c138f66425b0848bd6ce575626fd77",
    "DAT_ASCII_NSXUSD_T_202608.zip": "2a86cb3bbcb84e5c62c5ea3f19cd0d43fdaed45ef54bf7ed7ae8366f63bdbcd2",
    "DAT_ASCII_XAUUSD_T_202608.zip": "2dea6c1f5ad0ece8d7f34b5838b952f4e65ea556265833ab36a24f0223aa03cc",
}
_ALLOWED_SYMBOLS = {"NSXUSD", "XAUUSD"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _load_manifest(path: Path) -> dict[str, Any]:
    if path.exists():
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("checksum manifest is unreadable; refusing to continue") from exc
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema_version") != 1
            or not isinstance(manifest.get("archives"), dict)
            or not isinstance(manifest.get("completed_months"), dict)
        ):
            raise ValueError("checksum manifest has an unsupported shape")
    else:
        manifest = {
            "schema_version": 1,
            "description": (
                "August hashes are trusted report seeds. First-seen hashes for other archives "
                "provide cache integrity only, not independent source verification."
            ),
            "archives": {},
            "completed_months": {},
        }

    for filename, expected in _TRUSTED_SEEDS.items():
        existing = manifest["archives"].get(filename)
        record = {"sha256": expected, "verification": "trusted_report"}
        if existing is not None and existing != record:
            raise ValueError(f"trusted checksum entry conflicts for {filename}")
        manifest["archives"][filename] = record
    return manifest


def _verify_or_pin(path: Path, manifest: dict[str, Any], manifest_path: Path) -> str:
    filename = path.name
    actual = _sha256(path)
    record = manifest["archives"].get(filename)
    if record is not None:
        if (
            not isinstance(record, dict)
            or record.get("sha256") != actual
            or record.get("verification") not in {"trusted_report", "first_seen_cache_integrity"}
        ):
            raise ValueError(f"archive checksum mismatch for {filename}")
        return str(record["verification"])
    manifest["archives"][filename] = {
        "sha256": actual,
        "verification": "first_seen_cache_integrity",
    }
    _atomic_json(manifest_path, manifest)
    return "first_seen_cache_integrity"


def _months(start_year: int, through_year: int, as_of: datetime) -> list[tuple[int, int]]:
    if not 1900 <= start_year <= through_year <= 2100:
        raise ValueError("provide a valid inclusive year range from 1900 through 2100")
    current = as_of.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last_year = min(through_year, current.year)
    result: list[tuple[int, int]] = []
    for year in range(start_year, last_year + 1):
        for month in range(1, 13):
            if year == current.year and month >= current.month:
                break
            result.append((year, month))
    if not result:
        raise ValueError("the requested range contains no completed historical months")
    return result


def _archive_path(raw_root: Path, symbol: str, year: int, month: int, timeframe: str) -> Path:
    return raw_root / f"DAT_ASCII_{symbol}_{timeframe}_{year:04d}{month:02d}.zip"


def _check_cached_archives(
    raw_root: Path,
    symbol: str,
    year: int,
    month: int,
    manifest: dict[str, Any],
    manifest_path: Path,
    completed: dict[str, Any] | None,
) -> None:
    for timeframe in ("M1", "T"):
        archive = _archive_path(raw_root, symbol, year, month, timeframe)
        if not archive.exists():
            continue
        record = manifest["archives"].get(archive.name)
        if record is not None:
            _verify_or_pin(archive, manifest, manifest_path)
        elif completed is not None:
            key = "m1_sha256" if timeframe == "M1" else "tick_sha256"
            expected = completed.get(key)
            if expected is not None and _sha256(archive) != expected:
                raise ValueError(f"archive checksum mismatch for {archive.name}")


def _summary_reports(reports: list[dict[str, object]]) -> dict[str, Any]:
    gaps: list[dict[str, object]] = []
    for report in reports:
        report_gaps = report.get("gaps", [])
        if isinstance(report_gaps, list):
            gaps.extend(
                cast(dict[str, object], gap) for gap in report_gaps if isinstance(gap, dict)
            )
    return {
        "rows_read": sum(int(report.get("rows_read", 0)) for report in reports),
        "rows_written": sum(int(report.get("rows_written", 0)) for report in reports),
        "invalid_rows": sum(int(report.get("invalid_rows", 0)) for report in reports),
        "conflicting_duplicates": sum(
            int(report.get("conflicting_duplicates", 0)) for report in reports
        ),
        "gaps": len(gaps),
        "gap_examples": gaps[:20],
    }


def import_histdata_range(
    symbol: str,
    start_year: int,
    through_year: int,
    raw_root: str | Path,
    lake_root: str | Path,
    lineage_root: str | Path,
    manifest_path: str | Path | None = None,
    pause_seconds: float = 1.0,
    as_of: datetime | None = None,
) -> dict[str, object]:
    """Download and import completed months serially, resuming from an atomic manifest."""
    if symbol not in _ALLOWED_SYMBOLS:
        raise ValueError("unsupported HistData symbol")
    if pause_seconds < 0 or pause_seconds > 3600:
        raise ValueError("pause_seconds must be between 0 and 3600")
    now = as_of or datetime.now(UTC)
    months = _months(start_year, through_year, now)
    raw = Path(raw_root)
    lake = Path(lake_root)
    lineage = Path(lineage_root)
    manifest_file = Path(manifest_path) if manifest_path else raw / "sha256-manifest.json"
    raw.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest(manifest_file)
    _atomic_json(manifest_file, manifest)

    result: dict[str, Any] = {
        "status": "ok",
        "symbol": symbol,
        "start_year": start_year,
        "through_year": through_year,
        "rows_read": 0,
        "rows_written": 0,
        "invalid_rows": 0,
        "conflicting_duplicates": 0,
        "gaps": 0,
        "gap_examples": [],
        "imported_months": [],
        "skipped_months": [],
        "rejected_months": [],
    }
    last_download_at: float | None = None

    for year, month in months:
        key = f"{symbol}:{year:04d}-{month:02d}"
        month_id = f"{year:04d}{month:02d}"
        completed = manifest["completed_months"].get(key)
        try:
            is_complete = isinstance(completed, dict) and completed.get("status") == "ok"
            _check_cached_archives(
                raw,
                symbol,
                year,
                month,
                manifest,
                manifest_file,
                completed if is_complete else None,
            )
            if isinstance(completed, dict) and completed.get("status") == "ok":
                result["skipped_months"].append(month_id)
                continue

            m1_path = _archive_path(raw, symbol, year, month, "M1")
            if not m1_path.exists() and last_download_at is not None and pause_seconds:
                elapsed = time.monotonic() - last_download_at
                if elapsed < pause_seconds:
                    time.sleep(pause_seconds - elapsed)
            was_cached = m1_path.exists()
            m1_path = download_histdata(symbol, year, month, raw, "M1", pause_seconds)
            if not was_cached:
                last_download_at = time.monotonic()
            m1_verification = _verify_or_pin(m1_path, manifest, manifest_file)
            reports = import_histdata(m1_path, symbol, lake, lineage)
            if not reports:
                raise ValueError("M1 importer returned no QA report")
            if any(report.get("status") == "conflicting_duplicates" for report in reports):
                tick_path = _archive_path(raw, symbol, year, month, "T")
                if not tick_path.exists() and last_download_at is not None and pause_seconds:
                    elapsed = time.monotonic() - last_download_at
                    if elapsed < pause_seconds:
                        time.sleep(pause_seconds - elapsed)
                was_cached = tick_path.exists()
                tick_path = download_histdata(symbol, year, month, raw, "T", pause_seconds)
                if not was_cached:
                    last_download_at = time.monotonic()
                tick_verification = _verify_or_pin(tick_path, manifest, manifest_file)
                reports = rebuild_histdata_ticks(tick_path, symbol, lake, lineage)
                method = "ticks"
                archive_sha = _sha256(tick_path)
            else:
                tick_verification = None
                method = "M1"
                archive_sha = _sha256(m1_path)

            if not reports or any(report.get("status") != "ok" for report in reports):
                raise ValueError("source failed HistData quality checks")
            totals = _summary_reports(reports)
            imported = {
                "status": "ok",
                "m1_sha256": _sha256(m1_path),
                "m1_verification": m1_verification,
                "tick_sha256": archive_sha if method == "ticks" else None,
                "tick_verification": tick_verification,
                "method": method,
                **totals,
            }
            manifest["completed_months"][key] = imported
            _atomic_json(manifest_file, manifest)
            result["imported_months"].append(month_id)
            for metric in (
                "rows_read",
                "rows_written",
                "invalid_rows",
                "conflicting_duplicates",
                "gaps",
            ):
                result[metric] = int(result[metric]) + int(totals[metric])
            result["gap_examples"].extend(totals["gap_examples"])
        except (OSError, ValueError, zipfile.BadZipFile, KeyError) as exc:
            result["status"] = "partial"
            result["rejected_months"].append(  # type: ignore[union-attr]
                {"month": month_id, "reason": str(exc)[:240]}
            )

    result["gap_examples"] = result["gap_examples"][:20]
    if result["rejected_months"]:
        result["status"] = "partial"
    return result
