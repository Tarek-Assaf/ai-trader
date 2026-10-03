from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from trader.data.importers import histdata_download as hd


def _archive(symbol: str = "NSXUSD") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(f"DAT_ASCII_{symbol}_M1_202608.csv", "20260802 180000;100;101;99;100;0\n")
    return buffer.getvalue()


def _form(symbol: str = "NSXUSD") -> bytes:
    fields = {
        "tk": "test-form-token",
        "fxpair": symbol,
        "date": "2026",
        "datemonth": "202608",
        "platform": "ASCII",
        "timeframe": "M1",
    }
    inputs = "".join(f'<input name="{name}" value="{value}" />' for name, value in fields.items())
    return f'<form id="file_down" action="/get.php">{inputs}</form>'.encode()


def test_download_is_verified_atomic_and_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def request(url: str, data: bytes | None = None, referer: str | None = None) -> bytes:
        calls.append(url)
        if data is None:
            return _form()
        assert referer is not None
        assert b"fxpair=NSXUSD" in data
        return _archive()

    monkeypatch.setattr(hd, "_request", request)
    first = hd.download_histdata("NSXUSD", 2026, 8, tmp_path)
    assert hd.download_histdata("NSXUSD", 2026, 8, tmp_path) == first
    assert len(calls) == 2
    assert not list(tmp_path.glob("*.tmp"))
    assert zipfile.is_zipfile(first)


@pytest.mark.parametrize("body", [b"", b"service error", _archive("XAUUSD")])
def test_invalid_download_never_commits(
    body: bytes, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        hd, "_request", lambda url, data=None, referer=None: _form() if data is None else body
    )
    with pytest.raises(ValueError):
        hd.download_histdata("NSXUSD", 2026, 8, tmp_path)
    assert not list(tmp_path.iterdir())


def test_wrong_form_fails_before_post(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def request(url, data=None, referer=None):
        calls.append(url)
        return _form("XAUUSD")

    monkeypatch.setattr(hd, "_request", request)
    with pytest.raises(ValueError, match="does not match"):
        hd.download_histdata("NSXUSD", 2026, 8, tmp_path)
    assert len(calls) == 1


def test_invalid_month_or_symbol_makes_no_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unexpected(*args, **kwargs):
        pytest.fail("invalid inputs must not send a request")

    monkeypatch.setattr(hd, "_request", unexpected)
    for symbol, year, month in (("OTHER", 2026, 8), ("NSXUSD", 2099, 1), ("NSXUSD", 2026, 13)):
        with pytest.raises(ValueError):
            hd.download_histdata(symbol, year, month, tmp_path)


def test_archive_with_additional_price_members_is_rejected() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("DAT_ASCII_NSXUSD_M1_202608.csv", "price rows")
        archive.writestr("DAT_ASCII_XAUUSD_M1_202608.csv", "unrelated price rows")
    with pytest.raises(ValueError, match="unexpected members"):
        hd._validate_archive(buffer.getvalue(), "NSXUSD", 2026, 8)


def test_network_error_does_not_expose_response_or_token(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class BrokenOpener:
        def open(self, request, timeout):
            raise hd.urllib.error.URLError("private-token-value in remote error")

    monkeypatch.setattr(hd.urllib.request, "build_opener", lambda *args: BrokenOpener())
    with pytest.raises(ValueError) as error:
        hd._request(hd.BASE + "/get.php", data=b"tk=private-token-value")
    assert "private-token-value" not in str(error.value)
    assert "private-token-value" not in caplog.text


@pytest.mark.parametrize("status,body", [(503, b"error"), (200, b"oversized")])
def test_unexpected_http_status_and_oversized_response_are_rejected(
    status: int, body: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Response:
        def __init__(self) -> None:
            self.status = status

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self, limit: int) -> bytes:
            return body

    class Opener:
        def open(self, request, timeout):
            return Response()

    monkeypatch.setattr(hd.urllib.request, "build_opener", lambda *args: Opener())
    monkeypatch.setattr(hd, "MAX_BYTES", 4)
    with pytest.raises(ValueError):
        hd._request(hd.BASE + "/get.php")
