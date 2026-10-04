from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from trader.data.importers import histdata_download as hd


def _archive(symbol: str = "NSXUSD", period: str = "202608", timeframe: str = "M1") -> bytes:
    buffer = io.BytesIO()
    date = f"{period[:6]}02" if len(period) == 6 else f"{period}0102"
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            f"DAT_ASCII_{symbol}_{timeframe}_{period}.csv",
            f"{date} 180000;100;101;99;100;0\\n",
        )
    return buffer.getvalue()


def _form(symbol: str = "NSXUSD", year: int = 2026, month: str | None = "202608") -> bytes:
    fields = {
        "tk": "test-form-token",
        "fxpair": symbol,
        "date": str(year),
        "platform": "ASCII",
        "timeframe": "M1",
    }
    if month is not None:
        fields["datemonth"] = month
    inputs = "".join(f'<input name="{name}" value="{value}" />' for name, value in fields.items())
    return f'<form id="file_down" action="/get.php">{inputs}</form>'.encode()


def test_download_is_verified_atomic_and_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def request(
        url: str,
        data: bytes | None = None,
        referer: str | None = None,
        **kwargs: object,
    ) -> bytes:
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
        hd,
        "_request",
        lambda url, data=None, referer=None, **kwargs: _form() if data is None else body,
    )
    with pytest.raises(ValueError):
        hd.download_histdata("NSXUSD", 2026, 8, tmp_path)
    assert not list(tmp_path.iterdir())


def test_wrong_form_fails_before_post(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def request(url, data=None, referer=None, **kwargs):
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
    for symbol, year, month in (
        ("OTHER", 2026, 8),
        ("NSXUSD", 2099, 1),
        ("NSXUSD", 2026, 13),
    ):
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
        hd._request(hd.BASE + "/get.php", max_bytes=4)


def test_range_download_pauses_between_form_and_archive_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    pauses: list[float] = []

    def request(
        url: str,
        data: bytes | None = None,
        referer: str | None = None,
        **kwargs: object,
    ) -> bytes:
        calls.append(url)
        return _form() if data is None else _archive()

    monkeypatch.setattr(hd, "_request", request)
    monkeypatch.setattr(hd.time, "sleep", pauses.append)

    hd.download_histdata("NSXUSD", 2026, 8, tmp_path, pause_seconds=0.75)

    assert len(calls) == 2
    assert pauses == [0.75]


def test_invalid_pause_is_rejected_before_network_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        hd,
        "_request",
        lambda *args, **kwargs: pytest.fail("invalid pause must not request"),
    )

    with pytest.raises(ValueError, match="pause_seconds"):
        hd.download_histdata("NSXUSD", 2026, 8, tmp_path, pause_seconds=-1)


def test_period_parser_reads_year_and_month_routes_and_prefers_months() -> None:
    parser = hd._PeriodParser("NSXUSD")
    route = "/download-free-forex-historical-data/?/ascii/1-minute-bar-quotes/"
    links = [
        f'<a href="{route}nsxusd/2024">2024</a>',
        f'<a href="{route}nsxusd/2026/9">Sep</a>',
        f'<a href="{route}xauusd/2025">Other</a>',
        f'<a href="https://evil.example{route}nsxusd/2023">Bad host</a>',
    ]
    parser.feed("".join(links))
    assert parser.periods() == {2024: None, 2026: {9}}


def test_yearly_download_uses_year_route_and_yearly_size_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, bytes | None, int | None]] = []

    def request(
        url: str,
        data: bytes | None = None,
        referer: str | None = None,
        *,
        max_bytes=hd.MAX_BYTES,
    ) -> bytes:
        calls.append((url, data, max_bytes))
        return _form(year=2024, month=None) if data is None else _archive(period="2024")

    monkeypatch.setattr(hd, "_request", request)
    target = hd.download_histdata("NSXUSD", 2024, None, tmp_path)

    assert target.name == "DAT_ASCII_NSXUSD_M1_2024.zip"
    assert "/nsxusd/2024" in calls[0][0]
    assert "/nsxusd/2024/" not in calls[0][0]
    assert calls[1][2] == hd.MAX_YEARLY_M1_BYTES
    assert calls[1][1] is not None
    assert b"date=2024" in calls[1][1]
    assert b"datemonth" not in calls[1][1]


def test_yearly_form_rejects_unexpected_month_before_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def request(url, data=None, referer=None, **kwargs):
        calls.append(url)
        return _form(year=2024, month="202401")

    monkeypatch.setattr(hd, "_request", request)
    with pytest.raises(ValueError, match="unexpected month"):
        hd.download_histdata("NSXUSD", 2024, None, tmp_path)
    assert len(calls) == 1


def test_yearly_archive_rejects_expansion_over_yearly_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hd, "MAX_YEARLY_M1_EXPANDED_BYTES", 10)
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w") as archive:
        archive.writestr("DAT_ASCII_NSXUSD_M1_2024.csv", "x" * 11)
    with pytest.raises(ValueError, match="expanded size"):
        hd._validate_archive(raw.getvalue(), "NSXUSD", 2024, None)


def test_invalid_yearly_inputs_make_no_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hd, "_request", lambda *args, **kwargs: pytest.fail("must not request"))
    with pytest.raises(ValueError):
        hd.download_histdata("NSXUSD", 2026, None, tmp_path)
    with pytest.raises(ValueError):
        hd.download_histdata("NSXUSD", 2024, None, tmp_path, timeframe="T")
