"""Read-only commands for the extra accounts: one account by number, or all accounts together."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from trader.api import account_proxy as ap

WORKERS = {"02": "Second"}


def _local(command: str, argument: str) -> str:
    return f"main {command} [{argument}]"


def _render(
    command: str, argument: str, asks: list | None = None, answer: str | None = "ok"
) -> str:
    def ask(number: str, cmd: str, arg: str = "") -> str | None:
        if asks is not None:
            asks.append((number, cmd, arg))
        return None if answer is None else f"{answer} {number}"

    return ap.render_for_accounts(command, argument, local=_local, workers=lambda: WORKERS, ask=ask)


def test_without_extra_accounts_nothing_changes() -> None:
    text = ap.render_for_accounts("/pnl", "week", local=_local, workers=dict)
    assert text == "main /pnl [week]"


def test_a_number_asks_that_accounts_worker_only() -> None:
    asks: list = []
    assert _render("/pnl", "02", asks) == "ok 02"
    assert asks == [("02", "/pnl", "")]
    assert _render("/report", "2 daily", asks) == "ok 02"
    assert asks[-1] == ("02", "/report", "daily")


def test_account_01_by_number_is_the_main_account() -> None:
    asks: list = []
    assert _render("/pnl", "01 week", asks) == "main /pnl [week]"
    assert asks == []


def test_without_a_number_every_account_is_shown_with_its_name() -> None:
    text = _render("/positions", "")
    assert text.startswith("Account 01:\nmain /positions []")
    assert "Account 02 (Second):\nok 02" in text


def test_a_silent_worker_is_reported_and_never_hides_the_main_account() -> None:
    text = _render("/risk", "", answer=None)
    assert "main /risk" in text
    assert "Account 02 is not answering right now." in text
    assert _render("/pnl", "02", answer=None) == "Account 02 is not answering right now."


def test_an_unknown_number_is_not_treated_as_an_account() -> None:
    asks: list = []
    text = _render("/pnl", "07", asks)
    assert asks[0][0] == "02"  # shown as all accounts; "07" is passed along as an ordinary argument
    assert text.startswith("Account 01:")


def test_status_is_not_expanded_here_the_status_handler_adds_the_blocks() -> None:
    assert _render("/status", "") == "main /status []"


def test_only_read_only_commands_can_be_asked_of_a_worker() -> None:
    assert ap.ask_worker("02", "/kill") is None
    assert ap.ask_worker("02", "/resume") is None


def _client(handlers: dict | None = None) -> TestClient:
    app = FastAPI()
    app.include_router(ap.router)
    app.state.local_command_handlers = handlers or {"/pnl": lambda c: f"pnl for {c}"}
    return TestClient(app, client=("127.0.0.1", 50000))


def test_the_worker_route_answers_read_only_commands_from_localhost() -> None:
    response = _client().post("/internal/command", json={"command": "/pnl", "argument": "week"})
    assert response.json() == {"text": "pnl for /pnl week"}


def test_the_worker_route_refuses_control_commands_and_unknown_ones() -> None:
    client = _client({"/kill": lambda c: "killed", "/pnl": lambda c: "x"})
    assert client.post("/internal/command", json={"command": "/kill"}).status_code == 404
    assert client.post("/internal/command", json={"command": "/nope"}).status_code == 404


def test_the_worker_route_is_closed_to_tunnelled_and_remote_callers() -> None:
    tunnelled = _client().post(
        "/internal/command", json={"command": "/pnl"}, headers={"X-Forwarded-For": "1.2.3.4"}
    )
    assert tunnelled.status_code == 404
    app = FastAPI()
    app.include_router(ap.router)
    app.state.local_command_handlers = {"/pnl": lambda c: "x"}
    remote = TestClient(app, client=("10.0.0.5", 50000))
    assert remote.post("/internal/command", json={"command": "/pnl"}).status_code == 404


def test_a_failing_handler_is_a_bounded_503() -> None:
    def boom(command: str) -> str:
        raise RuntimeError("broker down")

    client = _client({"/pnl": boom})
    assert client.post("/internal/command", json={"command": "/pnl"}).status_code == 503


def test_status_blocks_use_the_workers_own_answer_and_fall_back_to_control(monkeypatch) -> None:
    import trader.api.app as app_module

    monkeypatch.setattr(app_module, "_extra_accounts", lambda _app: WORKERS)
    controls = SimpleNamespace(mode=lambda number: SimpleNamespace(value="run"))
    fake_app = SimpleNamespace(state=SimpleNamespace(account_controls=controls))
    monkeypatch.setattr(
        ap, "ask_worker", lambda number, command, argument="": "Status: account USD"
    )
    lines = app_module._worker_status_lines(fake_app)
    assert lines == ["", "Account 02 (Second):", "Status: account USD"]
