"""Account names on Telegram messages, per-account leader locks, and per-account status."""

from __future__ import annotations

import pytest

from trader.channels.telegram import TelegramBotTransport
from trader.core.accounts import current_account_id, scoped_lease_key
from trader.reports.account_status import other_account_lines


def capture(monkeypatch, transport: TelegramBotTransport) -> list[dict]:
    sent: list[dict] = []
    monkeypatch.setattr(transport, "_request", lambda method, payload: sent.append(dict(payload)))
    return sent


def test_messages_carry_the_account_name_only_when_one_is_set(monkeypatch) -> None:
    labelled = TelegramBotTransport("token", label="02 Demo B")
    plain = TelegramBotTransport("token")
    sent = capture(monkeypatch, labelled)
    labelled.send_message("42", "Trade opened")
    assert sent[0]["text"] == "[02 Demo B] Trade opened"
    sent = capture(monkeypatch, plain)
    plain.send_message("42", "Trade opened")
    assert sent[0]["text"] == "Trade opened"


def test_a_long_or_blank_label_is_bounded_or_ignored(monkeypatch) -> None:
    long = TelegramBotTransport("token", label="x" * 200)
    sent = capture(monkeypatch, long)
    long.send_message("42", "hi")
    assert sent[0]["text"].startswith("[" + "x" * 40 + "] ")
    blank = TelegramBotTransport("token", label="   ")
    sent = capture(monkeypatch, blank)
    blank.send_message("42", "hi")
    assert sent[0]["text"] == "hi"


def test_the_label_does_not_defeat_the_closed_market_warning_filter(monkeypatch) -> None:
    transport = TelegramBotTransport("token", label="02 Demo B")
    sent = capture(monkeypatch, transport)
    monkeypatch.setattr("trader.channels.telegram._closed_market_feed_warning", lambda text: True)
    transport.send_message("42", "Capital.com feed stalled since 10:00")
    assert sent == []


def test_account_one_keeps_its_original_lease_keys_and_others_get_their_own() -> None:
    assert scoped_lease_key("ai-trader:execution:lease", {}) == "ai-trader:execution:lease"
    assert (
        scoped_lease_key("ai-trader:execution:lease", {"ACCOUNT_ID": "acct-01"})
        == "ai-trader:execution:lease"
    )
    two = scoped_lease_key("ai-trader:execution:lease", {"ACCOUNT_ID": "acct-02"})
    three = scoped_lease_key("ai-trader:execution:lease", {"ACCOUNT_ID": "acct-03"})
    assert two == "ai-trader:execution:lease:acct-02"
    assert three == "ai-trader:execution:lease:acct-03"
    assert len({two, three, "ai-trader:execution:lease"}) == 3


@pytest.mark.parametrize("bad", ["acct-2", "02", "acct-02; rm", "ACCT-02", "acct-0002"])
def test_a_malformed_account_id_stops_start_up_instead_of_sharing_a_lease(bad) -> None:
    with pytest.raises(ValueError):
        current_account_id({"ACCOUNT_ID": bad})


def test_the_default_account_is_account_one() -> None:
    assert current_account_id({}) == "acct-01"
    assert current_account_id({"ACCOUNT_ID": "  "}) == "acct-01"


def test_extra_account_status_lines_never_fail_the_command() -> None:
    def engine_for(number: str):
        if number == "03":
            raise RuntimeError("database not ready")
        return None

    lines = other_account_lines(
        {"02": "Demo B", "03": "Demo C"}, engine_for, lambda number: lambda: f"mode-{number}"
    )
    text = "\n".join(lines)
    assert "Account 02 (Demo B):" in text and "Account 03 (Demo C):" in text
    assert "Control mode: mode-02" in text and "Control mode: mode-03" in text
    assert text.count("Entries: unknown") == 2
