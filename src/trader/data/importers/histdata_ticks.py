"""Rebuild bid M1 candles from HistData quotes ordered by millisecond timestamp."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import tempfile
import zipfile
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path

from trader.data.importers.histdata import import_histdata, parse_histdata_timestamp

_STAMP = re.compile(r"^\d{8} \d{9}$")


@dataclass
class _Minute:
    first: str
    last: str
    op: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    tied_open: bool = False
    tied_close: bool = False

    def add(self, stamp: str, bid: Decimal) -> None:
        self.high = max(self.high, bid)
        self.low = min(self.low, bid)
        if stamp < self.first:
            self.first, self.op, self.tied_open = stamp, bid, False
        elif stamp == self.first and bid != self.op:
            self.tied_open = True
        if stamp > self.last:
            self.last, self.close, self.tied_close = stamp, bid, False
        elif stamp == self.last and bid != self.close:
            self.tied_close = True
            self.close = bid  # Keep provider archive order when timestamps tie.


def rebuild_histdata_ticks(
    archive_path: str | Path, symbol: str, lake_root: str | Path, lineage_root: str | Path
) -> list[dict[str, object]]:
    """Order by timestamp, retaining archive sequence when timestamps tie."""
    source = Path(archive_path)
    minutes: dict[str, _Minute] = {}
    rows = 0
    reversals = 0
    previous = ""
    with zipfile.ZipFile(source) as archive:
        expected = source.stem + ".csv"
        if not expected.startswith(f"DAT_ASCII_{symbol}_T_"):
            raise ValueError("tick archive does not match requested symbol")
        info = archive.getinfo(expected)
        if info.file_size > 1024 * 1024 * 1024:
            raise ValueError("tick data exceeds the expanded size limit")
        with archive.open(expected) as member:
            for fields in csv.reader(io.TextIOWrapper(member, encoding="utf-8-sig")):
                rows += 1
                if len(fields) != 4 or not _STAMP.fullmatch(fields[0]):
                    raise ValueError("invalid HistData quote format")
                stamp = fields[0]
                if not (
                    "00" <= stamp[9:11] <= "23"
                    and "00" <= stamp[11:13] <= "59"
                    and "00" <= stamp[13:15] <= "59"
                ):
                    raise ValueError("invalid HistData quote time")
                try:
                    bid, ask, volume = (Decimal(value) for value in fields[1:])
                except InvalidOperation:
                    raise ValueError("invalid HistData quote number") from None
                if not all(value.is_finite() for value in (bid, ask, volume)):
                    raise ValueError("nonfinite HistData quote")
                if bid <= 0 or ask < bid or volume < 0:
                    raise ValueError("invalid HistData bid/ask quote")
                if previous and stamp < previous:
                    reversals += 1
                previous = stamp
                minute = stamp[:13] + "00"
                existing = minutes.get(minute)
                if existing is None:
                    parse_histdata_timestamp(minute)  # Validate the calendar date too.
                    minutes[minute] = _Minute(stamp, stamp, bid, bid, bid, bid)
                else:
                    existing.add(stamp, bid)
    if not minutes:
        raise ValueError("tick archive contains no quotes")
    tied = [key for key, value in minutes.items() if value.tied_open or value.tied_close]
    lineage = Path(lineage_root)
    lineage.mkdir(parents=True, exist_ok=True)
    metadata = {
        "source_archive": source.name,
        "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "tick_rows": rows,
        "out_of_order_quotes": reversals,
        "minute_count": len(minutes),
        "tied_endpoint_minutes": len(tied),
        "price_basis": "bid",
        "method": "Timestamp order; archive sequence for ties; extrema over all minute bids.",
        "status": "ok",
    }
    (lineage / f"{source.stem}_reconstruction.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    reconstructed = source.with_name(source.stem + "_rebuilt_bid_m1.csv")
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=source.parent, delete=False
        ) as output:
            temporary = Path(output.name)
            for minute, candle in sorted(minutes.items()):
                # HistData documents volume as unavailable; preserve zero.
                output.write(f"{minute};{candle.op};{candle.high};{candle.low};{candle.close};0\n")
        temporary.replace(reconstructed)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    reports = import_histdata(reconstructed, symbol, lake_root, lineage_root)
    for report in reports:
        report["reconstruction"] = metadata
    return reports
