"""One alert, many accounts: copying an authenticated alert to the other accounts' bots."""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path

import pytest

import trader.api.tradingview as tv

BODY = json.dumps({"secret": "test_" + "x" * 30, "signal": "BUY"}).encode()
A = "http://127.0.0.1:8002/webhooks/tradingview"
B = "http://127.0.0.1:8003/webhooks/tradingview"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path: Path):
    monkeypatch.delenv(tv.FORWARD_URLS_ENV, raising=False)
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    monkeypatch.setattr(tv, "FORWARD_RETRY_SECONDS", 0.0)


def write_file(tmp_path: Path, text: str) -> None:
    (tmp_path / "state").mkdir(exist_ok=True)
    (tmp_path / "state" / tv.FORWARD_FILE_NAME).write_text(text, encoding="utf-8")


def test_no_targets_by_default() -> None:
    assert tv._forward_targets() == []


def test_targets_come_from_the_environment_and_the_file_without_duplicates(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv(tv.FORWARD_URLS_ENV, f"{A}, ,{B}")
    write_file(tmp_path, f"# account 03\n{B}\nhttp://127.0.0.1:8004/x  # inline note\n\n")
    assert tv._forward_targets() == [A, B, "http://127.0.0.1:8004/x"]


def test_the_file_is_read_on_every_alert_so_new_accounts_need_no_restart(tmp_path) -> None:
    assert tv._forward_targets() == []
    write_file(tmp_path, A + "\n")
    assert tv._forward_targets() == [A]
    write_file(tmp_path, A + "\n" + B + "\n")
    assert tv._forward_targets() == [A, B]
    write_file(tmp_path, "")
    assert tv._forward_targets() == []


@pytest.mark.parametrize("bad", ["ftp://x/y", "file:///etc/passwd", "javascript:alert(1)", "nope"])
def test_anything_that_is_not_an_http_address_is_skipped(monkeypatch, bad) -> None:
    monkeypatch.setenv(tv.FORWARD_URLS_ENV, f"{bad},{A}")
    assert tv._forward_targets() == [A]


def test_every_target_gets_the_same_body_and_one_failure_does_not_stop_the_rest(
    monkeypatch,
) -> None:
    seen: list[tuple[str, bytes]] = []

    def fake(url: str, body: bytes, timeout: float) -> int:
        seen.append((url, body))
        if url == A:
            raise urllib.error.URLError("down")
        return 200

    monkeypatch.setattr(tv, "_send_copy", fake)
    tv._forward([A, B], BODY, "BUY")
    assert {url for url, _ in seen} == {A, B}
    assert {body for _, body in seen} == {BODY}
    assert [url for url, _ in seen].count(B) == 1


def test_a_network_failure_is_retried_once_then_succeeds(monkeypatch) -> None:
    calls: list[str] = []

    def fake(url: str, body: bytes, timeout: float) -> int:
        calls.append(url)
        if len(calls) == 1:
            raise OSError("refused")
        return 200

    monkeypatch.setattr(tv, "_send_copy", fake)
    assert tv._forward_one(A, BODY) is True
    assert len(calls) == tv.FORWARD_ATTEMPTS == 2


def test_a_refusal_from_the_other_bot_is_not_retried(monkeypatch) -> None:
    calls: list[str] = []

    def fake(url: str, body: bytes, timeout: float) -> int:
        calls.append(url)
        return 403

    monkeypatch.setattr(tv, "_send_copy", fake)
    assert tv._forward_one(A, BODY) is False
    assert len(calls) == 1


def test_a_persistent_network_failure_gives_up_without_raising(monkeypatch) -> None:
    def fake(url: str, body: bytes, timeout: float) -> int:
        raise OSError("refused")

    monkeypatch.setattr(tv, "_send_copy", fake)
    assert tv._forward_one(A, BODY) is False
    tv._forward([A], BODY, "SELL")  # must not raise


def test_the_copy_marks_itself_so_it_is_never_copied_again(monkeypatch) -> None:
    captured = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["body"] = request.data
        captured["header"] = request.get_header(tv.FORWARD_HEADER.capitalize())
        captured["method"] = request.get_method()
        return Response()

    monkeypatch.setattr(tv.urllib.request, "urlopen", fake_urlopen)
    assert tv._send_copy(A, BODY, 1.0) == 200
    assert captured == {"url": A, "body": BODY, "header": "1", "method": "POST"}
