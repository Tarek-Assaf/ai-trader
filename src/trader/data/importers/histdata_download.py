"""Download a HistData monthly or yearly ASCII archive from its public form."""

from __future__ import annotations

import io
import re
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import UTC, datetime
from email.message import Message
from html.parser import HTMLParser
from pathlib import Path
from typing import IO

BASE = "https://www.histdata.com"
MAX_BYTES = 128 * 1024 * 1024
MAX_YEARLY_M1_BYTES = 512 * 1024 * 1024
MAX_YEARLY_M1_EXPANDED_BYTES = 2 * 1024 * 1024 * 1024


class _FormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.active = False
        self.fields: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "form":
            self.active = values.get("id") == "file_down"
            if self.active and values.get("action") != "/get.php":
                raise ValueError("unexpected HistData download endpoint")
        elif self.active and tag == "input":
            name, value = values.get("name"), values.get("value")
            if name is not None and value is not None:
                self.fields[name] = value

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self.active = False


class _PeriodParser(HTMLParser):
    """Read HistData's visible year/month archive selector for one symbol."""

    _ROUTE = re.compile(
        r"^/ascii/1-minute-bar-quotes/(nsxusd|xauusd)/(\d{4})(?:/(\d{1,2}))?/?$"
    )

    def __init__(self, symbol: str) -> None:
        super().__init__()
        self.symbol = symbol.casefold()
        self.years: set[int] = set()
        self.months: dict[int, set[int]] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "a":
            return
        href = dict(attrs).get("href")
        if not href:
            return
        url = urllib.parse.urljoin(BASE + "/", href)
        parts = urllib.parse.urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.hostname != "www.histdata.com"
            or parts.path != "/download-free-forex-historical-data/"
        ):
            return
        match = self._ROUTE.fullmatch(parts.query)
        if match is None or match.group(1) != self.symbol:
            return
        year = int(match.group(2))
        month = match.group(3)
        if month is None:
            self.years.add(year)
        else:
            value = int(month)
            if not 1 <= value <= 12:
                return
            self.months.setdefault(year, set()).add(value)

    def periods(self) -> dict[int, set[int] | None]:
        """Prefer listed month archives; None identifies a year archive."""
        result: dict[int, set[int] | None] = dict(self.months)
        for year in self.years:
            result.setdefault(year, None)
        return result


def list_histdata_periods(
    symbol: str, pause_seconds: float = 0.0
) -> dict[int, set[int] | None]:
    """Read the provider's visible M1 selector to avoid requesting absent months."""
    if symbol not in {"NSXUSD", "XAUUSD"}:
        raise ValueError("unsupported HistData symbol")
    if pause_seconds < 0 or pause_seconds > 3600:
        raise ValueError("pause_seconds must be between 0 and 3600")
    page = (
        f"{BASE}/download-free-forex-historical-data/"
        f"?/ascii/1-minute-bar-quotes/{symbol.lower()}"
    )
    parser = _PeriodParser(symbol)
    try:
        parser.feed(_request(page).decode("utf-8"))
    except UnicodeDecodeError:
        raise ValueError("HistData period selector is not UTF-8") from None
    periods = parser.periods()
    if not periods:
        raise ValueError("HistData period selector has no matching archives")
    if pause_seconds:
        time.sleep(pause_seconds)
    return periods


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> None:
        raise ValueError("unexpected HistData redirect")


def _request(
    url: str,
    data: bytes | None = None,
    referer: str | None = None,
    *,
    max_bytes: int = MAX_BYTES,
) -> bytes:
    headers = {"User-Agent": "ai-trader HistData importer"}
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.build_opener(_NoRedirect).open(
            request, timeout=60
        ) as response:
            if response.status != 200:
                raise ValueError("HistData returned an unsuccessful status")
            content = bytes(response.read(max_bytes + 1))
    except (urllib.error.URLError, OSError):
        raise ValueError("HistData download failed; retry later") from None
    if len(content) > max_bytes:
        raise ValueError("HistData response exceeds the archive size limit")
    return content


def _validate_archive(
    raw: bytes, symbol: str, year: int, month: int | None, timeframe: str = "M1"
) -> None:
    period = f"{year:04d}{month:02d}" if month is not None else f"{year:04d}"
    expected = f"DAT_ASCII_{symbol}_{timeframe}_{period}.csv"
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            expanded_limit = (
                MAX_YEARLY_M1_EXPANDED_BYTES
                if month is None and timeframe == "M1"
                else MAX_BYTES
                if timeframe == "M1"
                else 1024 * 1024 * 1024
            )
            if sum(member.file_size for member in archive.infolist()) > expanded_limit:
                raise ValueError("HistData archive exceeds the expanded size limit")
            if expected not in archive.namelist():
                raise ValueError(
                    "HistData archive does not match the requested symbol/month"
                )
            if any(
                name not in {expected, expected[:-4] + ".txt"}
                for name in archive.namelist()
            ):
                raise ValueError("HistData archive contains unexpected members")
            if archive.testzip() is not None:
                raise ValueError("HistData archive checksum failed")
    except zipfile.BadZipFile:
        raise ValueError("HistData did not return a valid ZIP archive") from None


def download_histdata(
    symbol: str,
    year: int,
    month: int | None,
    raw_root: str | Path,
    timeframe: str = "M1",
    pause_seconds: float = 0.0,
) -> Path:
    """Cache a verified monthly or yearly archive atomically."""
    if pause_seconds < 0 or pause_seconds > 3600:
        raise ValueError("pause_seconds must be between 0 and 3600")
    if symbol not in {"NSXUSD", "XAUUSD"}:
        raise ValueError("unsupported HistData symbol")
    if timeframe not in {"M1", "T"}:
        raise ValueError("unsupported HistData timeframe")
    if not 1900 <= year <= 2100:
        raise ValueError("HistData year must be between 1900 and 2100")
    now = datetime.now(UTC)
    if month is None:
        if timeframe != "M1" or year >= now.year:
            raise ValueError("select a completed yearly M1 archive")
        period = f"{year:04d}"
        max_bytes = MAX_YEARLY_M1_BYTES
    else:
        try:
            requested = datetime(year, month, 1, tzinfo=UTC)
        except ValueError:
            raise ValueError("select a valid historical month") from None
        if requested >= now.replace(day=1, hour=0, minute=0, second=0, microsecond=0):
            raise ValueError("select a completed historical month")
        period = f"{year:04d}{month:02d}"
        max_bytes = MAX_BYTES
    root = Path(raw_root)
    target = root / f"DAT_ASCII_{symbol}_{timeframe}_{period}.zip"
    if target.exists():
        if target.stat().st_size > max_bytes:
            raise ValueError("cached HistData archive exceeds the size limit")
        _validate_archive(target.read_bytes(), symbol, year, month, timeframe)
        return target
    layout = "1-minute-bar-quotes" if timeframe == "M1" else "tick-data-quotes"
    suffix = f"/{year}" if month is None else f"/{year}/{month}"
    page = f"{BASE}/download-free-forex-historical-data/?/ascii/{layout}/{symbol.lower()}{suffix}"
    parser = _FormParser()
    parser.feed(_request(page).decode("utf-8"))
    if pause_seconds:
        time.sleep(pause_seconds)
    fields = parser.fields
    required = {
        "fxpair": symbol,
        "date": str(year),
        "platform": "ASCII",
        "timeframe": timeframe,
    }
    allowed_fields = set(required) | {"tk", "datemonth"}
    if month is not None:
        required["datemonth"] = f"{year:04d}{month:02d}"
    elif fields.get("datemonth") not in {None, ""}:
        raise ValueError("HistData yearly form contains an unexpected month")
    if (
        any(fields.get(key) != value for key, value in required.items())
        or not fields.get("tk")
        or set(fields) - allowed_fields
    ):
        raise ValueError(
            "HistData download form is missing or does not match the request"
        )
    raw = _request(
        f"{BASE}/get.php",
        urllib.parse.urlencode(fields).encode(),
        page,
        max_bytes=max_bytes,
    )
    _validate_archive(raw, symbol, year, month, timeframe)
    root.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=root, suffix=".tmp", delete=False
        ) as output:
            temporary = Path(output.name)
            output.write(raw)
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target

