"""Extra accounts run as worker processes inside the one container."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from trader.runtime import account_workers as aw

BASE = {
    "CAPITAL_IDENTIFIER": "main-login-secret",
    "CAPITAL_API_KEY": "main-key-secret",
    "CAPITAL_API_PASSWORD": "main-pass-secret",
    "TAI_DEMO_START_BALANCE": "10999.80",
    "TAI_DEMO_EPICS": "US100,GOLD",
    "TAI_DEMO_BASE_LOT": "0.1",
    "DATABASE_URL": "postgresql+psycopg://trader@127.0.0.1:5432/trader",
    "TRADINGVIEW_WEBHOOK_SECRET": "tv-secret-" + "x" * 20,
    "TELEGRAM_BOT_TOKEN": "telegram-secret-token",
}


def account(number: str, **extra: str) -> dict[str, str]:
    values = {
        f"CAPITAL_IDENTIFIER_{number}": f"login-{number}-secret",
        f"CAPITAL_API_KEY_{number}": f"key-{number}-secret",
        f"CAPITAL_API_PASSWORD_{number}": f"pass-{number}-secret",
    }
    values.update({f"{name}_{number}": value for name, value in extra.items()})
    return values


SECRETS = [
    "main-login-secret",
    "main-key-secret",
    "main-pass-secret",
    "login-02-secret",
    "key-02-secret",
    "pass-02-secret",
    "login-03-secret",
    "key-03-secret",
    "pass-03-secret",
    "telegram-secret-token",
]


def environ(*accounts: dict[str, str]) -> dict[str, str]:
    merged = dict(BASE)
    for item in accounts:
        merged.update(item)
    return merged


def test_account_one_is_the_main_process_and_never_a_worker() -> None:
    assert aw.worker_accounts(environ()) == ()
    two = aw.worker_accounts(environ(account("02"), account("03")))
    assert [a.number for a in two] == ["02", "03"]


def test_parked_and_broken_accounts_do_not_start_a_worker() -> None:
    parked = account("02", ACCOUNT_ENABLED="no")
    broken = {"CAPITAL_IDENTIFIER_03": "login-03-secret"}  # key and password missing
    assert aw.worker_accounts(environ(parked, broken)) == ()


def test_each_worker_gets_its_own_loopback_port() -> None:
    assert aw.worker_port("02") == 8002
    assert aw.worker_port("12") == 8012
    with pytest.raises(ValueError):
        aw.worker_port("2")
    with pytest.raises(ValueError):
        aw.worker_port("../1")


def test_the_worker_environment_isolates_everything_that_must_not_be_shared() -> None:
    env = environ(account("02", ACCOUNT_NAME="Demo B", TAI_DEMO_BASE_LOT="0.5"))
    (worker,) = aw.worker_accounts(env)
    out = aw.worker_environment(worker, env)
    assert out["ACCOUNT_ID"] == "acct-02"
    assert out["ACCOUNT_LABEL"] == "02 Demo B"
    assert out["ACCOUNT_PORT"] == "8002"
    # Its own login, never the main account's.
    assert out["CAPITAL_IDENTIFIER"] == "login-02-secret"
    assert out["CAPITAL_API_KEY"] == "key-02-secret"
    assert out["CAPITAL_API_PASSWORD"] == "pass-02-secret"
    # Its own sizing: the main account's typed start balance must never leak across.
    assert out["TAI_DEMO_START_BALANCE"] == "0"
    assert out["TAI_DEMO_BASE_LOT"] == "0.5"
    # Its own records and state, but the shared market data.
    assert out["DATABASE_URL"] == "postgresql+psycopg://trader@127.0.0.1:5432/trader_02"
    assert out["WORKSPACE_DIR"] == "/workspace/accounts/02"
    assert out["DATA_LAKE_DIR"] == "/workspace/data/lake"
    assert out["TRADER_LOG_PATH"] == "/workspace/logs/trader_02.log"
    # It never competes with the main bot for Telegram commands.
    assert out["TELEGRAM_POLLING"] == "off"


def test_shared_markets_are_inherited_and_an_accounts_own_markets_win() -> None:
    env = environ(account("02"), account("03", TAI_DEMO_EPICS="GOLD"))
    two, three = aw.worker_accounts(env)
    assert aw.worker_environment(two, env)["TAI_DEMO_EPICS"] == "US100,GOLD"
    assert aw.worker_environment(three, env)["TAI_DEMO_EPICS"] == "GOLD"


def test_an_explicit_start_balance_for_a_worker_is_honoured() -> None:
    env = environ(account("02", TAI_DEMO_START_BALANCE="5000"))
    (worker,) = aw.worker_accounts(env)
    assert aw.worker_environment(worker, env)["TAI_DEMO_START_BALANCE"] == "5000"


def test_the_worker_database_keeps_the_server_login_and_changes_only_the_name() -> None:
    env = environ(account("02"))
    env["DATABASE_URL"] = (
        "postgresql+psycopg://trader:p%40ss@db.internal:5433/trader?sslmode=disable"
    )
    (worker,) = aw.worker_accounts(env)
    assert aw.worker_environment(worker, env)["DATABASE_URL"] == (
        "postgresql+psycopg://trader:p%40ss@db.internal:5433/trader_02?sslmode=disable"
    )


def test_the_supervisor_file_names_each_worker_and_holds_no_credentials(tmp_path: Path) -> None:
    env = environ(account("02"), account("03"))
    report = aw.render(
        env, supervisor_path=tmp_path / "sup.conf", forward_path=tmp_path / "state" / "fwd"
    )
    text = (tmp_path / "sup.conf").read_text(encoding="utf-8")
    assert "[program:account_02]" in text and "[program:account_03]" in text
    assert f"command={aw.LAUNCHER} 02" in text and f"command={aw.LAUNCHER} 03" in text
    assert "[program:account_01]" not in text
    for secret in SECRETS:
        assert secret not in text
        assert secret not in (tmp_path / "state" / "fwd").read_text(encoding="utf-8")
        assert all(secret not in line for line in report)


def test_the_forward_file_lists_every_worker_and_nothing_else(tmp_path: Path) -> None:
    env = environ(account("02"), account("03"))
    aw.render(env, supervisor_path=tmp_path / "sup.conf", forward_path=tmp_path / "fwd")
    lines = [
        line
        for line in (tmp_path / "fwd").read_text(encoding="utf-8").splitlines()
        if not line.startswith("#")
    ]
    assert lines == [
        "http://127.0.0.1:8002/webhooks/tradingview",
        "http://127.0.0.1:8003/webhooks/tradingview",
    ]


def test_removing_an_account_removes_its_worker_and_its_forward_line(tmp_path: Path) -> None:
    sup, fwd = tmp_path / "sup.conf", tmp_path / "fwd"
    aw.render(environ(account("02")), supervisor_path=sup, forward_path=fwd)
    assert "account_02" in sup.read_text(encoding="utf-8")
    aw.render(environ(), supervisor_path=sup, forward_path=fwd)
    assert "account_02" not in sup.read_text(encoding="utf-8")
    assert "8002" not in fwd.read_text(encoding="utf-8")


def test_a_broken_account_block_is_reported_by_variable_name_and_does_not_block_others(
    tmp_path: Path,
) -> None:
    broken = {"CAPITAL_IDENTIFIER_03": "login-03-secret", "CAPITAL_API_KEY_03": "key-03-secret"}
    report = aw.render(
        environ(account("02"), broken),
        supervisor_path=tmp_path / "sup.conf",
        forward_path=tmp_path / "fwd",
    )
    assert any("worker account 02" in line for line in report)
    assert any("CAPITAL_API_PASSWORD_03" in line and "missing" in line for line in report)
    assert "account_03" not in (tmp_path / "sup.conf").read_text(encoding="utf-8")
    assert all("key-03-secret" not in line for line in report)


def test_with_no_extra_accounts_the_supervisor_file_is_empty_of_programs(tmp_path: Path) -> None:
    report = aw.render(environ(), supervisor_path=tmp_path / "s", forward_path=tmp_path / "f")
    assert "[program:" not in (tmp_path / "s").read_text(encoding="utf-8")
    assert report == ["no worker accounts: only account 01 runs"]


def test_shell_exports_cannot_run_code_whatever_a_value_contains() -> None:
    env = environ(account("02", ACCOUNT_NAME="x'; touch /tmp/pwned; echo '$(id)`id`"))
    (worker,) = aw.worker_accounts(env)
    exports = aw.shell_exports(worker, env)
    script = exports + '\nprintf "%s" "$ACCOUNT_LABEL"'
    result = subprocess.run(["sh", "-c", script], capture_output=True, text=True, check=True)
    assert result.stdout == "02 x'; touch /tmp/pwned; echo '$(id)`id`"
    assert not Path("/tmp/pwned").exists()


def test_the_env_command_refuses_an_account_that_is_not_an_active_worker() -> None:
    with pytest.raises(SystemExit):
        aw.main(["env", "07"], environ())
    with pytest.raises(SystemExit):
        aw.main(["env", "01"], environ())  # account 01 is never started as a worker


def test_only_worker_databases_can_be_created() -> None:
    with pytest.raises(ValueError):
        aw.ensure_database("postgresql+psycopg://trader@127.0.0.1:5432/trader")
    with pytest.raises(ValueError):
        aw.ensure_database("postgresql+psycopg://trader@127.0.0.1:5432/postgres")
    with pytest.raises(ValueError):
        aw.ensure_database('postgresql+psycopg://trader@127.0.0.1:5432/trader_02"; DROP')


def test_render_writes_atomically_and_leaves_no_temporary_files(tmp_path: Path) -> None:
    aw.render(environ(account("02")), supervisor_path=tmp_path / "s", forward_path=tmp_path / "f")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["f", "s"]
