from __future__ import annotations

from decimal import Decimal

from trader.core.accounts import describe_accounts, load_accounts

SECRETS = ("id-secret-aaa", "key-secret-bbb", "pw-secret-ccc", "id-secret-ddd")


def block(number: str, identifier: str = "id-secret-ddd", **extra: str) -> dict[str, str]:
    values = {
        f"CAPITAL_IDENTIFIER_{number}": identifier,
        f"CAPITAL_API_KEY_{number}": "key-secret-bbb",
        f"CAPITAL_API_PASSWORD_{number}": "pw-secret-ccc",
    }
    values.update({f"{name}_{number}": value for name, value in extra.items()})
    return values


def test_unnumbered_variables_are_account_01() -> None:
    load = load_accounts(
        {
            "CAPITAL_IDENTIFIER": "id-secret-aaa",
            "CAPITAL_API_KEY": "key-secret-bbb",
            "CAPITAL_API_PASSWORD": "pw-secret-ccc",
            "TAI_DEMO_EPICS": "us100, gold",
            "TAI_DEMO_BASE_LOT": "0.5",
            "TAI_DEMO_START_BALANCE": "10000",
        }
    )
    assert load.problems == ()
    (account,) = load.accounts
    assert account.number == "01"
    assert account.account_id == "acct-01"
    assert account.name == "Account 01"
    assert account.broker_env == "demo"
    assert account.live_enabled is False
    assert account.epics == ("US100", "GOLD")
    assert account.base_lot == Decimal("0.5")
    assert account.start_balance == Decimal("10000")


def test_numbered_accounts_load_in_number_order_and_fall_back_to_shared_values() -> None:
    environ = {
        "TAI_DEMO_EPICS": "US100,GOLD",
        "TAI_DEMO_BASE_LOT": "0.2",
        **block("05", ACCOUNT_NAME="Five"),
        **block("02", identifier="id-secret-aaa", TAI_DEMO_EPICS="GOLD", TAI_DEMO_BASE_LOT="1"),
    }
    load = load_accounts(environ)
    assert [a.number for a in load.accounts] == ["02", "05"]
    two, five = load.accounts
    assert two.epics == ("GOLD",)
    assert two.base_lot == Decimal("1")
    assert five.name == "Five"
    assert five.epics == ("US100", "GOLD")
    assert five.base_lot == Decimal("0.2")


def test_the_start_balance_is_one_accounts_own_and_never_falls_back() -> None:
    environ = {
        "CAPITAL_IDENTIFIER": "id-secret-aaa",
        "CAPITAL_API_KEY": "key-secret-bbb",
        "CAPITAL_API_PASSWORD": "pw-secret-ccc",
        "TAI_DEMO_START_BALANCE": "10999.80",
        **block("02", identifier="id-secret-ddd"),
        **block("03", identifier="id-secret-eee", TAI_DEMO_START_BALANCE="5000"),
    }
    one, two, three = load_accounts(environ).accounts
    assert one.start_balance == Decimal("10999.80")
    assert two.start_balance == Decimal("0")  # automatic: recorded from the account
    assert three.start_balance == Decimal("5000")


def test_credentials_never_fall_back_to_unnumbered_or_other_accounts() -> None:
    environ = {
        "CAPITAL_API_KEY": "key-secret-bbb",
        "CAPITAL_API_PASSWORD": "pw-secret-ccc",
        "CAPITAL_IDENTIFIER_02": "id-secret-ddd",
    }
    load = load_accounts(environ)
    assert load.accounts == ()
    names = {problem.variable for problem in load.problems}
    assert names == {"CAPITAL_API_KEY_02", "CAPITAL_API_PASSWORD_02"}


def test_an_incomplete_account_is_skipped_and_the_others_still_load() -> None:
    environ = {**block("01", identifier="id-secret-aaa"), "CAPITAL_IDENTIFIER_02": "id-secret-ddd"}
    load = load_accounts(environ)
    assert [a.number for a in load.accounts] == ["01"]
    assert [p.variable for p in load.problems] == [
        "CAPITAL_API_KEY_02",
        "CAPITAL_API_PASSWORD_02",
    ]


def test_unnumbered_and_01_credentials_together_are_refused() -> None:
    environ = {
        "CAPITAL_IDENTIFIER": "id-secret-aaa",
        "CAPITAL_API_KEY": "key-secret-bbb",
        "CAPITAL_API_PASSWORD": "pw-secret-ccc",
        **block("01", identifier="id-secret-ddd"),
        **block("02", identifier="id-secret-ddd"),
    }
    load = load_accounts(environ)
    assert [a.number for a in load.accounts] == ["02"]
    assert len(load.problems) == 1
    assert load.problems[0].number == "01"
    assert "keep only one set" in load.problems[0].reason


def test_the_same_login_twice_is_refused() -> None:
    environ = {**block("01", identifier="id-secret-aaa"), **block("02", identifier="ID-secret-AAA")}
    load = load_accounts(environ)
    assert [a.number for a in load.accounts] == ["01"]
    assert load.problems[0].number == "02"
    assert "same login as account 01" in load.problems[0].reason


def test_live_needs_both_switches() -> None:
    only_env = load_accounts(block("01", identifier="id-secret-aaa", BROKER_ENV="live"))
    assert only_env.accounts == ()
    assert only_env.problems[0].variable == "LIVE_ENABLED_01"
    both = load_accounts(
        block("01", identifier="id-secret-aaa", BROKER_ENV="live", LIVE_ENABLED="yes")
    )
    assert both.problems == ()
    assert both.accounts[0].broker_env == "live"
    assert both.accounts[0].live_enabled is True


def test_invalid_values_are_reported_by_variable_name() -> None:
    load = load_accounts(
        block(
            "03",
            identifier="id-secret-aaa",
            ACCOUNT_ENABLED="maybe",
            BROKER_ENV="paper",
            TAI_DEMO_BASE_LOT="0",
            TAI_DEMO_START_BALANCE="-5",
        )
    )
    assert load.accounts == ()
    assert {p.variable for p in load.problems} == {
        "ACCOUNT_ENABLED_03",
        "BROKER_ENV_03",
        "TAI_DEMO_BASE_LOT_03",
        "TAI_DEMO_START_BALANCE_03",
    }


def test_parked_accounts_load_but_are_not_active() -> None:
    load = load_accounts(
        {
            **block("01", identifier="id-secret-aaa"),
            **block("02", ACCOUNT_ENABLED="no"),
        }
    )
    assert [a.number for a in load.accounts] == ["01", "02"]
    assert [a.number for a in load.active] == ["01"]
    assert "02 Account 02: demo, shared markets, parked" in describe_accounts(load)


def test_no_secret_value_appears_in_repr_problems_or_the_listing() -> None:
    environ = {
        **block("01", identifier="id-secret-aaa"),
        **block("02", identifier="id-secret-aaa"),
        "CAPITAL_IDENTIFIER_03": "id-secret-ddd",
    }
    load = load_accounts(environ)
    text = repr(load) + str(load.problems) + describe_accounts(load)
    for secret in SECRETS:
        assert secret not in text
    assert set(load.accounts[0].secret_values()) == {
        "id-secret-aaa",
        "key-secret-bbb",
        "pw-secret-ccc",
    }


def test_lowercase_environment_names_are_read() -> None:
    load = load_accounts({key.lower(): value for key, value in block("04").items()})
    assert [a.number for a in load.accounts] == ["04"]


def test_nothing_configured_loads_nothing() -> None:
    load = load_accounts({})
    assert load.accounts == () and load.problems == ()
    assert "none loaded" in describe_accounts(load)
