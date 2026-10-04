"""Resumable HistData M1 importer for live monthly and archived yearly periods."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import tempfile
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO, cast

from trader.data.importers.histdata import import_histdata
from trader.data.importers.histdata_download import (
    download_histdata,
    list_histdata_periods,
)
from trader.data.importers.histdata_ticks import rebuild_histdata_ticks

_NSXUSD_M1_SHA256 = "b8d8cf3f1599ac2ae018883995df88ad552b790dd2a64c14c11d63b8883b8b8e"
_XAUUSD_M1_SHA256 = "b6bdb6989098437df2df11cb2e0f16d145c138f66425b0848bd6ce575626fd77"
_NSXUSD_TICK_SHA256 = "2a86cb3bbcb84e5c62c5ea3f19cd0d43fdaed45ef54bf7ed7ae8366f63bdbcd2"
_XAUUSD_TICK_SHA256 = "2dea6c1f5ad0ece8d7f34b5838b952f4e65ea556265833ab36a24f0223aa03cc"
_TRUSTED_SEEDS = {
    "DAT_ASCII_NSXUSD_M1_202608.zip": _NSXUSD_M1_SHA256,
    "DAT_ASCII_XAUUSD_M1_202608.zip": _XAUUSD_M1_SHA256,
    "DAT_ASCII_NSXUSD_T_202608.zip": _NSXUSD_TICK_SHA256,
    "DAT_ASCII_XAUUSD_T_202608.zip": _XAUUSD_TICK_SHA256,
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
    return cast(dict[str, Any], manifest)


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
    archives = [
        _archive_path(raw_root, symbol, year, month, "M1"),
        raw_root / f"DAT_ASCII_{symbol}_M1_{year:04d}.zip",
        _archive_path(raw_root, symbol, year, month, "T"),
    ]
    for archive in archives:
        if not archive.exists():
            continue
        if (
            completed is not None
            and "_T_" not in archive.name
            and completed.get("m1_archive", _archive_path(raw_root, symbol, year, month, "M1").name)
            != archive.name
        ):
            continue
        record = manifest["archives"].get(archive.name)
        if record is not None:
            _verify_or_pin(archive, manifest, manifest_path)
        elif completed is not None:
            expected = (
                completed.get("tick_sha256")
                if "_T_" in archive.name
                else completed.get("m1_sha256")
            )
            if expected is not None and _sha256(archive) != expected:
                raise ValueError(f"archive checksum mismatch for {archive.name}")


def _report_count(report: dict[str, object], key: str) -> int:
    value = report.get(key, 0)
    if not isinstance(value, int):
        raise ValueError(f"HistData QA metric {key} is not an integer")
    return value


def _summary_reports(reports: list[dict[str, object]]) -> dict[str, Any]:
    gaps: list[dict[str, object]] = []
    for report in reports:
        report_gaps = report.get("gaps", [])
        if isinstance(report_gaps, list):
            gaps.extend(
                cast(dict[str, object], gap) for gap in report_gaps if isinstance(gap, dict)
            )
    return {
        "rows_read": sum(_report_count(report, "rows_read") for report in reports),
        "rows_written": sum(_report_count(report, "rows_written") for report in reports),
        "invalid_rows": sum(_report_count(report, "invalid_rows") for report in reports),
        "conflicting_duplicates": sum(
            _report_count(report, "conflicting_duplicates") for report in reports
        ),
        "gaps": len(gaps),
        "gap_examples": gaps[:20],
    }


def _split_year_archive(path: Path, symbol: str, year: int, scratch: Path) -> dict[int, Path]:
    """Split a validated yearly M1 member into source-calendar-month CSV files."""
    expected = f"DAT_ASCII_{symbol}_M1_{year:04d}.csv"
    writers: dict[int, tuple[TextIO, Any]] = {}
    paths: dict[int, Path] = {}
    try:
        with zipfile.ZipFile(path) as archive:
            with archive.open(expected) as member:
                reader = csv.reader(io.TextIOWrapper(member, encoding="utf-8-sig"), delimiter=";")
                for row in reader:
                    if len(row) < 6:
                        raise ValueError("yearly M1 archive contains a malformed row")
                    match = re.match(r"^(\d{4})(\d{2})\d{2}(?:\s|$)", row[0])
                    if match is None or int(match.group(1)) != year:
                        raise ValueError("yearly M1 archive contains an unexpected date")
                    month = int(match.group(2))
                    if not 1 <= month <= 12:
                        raise ValueError("yearly M1 archive contains an invalid month")
                    if month not in writers:
                        output_path = scratch / f"DAT_ASCII_{symbol}_M1_{year:04d}{month:02d}.csv"
                        handle = output_path.open("w", encoding="utf-8", newline="")
                        writer = csv.writer(handle, delimiter=";", lineterminator=chr(10))
                        writers[month] = (handle, writer)
                        paths[month] = output_path
                    writers[month][1].writerow(row)
    except KeyError:
        raise ValueError("yearly M1 archive is missing its expected CSV member") from None
    except csv.Error:
        raise ValueError("yearly M1 archive contains malformed CSV data") from None
    finally:
        for handle, _ in writers.values():
            handle.close()
    if not paths:
        raise ValueError("yearly M1 archive contains no price rows")
    return paths


def _period_plan(
    requested: list[tuple[int, int]], available: dict[int, set[int] | None]
) -> list[dict[str, object]]:
    requested_by_year: dict[int, set[int]] = {}
    for year, month in requested:
        requested_by_year.setdefault(year, set()).add(month)
    plan: list[dict[str, object]] = []
    for year, months in sorted(requested_by_year.items()):
        provider_months = available.get(year, set())
        if provider_months is None and year in available:
            mode = "yearly"
            selected = sorted(months)
        elif provider_months:
            mode = "monthly"
            selected = sorted(months & provider_months)
        else:
            mode = "unavailable"
            selected = []
        plan.append(
            {
                "year": year,
                "mode": mode,
                "months": [f"{year:04d}{m:02d}" for m in selected],
            }
        )
    return plan


def _complete_month(
    *,
    symbol: str,
    year: int,
    month: int,
    m1_path: Path,
    m1_archive: str,
    m1_verification: str,
    archive_sha256: str | None = None,
    raw: Path,
    lake: Path,
    lineage: Path,
    manifest: dict[str, Any],
    manifest_file: Path,
    last_download_at: float | None,
    pause_seconds: float,
) -> tuple[dict[str, Any], float | None]:
    """Import one isolated month, switching to ticks only when M1 conflicts."""
    m1_reports = import_histdata(m1_path, symbol, lake, lineage)
    if not m1_reports:
        raise ValueError("M1 importer returned no QA report")
    reports = m1_reports
    method = "M1"
    tick_path = _archive_path(raw, symbol, year, month, "T")
    tick_verification: str | None = None
    if any(report.get("status") == "conflicting_duplicates" for report in m1_reports):
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

    if not reports or any(report.get("status") != "ok" for report in reports):
        raise ValueError("source failed HistData quality checks")
    totals = _summary_reports(reports)
    month_id = f"{year:04d}{month:02d}"
    entry = {
        "status": "ok",
        "method": method,
        "m1_archive": m1_archive,
        "m1_sha256": archive_sha256 or _sha256(m1_path),
        "m1_verification": m1_verification,
        "tick_sha256": _sha256(tick_path) if method == "ticks" else None,
        "tick_verification": tick_verification,
        **totals,
    }
    manifest["completed_months"][f"{symbol}:{year:04d}-{month:02d}"] = entry
    _atomic_json(manifest_file, manifest)
    return {"month": month_id, **entry}, last_download_at


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
    dry_run: bool = False,
) -> dict[str, object]:
    """Import listed HistData periods serially, resuming month by month."""
    if symbol not in _ALLOWED_SYMBOLS:
        raise ValueError("unsupported HistData symbol")
    if pause_seconds < 0 or pause_seconds > 3600:
        raise ValueError("pause_seconds must be between 0 and 3600")
    now = as_of or datetime.now(UTC)
    months = _months(start_year, through_year, now)
    periods = list_histdata_periods(symbol, pause_seconds)
    plan = _period_plan(months, periods)
    result: dict[str, Any] = {
        "status": "planned" if dry_run else "ok",
        "symbol": symbol,
        "start_year": start_year,
        "through_year": through_year,
        "period_plan": plan,
        "rows_read": 0,
        "rows_written": 0,
        "invalid_rows": 0,
        "conflicting_duplicates": 0,
        "gaps": 0,
        "gap_examples": [],
        "imported_months": [],
        "skipped_months": [],
        "unavailable_months": [],
        "rejected_months": [],
    }
    if dry_run:
        return result

    raw = Path(raw_root)
    lake = Path(lake_root)
    lineage = Path(lineage_root)
    manifest_file = Path(manifest_path) if manifest_path else raw / "sha256-manifest.json"
    raw.mkdir(parents=True, exist_ok=True)
    manifest = _load_manifest(manifest_file)
    _atomic_json(manifest_file, manifest)
    last_download_at: float | None = None

    def add_report(report: dict[str, Any]) -> None:
        result["imported_months"].append(report["month"])
        for metric in (
            "rows_read",
            "rows_written",
            "invalid_rows",
            "conflicting_duplicates",
            "gaps",
        ):
            result[metric] = int(result[metric]) + int(report[metric])
        result["gap_examples"].extend(report["gap_examples"])

    for year_entry in plan:
        year = int(year_entry["year"])
        mode = str(year_entry["mode"])
        selected = [int(str(value)[4:]) for value in year_entry["months"]]
        if mode == "unavailable":
            continue
        if mode == "yearly":
            archive_path = raw / f"DAT_ASCII_{symbol}_M1_{year:04d}.zip"
            try:
                pending = []
                for month in selected:
                    key = f"{symbol}:{year:04d}-{month:02d}"
                    completed = manifest["completed_months"].get(key)
                    if isinstance(completed, dict) and completed.get("status") == "ok":
                        _check_cached_archives(
                            raw, symbol, year, month, manifest, manifest_file, completed
                        )
                        result["skipped_months"].append(f"{year:04d}{month:02d}")
                    else:
                        pending.append(month)
                if not pending:
                    continue
                if not archive_path.exists() and last_download_at is not None and pause_seconds:
                    elapsed = time.monotonic() - last_download_at
                    if elapsed < pause_seconds:
                        time.sleep(pause_seconds - elapsed)
                was_cached = archive_path.exists()
                archive_path = download_histdata(symbol, year, None, raw, "M1", pause_seconds)
                if not was_cached:
                    last_download_at = time.monotonic()
                verification = _verify_or_pin(archive_path, manifest, manifest_file)
                with tempfile.TemporaryDirectory(prefix=f".histdata-{year}-", dir=raw) as temp:
                    split = _split_year_archive(archive_path, symbol, year, Path(temp))
                    present_months = sorted(split)
                    first_present, last_present = present_months[0], present_months[-1]
                    for month in pending:
                        month_path = split.get(month)
                        if month_path is None:
                            month_id = f"{year:04d}{month:02d}"
                            if month < first_present or month > last_present:
                                result["unavailable_months"].append(month_id)
                            else:
                                result["rejected_months"].append(
                                    {
                                        "month": month_id,
                                        "reason": "yearly archive has an internal month gap",
                                    }
                                )
                                result["status"] = "partial"
                            continue
                        report, last_download_at = _complete_month(
                            symbol=symbol,
                            year=year,
                            month=month,
                            m1_path=month_path,
                            m1_archive=archive_path.name,
                            m1_verification=verification,
                            archive_sha256=_sha256(archive_path),
                            raw=raw,
                            lake=lake,
                            lineage=lineage,
                            manifest=manifest,
                            manifest_file=manifest_file,
                            last_download_at=last_download_at,
                            pause_seconds=pause_seconds,
                        )
                        add_report(report)
            except (OSError, ValueError, zipfile.BadZipFile, KeyError) as exc:
                result["status"] = "partial"
                for month in selected:
                    key = f"{symbol}:{year:04d}-{month:02d}"
                    completed = manifest["completed_months"].get(key)
                    if not isinstance(completed, dict) or completed.get("status") != "ok":
                        result["rejected_months"].append(
                            {
                                "month": f"{year:04d}{month:02d}",
                                "reason": str(exc)[:240],
                            }
                        )
            continue

        for month in selected:
            month_id = f"{year:04d}{month:02d}"
            key = f"{symbol}:{year:04d}-{month:02d}"
            completed = manifest["completed_months"].get(key)
            try:
                if isinstance(completed, dict) and completed.get("status") == "ok":
                    _check_cached_archives(
                        raw, symbol, year, month, manifest, manifest_file, completed
                    )
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
                verification = _verify_or_pin(m1_path, manifest, manifest_file)
                report, last_download_at = _complete_month(
                    symbol=symbol,
                    year=year,
                    month=month,
                    m1_path=m1_path,
                    m1_archive=m1_path.name,
                    m1_verification=verification,
                    raw=raw,
                    lake=lake,
                    lineage=lineage,
                    manifest=manifest,
                    manifest_file=manifest_file,
                    last_download_at=last_download_at,
                    pause_seconds=pause_seconds,
                )
                add_report(report)
            except (OSError, ValueError, zipfile.BadZipFile, KeyError) as exc:
                result["status"] = "partial"
                result["rejected_months"].append({"month": month_id, "reason": str(exc)[:240]})

    result["gap_examples"] = result["gap_examples"][:20]
    if result["rejected_months"]:
        result["status"] = "partial"
    return result
