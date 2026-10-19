#!/usr/bin/env python3
"""Read-only recorder-lake minute-gap diagnosis for T-189.

The command reads existing Parquet files and a saved Capital.com market-details
payload.  It never contacts the broker and never mutates the lake.  A Markdown
report can be written with ``--out``; the same report is always printed.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from trader.data.market_hours import MarketHours

DEFAULT_EPIC = "NSXUSD"


def parse_stamp(value: str) -> datetime:
    """Parse an ISO timestamp and normalize it to a UTC minute."""
    stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return stamp.astimezone(UTC).replace(second=0, microsecond=0)


def _candidate_paths(lake_root: Path, epic: str) -> tuple[Path, ...]:
    aliases = (epic, "NAS100", "US100") if epic == DEFAULT_EPIC else (epic,)
    paths = [
        lake_root / alias / timeframe for alias in aliases for timeframe in ("1m_capital", "1m")
    ]
    return tuple(
        sorted({path for root in paths if root.is_dir() for path in root.rglob("*.parquet")})
    )


def _row_stamp(value: object) -> datetime:
    if isinstance(value, datetime):
        stamp = value
    else:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC).replace(second=0, microsecond=0)


@dataclass(frozen=True, slots=True)
class GapReport:
    epic: str
    start: datetime
    end: datetime
    open_minutes: tuple[datetime, ...]
    missing_minutes: tuple[datetime, ...]
    bid_only_minutes: tuple[datetime, ...]
    ask_only_minutes: tuple[datetime, ...]
    rejected_count: int | None
    last_reject_reason: str | None
    source_files: tuple[str, ...]


def read_report(
    lake_root: str | Path,
    epic: str,
    start: datetime,
    end: datetime,
    market_details: object,
    *,
    status_path: str | Path | None = None,
) -> GapReport:
    """Read one UTC window and classify open-minute gaps and side singles."""
    if (
        start.tzinfo is None
        or end.tzinfo is None
        or start.utcoffset() is None
        or end.utcoffset() is None
    ):
        raise ValueError("window timestamps must include a timezone")
    start = start.astimezone(UTC).replace(second=0, microsecond=0)
    end = end.astimezone(UTC).replace(second=0, microsecond=0)
    if end <= start:
        raise ValueError("window end must be after start")
    schedule = MarketHours.from_market_details(market_details)
    aliases = {epic, "NAS100", "US100"} if epic == DEFAULT_EPIC else {epic}
    sides: dict[datetime, set[str]] = defaultdict(set)
    source_files: list[str] = []
    try:
        import pyarrow.parquet as pq  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - supplied by project runtime
        raise RuntimeError("pyarrow is required for T-189") from exc
    for path in _candidate_paths(Path(lake_root), epic):
        try:
            names = set(pq.ParquetFile(path).schema.names)
            if not {"epic", "ts_utc", "side"}.issubset(names):
                continue
            for row in pq.read_table(path, columns=["epic", "ts_utc", "side"]).to_pylist():
                if str(row.get("epic") or "") not in aliases:
                    continue
                stamp = _row_stamp(row["ts_utc"])
                side = str(row.get("side") or "").lower()
                if start <= stamp < end and side in {"bid", "ask"}:
                    sides[stamp].add(side)
            source_files.append(str(path))
        except (OSError, ValueError, TypeError, KeyError):
            continue

    open_minutes: list[datetime] = []
    cursor = start
    while cursor < end:
        if schedule.is_open(cursor):
            open_minutes.append(cursor)
        cursor += timedelta(minutes=1)
    missing = [stamp for stamp in open_minutes if not {"bid", "ask"}.issubset(sides[stamp])]
    bid_only = [stamp for stamp in open_minutes if sides[stamp] == {"bid"}]
    ask_only = [stamp for stamp in open_minutes if sides[stamp] == {"ask"}]

    rejected_count: int | None = None
    last_reject_reason: str | None = None
    if status_path is not None and Path(status_path).exists():
        status = json.loads(Path(status_path).read_text(encoding="utf-8"))
        if isinstance(status, dict):
            value = status.get("rejected_candles")
            rejected_count = value if isinstance(value, int) else None
            reason = status.get("last_reject_reason")
            last_reject_reason = reason if isinstance(reason, str) else None
    return GapReport(
        epic=epic,
        start=start,
        end=end,
        open_minutes=tuple(open_minutes),
        missing_minutes=tuple(missing),
        bid_only_minutes=tuple(bid_only),
        ask_only_minutes=tuple(ask_only),
        rejected_count=rejected_count,
        last_reject_reason=last_reject_reason,
        source_files=tuple(source_files),
    )


def _fmt(stamp: datetime) -> str:
    return stamp.isoformat().replace("+00:00", "Z")


def render_markdown(report: GapReport) -> str:
    """Render a bounded report, listing at most the first 20 timestamps per class."""

    def values(items: tuple[datetime, ...]) -> str:
        if not items:
            return "none"
        rendered = ", ".join(_fmt(item) for item in items[:20])
        return rendered + (f" (+{len(items) - 20} more)" if len(items) > 20 else "")

    rejected = "unknown (no status file supplied)"
    if report.rejected_count is not None:
        rejected = str(report.rejected_count)
        if report.last_reject_reason:
            rejected += f"; last reason: `{report.last_reject_reason}`"
    return (
        "\n".join(
            [
                f"# T-189 recorder minute-gap report ({report.epic})",
                "",
                f"Window: `{_fmt(report.start)}` to `{_fmt(report.end)}` (end exclusive)",
                f"Open minutes: **{len(report.open_minutes)}**",
                f"Missing paired bid/ask minutes: **{len(report.missing_minutes)}**",
                f"Bid-only open minutes: **{len(report.bid_only_minutes)}**",
                f"Ask-only open minutes: **{len(report.ask_only_minutes)}**",
                f"Rejected-candle status count: **{rejected}**",
                "",
                f"Missing: {values(report.missing_minutes)}",
                f"Bid-only: {values(report.bid_only_minutes)}",
                f"Ask-only: {values(report.ask_only_minutes)}",
                "",
                "Source files:",
                *(f"- `{path}`" for path in report.source_files),
                "",
                "This report is read-only. Missing minutes are classified with `MarketHours`; "
                "the status count is process-wide and is not restricted to this window.",
            ]
        )
        + "\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lake-root", required=True, type=Path)
    parser.add_argument("--market-details-json", required=True, type=Path)
    parser.add_argument("--start", required=True, type=parse_stamp)
    parser.add_argument("--end", required=True, type=parse_stamp)
    parser.add_argument("--epic", default=DEFAULT_EPIC)
    parser.add_argument("--status-path", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    details = json.loads(args.market_details_json.read_text(encoding="utf-8"))
    report = read_report(
        args.lake_root,
        args.epic,
        args.start,
        args.end,
        details,
        status_path=args.status_path,
    )
    rendered = render_markdown(report)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
