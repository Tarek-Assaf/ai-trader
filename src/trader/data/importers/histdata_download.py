"""Download a monthly HistData ASCII M1 archive from its public download form."""

from __future__ import annotations

import io
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


def _request(url: str, data: bytes | None = None, referer: str | None = None) -> bytes:
    headers = {"User-Agent": "ai-trader HistData importer"}
    if referer:
        headers["Referer"] = referer
    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=60) as response:
            if response.status != 200:
                raise ValueError("HistData returned an unsuccessful status")
            content = bytes(response.read(MAX_BYTES + 1))
    except (urllib.error.URLError, OSError):
        raise ValueError("HistData download failed; retry later") from None
    if len(content) > MAX_BYTES:
        raise ValueError("HistData response exceeds the archive size limit")
    return content


def _validate_archive(
    raw: bytes, symbol: str, year: int, month: int, timeframe: str = "M1"
) -> None:
    expected = f"DAT_ASCII_{symbol}_{timeframe}_{year:04d}{month:02d}.csv"
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            expanded_limit = MAX_BYTES if timeframe == "M1" else 1024 * 1024 * 1024
            if sum(member.file_size for member in archive.infolist()) > expanded_limit:
                raise ValueError("HistData archive exceeds the expanded size limit")
            if expected not in archive.namelist():
                raise ValueError("HistData archive does not match the requested symbol/month")
            if any(name not in {expected, expected[:-4] + ".txt"} for name in archive.namelist()):
                raise ValueError("HistData archive contains unexpected members")
            if archive.testzip() is not None:
                raise ValueError("HistData archive checksum failed")
    except zipfile.BadZipFile:
        raise ValueError("HistData did not return a valid ZIP archive") from None


def download_histdata(
    symbol: str,
    year: int,
    month: int,
    raw_root: str | Path,
    timeframe: str = "M1",
    pause_seconds: float = 0.0,
) -> Path:
    """Cache a verified archive atomically; valid cached files need no new request."""
    if pause_seconds < 0 or pause_seconds > 3600:
        raise ValueError("pause_seconds must be between 0 and 3600")
    if symbol not in {"NSXUSD", "XAUUSD"}:
        raise ValueError("unsupported HistData symbol")
    if timeframe not in {"M1", "T"}:
        raise ValueError("unsupported HistData timeframe")
    requested = datetime(year, month, 1, tzinfo=UTC)
    now = datetime.now(UTC)
    if requested >= now.replace(day=1, hour=0, minute=0, second=0, microsecond=0):
        raise ValueError("select a completed historical month")
    root = Path(raw_root)
    target = root / f"DAT_ASCII_{symbol}_{timeframe}_{year:04d}{month:02d}.zip"
    if target.exists():
        if target.stat().st_size > MAX_BYTES:
            raise ValueError("cached HistData archive exceeds the size limit")
        _validate_archive(target.read_bytes(), symbol, year, month, timeframe)
        return target
    layout = "1-minute-bar-quotes" if timeframe == "M1" else "tick-data-quotes"
    page = (
        f"{BASE}/download-free-forex-historical-data/"
        f"?/ascii/{layout}/{symbol.lower()}/{year}/{month}"
    )
    parser = _FormParser()
    parser.feed(_request(page).decode("utf-8"))
    if pause_seconds:
        time.sleep(pause_seconds)
    fields = parser.fields
    required = {
        "fxpair": symbol,
        "date": str(year),
        "datemonth": f"{year:04d}{month:02d}",
        "platform": "ASCII",
        "timeframe": timeframe,
    }
    if any(fields.get(key) != value for key, value in required.items()) or not fields.get("tk"):
        raise ValueError("HistData download form is missing or does not match the request")
    raw = _request(f"{BASE}/get.php", urllib.parse.urlencode(fields).encode(), page)
    _validate_archive(raw, symbol, year, month, timeframe)
    root.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=root, suffix=".tmp", delete=False) as output:
            temporary = Path(output.name)
            output.write(raw)
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target
