"""Import HistData M1 CSV exports into the UTC Parquet lake."""

from __future__ import annotations

import csv
import json
import re
import zipfile
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import TypedDict, cast

import pyarrow as pa
import pyarrow.parquet as pq

UTC_MINUS_FIVE = timezone(timedelta(hours=-5), name="HistData EST (fixed UTC-5)")
OUTPUT_COLUMNS = ["ts_utc", "open", "high", "low", "close", "volume", "price_basis", "source"]
_DATE_RE = re.compile(r"^\d{8}$")


class BarRow(TypedDict):
    ts_utc: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    price_basis: str
    source: str


@dataclass
class QAReport:
    source: str
    symbol: str
    format: str = "unknown"
    rows_read: int = 0
    rows_written: int = 0
    coverage_start_utc: str | None = None
    coverage_end_utc: str | None = None
    invalid_rows: int = 0
    duplicates: int = 0
    conflicting_duplicates: int = 0
    outliers: int = 0
    outlier_examples: list[str] = field(default_factory=list)
    gaps: list[dict[str, str | int]] = field(default_factory=list)
    invalid_examples: list[dict[str, str | int]] = field(default_factory=list)
    conflict_examples: list[str] = field(default_factory=list)
    output_files: list[str] = field(default_factory=list)
    status: str = "ok"


def parse_histdata_timestamp(date_text: str, time_text: str | None = None) -> datetime:
    """Parse HistData wall time as fixed EST (UTC-5), including DST dates."""
    if time_text is None and " " in date_text.strip():
        date_text, time_text = date_text.strip().split(None, 1)
    if time_text is None:
        raise ValueError("missing time")
    date_text = date_text.strip()
    time_text = time_text.strip()
    if _DATE_RE.fullmatch(date_text):
        local = datetime.strptime(date_text + time_text, "%Y%m%d%H%M%S")
    else:
        local = datetime.strptime(f"{date_text} {time_text}", "%Y.%m.%d %H:%M")
    return local.replace(tzinfo=UTC_MINUS_FIVE).astimezone(UTC)


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("file is not valid text")


def _rows(text: str) -> tuple[str, Iterable[tuple[int, list[str]]]]:
    lines = [(n, line.strip()) for n, line in enumerate(text.splitlines(), 1) if line.strip()]
    if not lines:
        return "generic_ascii", []
    delimiter = ";" if ";" in lines[0][1] else ","
    parsed = [(n, next(csv.reader([line], delimiter=delimiter))) for n, line in lines]
    first = [x.strip().lower() for x in parsed[0][1]]
    if first and first[0] in {"date", "datetime", "timestamp"}:
        parsed = parsed[1:]
    sample = parsed[0][1] if parsed else []
    mt4 = len(sample) >= 7 and "." in sample[0] and ":" in sample[1]
    return ("mt4" if mt4 else "generic_ascii"), parsed


def _parse_row(row: list[str], layout: str) -> tuple[datetime, float, float, float, float, float]:
    if layout == "mt4":
        date, clock, *values = row
        ts = parse_histdata_timestamp(date, clock)
    else:
        if len(row) < 6:
            raise ValueError("expected datetime and five numeric values")
        ts = parse_histdata_timestamp(row[0])
        values = row[1:]
    if len(values) < 5:
        raise ValueError("expected open, high, low, close and volume")
    op = float(values[0].strip())
    high = float(values[1].strip())
    low = float(values[2].strip())
    close = float(values[3].strip())
    volume = float(values[4].strip())
    if any(not (x > 0 and x < float("inf")) for x in (op, high, low, close)):
        raise ValueError("prices must be finite and positive")
    if not (low <= min(op, close) <= max(op, close) <= high):
        raise ValueError("OHLC bounds are inconsistent")
    if not (volume >= 0 and volume < float("inf")):
        raise ValueError("volume must be finite and non-negative")
    return ts, op, high, low, close, volume


def _input_files(path: Path) -> Iterable[tuple[str, bytes]]:
    if path.is_dir():
        for item in sorted(path.iterdir()):
            if item.is_file() and item.suffix.lower() in {".zip", ".csv", ".txt"}:
                yield from _input_files(item)
    elif zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            for name in sorted(archive.namelist()):
                if not name.endswith("/") and Path(name).suffix.lower() in {".csv", ".txt"}:
                    yield f"{path.name}:{name}", archive.read(name)
    else:
        yield path.name, path.read_bytes()


def _gap_kind(previous: datetime, current: datetime) -> str:
    cursor = previous.date()
    while cursor <= current.date():
        if cursor.weekday() >= 5:
            return "weekend"
        cursor += timedelta(days=1)
    # Exact session breaks require a confirmed instrument calendar; avoid guessing.
    return "unexpected"


def import_histdata(
    source_path: str | Path,
    symbol: str,
    lake_root: str | Path,
    lineage_root: str | Path,
) -> list[dict[str, object]]:
    """Import ZIP/CSV/TXT inputs; output one QA JSON report for each input member."""
    source = Path(source_path)
    lake = Path(lake_root)
    lineage = Path(lineage_root)
    reports: list[dict[str, object]] = []
    for source_name, raw in _input_files(source):
        report = QAReport(source=source_name, symbol=symbol)
        try:
            layout, parsed = _rows(_decode(raw))
            report.format = layout
            unique: dict[datetime, tuple[float, float, float, float, float]] = {}
            conflicted: set[datetime] = set()
            for line_number, row in parsed:
                report.rows_read += 1
                try:
                    ts, op, hi, lo, close, vol = _parse_row(row, layout)
                except (ValueError, OverflowError) as exc:
                    report.invalid_rows += 1
                    if len(report.invalid_examples) < 20:
                        report.invalid_examples.append({"line": line_number, "reason": str(exc)})
                    continue
                values = (op, hi, lo, close, vol)
                if ts in unique:
                    if unique[ts] == values:
                        report.duplicates += 1
                    else:
                        report.conflicting_duplicates += 1
                        unique.pop(ts, None)
                        conflicted.add(ts)
                        if len(report.conflict_examples) < 20:
                            report.conflict_examples.append(ts.isoformat())
                    continue
                if ts in conflicted:
                    continue
                unique[ts] = values
            if report.conflicting_duplicates:
                report.status = "conflicting_duplicates"
            new_rows: list[BarRow] = []
            for ts, values in sorted(unique.items()):
                op, hi, lo, close, vol = values
                new_rows.append(
                    {
                        "ts_utc": ts,
                        "open": op,
                        "high": hi,
                        "low": lo,
                        "close": close,
                        "volume": vol,
                        "price_basis": "bid",
                        "source": source_name,
                    }
                )
            if new_rows:
                report.coverage_start_utc = new_rows[0]["ts_utc"].isoformat()
                report.coverage_end_utc = new_rows[-1]["ts_utc"].isoformat()
            for prev, cur in zip(new_rows, new_rows[1:], strict=False):
                gap = int((cur["ts_utc"] - prev["ts_utc"]).total_seconds() // 60) - 1
                if gap > 0:
                    report.gaps.append(
                        {
                            "after": prev["ts_utc"].isoformat(),
                            "before": cur["ts_utc"].isoformat(),
                            "missing_minutes": gap,
                            "classification": _gap_kind(prev["ts_utc"], cur["ts_utc"]),
                        }
                    )
                prior_close = float(prev["close"])
                if abs(float(cur["close"]) / prior_close - 1.0) > 0.20:
                    report.outliers += 1
                    if len(report.outlier_examples) < 20:
                        report.outlier_examples.append(cur["ts_utc"].isoformat())
            # Conflicts fail closed: the source file is not partially committed.
            by_partition: dict[tuple[int, int], list[BarRow]] = {}
            if not report.conflicting_duplicates:
                for row in new_rows:
                    ts = row["ts_utc"]
                    by_partition.setdefault((ts.year, ts.month), []).append(row)
            schema = pa.schema(
                [
                    ("ts_utc", pa.timestamp("us", tz="UTC")),
                    ("open", pa.float64()),
                    ("high", pa.float64()),
                    ("low", pa.float64()),
                    ("close", pa.float64()),
                    ("volume", pa.float64()),
                    ("price_basis", pa.string()),
                    ("source", pa.string()),
                ]
            )
            for (year, month), rows in by_partition.items():
                out = lake / symbol / "1m" / f"{year:04d}" / f"{month:02d}.parquet"
                prior: list[dict[str, object]] = []
                if out.exists():
                    prior.extend(cast(BarRow, row) for row in pq.read_table(out).to_pylist())
                # Existing timestamps are stable on repeat imports. Changed source data
                # at a previously imported timestamp is reported and left untouched.
                merged: dict[datetime, BarRow] = {r["ts_utc"]: r for r in prior}
                added = 0
                for row in rows:
                    existing = merged.get(row["ts_utc"])
                    if existing is None:
                        merged[row["ts_utc"]] = row
                        added += 1
                    elif any(existing[key] != row[key] for key in OUTPUT_COLUMNS[1:7]):
                        report.conflicting_duplicates += 1
                        report.status = "conflicting_duplicates"
                        if len(report.conflict_examples) < 20:
                            report.conflict_examples.append(row["ts_utc"].isoformat())
                table = pa.Table.from_pylist([merged[k] for k in sorted(merged)], schema=schema)
                out.parent.mkdir(parents=True, exist_ok=True)
                tmp = out.with_suffix(".parquet.tmp")
                pq.write_table(table, tmp)
                tmp.replace(out)
                report.output_files.append(str(out))
                report.rows_written += added
        except (OSError, ValueError, zipfile.BadZipFile, pa.ArrowException) as exc:
            report.status = "error"
            report.invalid_examples.append({"line": 0, "reason": str(exc)[:240]})
        lineage.mkdir(parents=True, exist_ok=True)
        report_stem = Path(source_name.replace(":", "_")).stem
        report_path = lineage / f"{symbol}_{report_stem}_qa.json"
        report_path.write_text(
            json.dumps(asdict(report), indent=2, default=str) + "\n", encoding="utf-8"
        )
        reports.append(asdict(report))
    return reports
