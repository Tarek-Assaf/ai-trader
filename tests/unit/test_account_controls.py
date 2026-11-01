"""Pause, kill and resume for each account separately or for all at once."""

from __future__ import annotations

from pathlib import Path

import pytest

from trader.channels.telegram import TelegramChannel
from trader.execution.control import (
    GLOBAL_CONTROL_KEY,
    AccountControls,
    ControlMode,
    control_key,
)

OWNER = "42"


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value

    def delete(self, key: str) -> None:
        self.values.pop(key, None)


def make_controls(tmp_path: Path, *accounts: str) -> tuple[AccountControls, FakeRedis]:
    redis = FakeRedis()
    ids = accounts or ("acct-01", "acct-02")
    return AccountControls(redis, ids, tmp_path / "control"), redis


def mode_of(controls: AccountControls, number: str) -> ControlMode:
    return controls.source_for(f"acct-{number}").read()


def test_pausing_one_account_leaves_the_other_running(tmp_path: Path) -> None:
    controls, _ = make_controls(tmp_path)
    controls.writer("02").set(ControlMode.PAUSE)
    assert mode_of(controls, "02") is ControlMode.PAUSE
    assert mode_of(controls, "01") is ControlMode.RUN


def test_killing_one_account_leaves_the_other_running(tmp_path: Path) -> None:
    controls, _ = make_controls(tmp_path)
    controls.writer("01").set(ControlMode.KILL)
    assert mode_of(controls, "01") is ControlMode.KILL
    assert mode_of(controls, "02") is ControlMode.RUN


def test_pause_and_kill_for_all_reach_every_account(tmp_path: Path) -> None:
    controls, _ = make_controls(tmp_path, "acct-01", "acct-02", "acct-03")
    controls.writer(None).set(ControlMode.PAUSE)
    assert {mode_of(controls, n) for n in ("01", "02", "03")} == {ControlMode.PAUSE}
    controls.writer(None).set(ControlMode.KILL)
    assert {mode_of(controls, n) for n in ("01", "02", "03")} == {ControlMode.KILL}


def test_resume_all_lifts_the_shared_pause_and_asks_each_account_to_resume(tmp_path) -> None:
    controls, redis = make_controls(tmp_path)
    controls.writer(None).set(ControlMode.PAUSE)
    controls.writer("02").set(ControlMode.PAUSE)
    controls.writer(None).set(ControlMode.RESUME)
    assert GLOBAL_CONTROL_KEY not in redis.values
    assert mode_of(controls, "01") is ControlMode.RESUME
    assert mode_of(controls, "02") is ControlMode.RESUME


def test_resume_all_never_clears_a_killed_account(tmp_path: Path) -> None:
    controls, _ = make_controls(tmp_path)
    controls.writer("02").set(ControlMode.KILL)
    controls.writer(None).set(ControlMode.RESUME)
    assert mode_of(controls, "02") is ControlMode.KILL
    assert mode_of(controls, "01") is ControlMode.RESUME


def test_a_flag_file_stops_every_account(tmp_path: Path) -> None:
    controls, _ = make_controls(tmp_path)
    (tmp_path / "control").mkdir()
    (tmp_path / "control" / "kill.flag").write_text("", encoding="utf-8")
    assert mode_of(controls, "01") is ControlMode.KILL
    assert mode_of(controls, "02") is ControlMode.KILL


def test_each_account_has_its_own_key_and_account_one_keeps_the_shared_key(tmp_path) -> None:
    assert control_key("acct-02") == f"{GLOBAL_CONTROL_KEY}:acct-02"
    with pytest.raises(ValueError):
        control_key("../../x")
    controls, redis = make_controls(tmp_path)
    controls.writer("02").set(ControlMode.PAUSE)
    assert redis.values == {control_key("acct-02"): "pause"}


def test_mode_for_all_is_the_most_restrictive_account(tmp_path: Path) -> None:
    controls, _ = make_controls(tmp_path)
    assert controls.mode(None) is ControlMode.RUN
    controls.writer("02").set(ControlMode.PAUSE)
    assert controls.mode(None) is ControlMode.PAUSE
    assert controls.mode("01") is ControlMode.RUN
    controls.writer("01").set(ControlMode.KILL)
    assert controls.mode(None) is ControlMode.KILL


# ---- Telegram ----------------------------------------------------------------------------


class Transport:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, object]] = []
        self.answers: list[str] = []

    def get_updates(self, offset, timeout):
        return []

    def send_message(self, chat_id, text, *, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))

    def send_photo(self, chat_id, photo, *, caption=""):
        pass

    def answer_callback(self, callback_id, text=""):
        self.answers.append(text)


def make_channel(tmp_path: Path, *accounts: str):
    controls, redis = make_controls(tmp_path, *accounts)
    transport = Transport()
    channel = TelegramChannel(
        transport,
        [OWNER],
        control_writer=controls.account_writer("acct-01"),
        current_mode=lambda: controls.mode(None),
        accounts=controls,
        authorize_resume=lambda: True,
    )
    return channel, controls, transport


def last_text(transport: Transport) -> str:
    return transport.sent[-1][1]


def test_pause_with_a_number_pauses_only_that_account(tmp_path: Path) -> None:
    channel, controls, transport = make_channel(tmp_path)
    channel.handle_message(OWNER, "/pause 02")
    assert mode_of(controls, "02") is ControlMode.PAUSE
    assert mode_of(controls, "01") is ControlMode.RUN
    assert "account 02" in last_text(transport)


def test_kill_with_a_number_kills_only_that_account(tmp_path: Path) -> None:
    channel, controls, _ = make_channel(tmp_path)
    channel.handle_message(OWNER, "/kill 1")
    assert mode_of(controls, "01") is ControlMode.KILL
    assert mode_of(controls, "02") is ControlMode.RUN


def test_pause_and_kill_without_a_number_apply_to_every_account(tmp_path: Path) -> None:
    channel, controls, _ = make_channel(tmp_path)
    channel.handle_message(OWNER, "/pause")
    assert {mode_of(controls, "01"), mode_of(controls, "02")} == {ControlMode.PAUSE}
    channel.handle_message(OWNER, "/kill")
    assert {mode_of(controls, "01"), mode_of(controls, "02")} == {ControlMode.KILL}


@pytest.mark.parametrize("text", ["/pause 09", "/kill banana", "/stop 99", "/pause acct-07"])
def test_an_unknown_account_is_refused_and_never_falls_back_to_all(tmp_path, text) -> None:
    channel, controls, transport = make_channel(tmp_path)
    channel.handle_message(OWNER, text)
    assert mode_of(controls, "01") is ControlMode.RUN
    assert mode_of(controls, "02") is ControlMode.RUN
    assert "no account" in last_text(transport)
    assert "01, 02" in last_text(transport)


def test_a_stranger_cannot_control_any_account(tmp_path: Path) -> None:
    channel, controls, transport = make_channel(tmp_path)
    channel.handle_message("999", "/kill 02")
    assert mode_of(controls, "02") is ControlMode.RUN
    assert transport.sent == []


def _confirm(channel, transport) -> None:
    markup = transport.sent[-1][2]
    data = markup["inline_keyboard"][0][0]["callback_data"]
    channel.handle_update(
        {
            "callback_query": {
                "id": "cb1",
                "data": data,
                "message": {"chat": {"id": int(OWNER)}},
            }
        }
    )


def test_resume_with_a_number_resumes_only_that_account(tmp_path: Path) -> None:
    channel, controls, transport = make_channel(tmp_path)
    channel.handle_message(OWNER, "/pause 02")
    channel.handle_message(OWNER, "/pause 01")
    channel.handle_message(OWNER, "/resume 02")
    assert "account 02" in last_text(transport)
    _confirm(channel, transport)
    assert mode_of(controls, "02") is ControlMode.RESUME
    assert mode_of(controls, "01") is ControlMode.PAUSE
    assert "account 02" in last_text(transport)


def test_resume_without_a_number_resumes_every_account(tmp_path: Path) -> None:
    channel, controls, transport = make_channel(tmp_path)
    channel.handle_message(OWNER, "/pause")
    channel.handle_message(OWNER, "/resume")
    _confirm(channel, transport)
    assert {mode_of(controls, "01"), mode_of(controls, "02")} == {ControlMode.RESUME}


def test_resume_of_a_killed_account_is_refused(tmp_path: Path) -> None:
    channel, controls, transport = make_channel(tmp_path)
    channel.handle_message(OWNER, "/kill 02")
    channel.handle_message(OWNER, "/resume 02")
    assert "kill is latched" in last_text(transport)
    assert mode_of(controls, "02") is ControlMode.KILL


def test_resume_of_one_account_is_refused_while_every_account_is_paused(tmp_path) -> None:
    channel, controls, transport = make_channel(tmp_path)
    channel.handle_message(OWNER, "/pause")
    channel.handle_message(OWNER, "/resume 02")
    assert "Every account is paused" in last_text(transport)
    assert mode_of(controls, "02") is ControlMode.PAUSE


def test_a_confirmation_for_one_account_cannot_resume_another(tmp_path: Path) -> None:
    channel, controls, transport = make_channel(tmp_path)
    channel.handle_message(OWNER, "/pause 01")
    channel.handle_message(OWNER, "/pause 02")
    channel.handle_message(OWNER, "/resume 02")
    markup = transport.sent[-1][2]
    data = markup["inline_keyboard"][0][0]["callback_data"]
    tampered = data.rsplit(":", 1)[0] + ":01"
    channel.handle_update(
        {
            "callback_query": {
                "id": "cb2",
                "data": tampered,
                "message": {"chat": {"id": int(OWNER)}},
            }
        }
    )
    assert mode_of(controls, "01") is ControlMode.PAUSE
    assert mode_of(controls, "02") is ControlMode.PAUSE
    assert "Expired or invalid" in transport.answers[-1]


def test_help_explains_the_account_number_only_with_several_accounts(tmp_path: Path) -> None:
    channel, _, transport = make_channel(tmp_path)
    channel.handle_message(OWNER, "/help")
    assert "/pause 02" in last_text(transport)
    single, _, single_transport = make_channel(tmp_path / "x", "acct-01")
    single.handle_message(OWNER, "/help")
    assert "/pause 02" not in last_text(single_transport)
