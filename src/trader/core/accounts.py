"""Per-account settings read from numbered environment variables.

An account is one block of variables ending in ``_NN`` (for example
``CAPITAL_IDENTIFIER_02``). The unnumbered Capital.com variables used by the single-account
setup count as account ``01``. Credentials never fall back to another account's or to the
unnumbered ones; every other value falls back to the shared unnumbered variable.

Nothing here reads the process environment by itself, prints a secret, or builds a broker.
Problems name the missing or invalid variable, never its value.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import SecretStr

_CREDENTIALS = ("CAPITAL_IDENTIFIER", "CAPITAL_API_KEY", "CAPITAL_API_PASSWORD")
_PER_ACCOUNT = (
    *_CREDENTIALS,
    "ACCOUNT_NAME",
    "ACCOUNT_ENABLED",
    "BROKER_ENV",
    "LIVE_ENABLED",
    "TAI_DEMO_EPICS",
    "TAI_DEMO_BASE_LOT",
    "TAI_DEMO_START_BALANCE",
)
_NUMBERED = re.compile(r"^(" + "|".join(_PER_ACCOUNT) + r")_(\d{2,3})$")
_TRUE = frozenset({"yes", "true", "1", "on"})
_FALSE = frozenset({"no", "false", "0", "off"})
_DEFAULT_LOT = Decimal("0.1")
_LEGACY_NUMBER = "01"


@dataclass(frozen=True, slots=True)
class AccountSettings:
    """One trading account's settings. Secrets are ``SecretStr`` and never print."""

    number: str
    name: str
    identifier: SecretStr
    api_key: SecretStr
    api_password: SecretStr
    broker_env: Literal["demo", "live"]
    live_enabled: bool
    enabled: bool
    epics: tuple[str, ...]
    base_lot: Decimal
    start_balance: Decimal

    @property
    def account_id(self) -> str:
        """Stable key for per-account state (journal rows, pause latch, leases)."""
        return f"acct-{self.number}"

    def secret_values(self) -> tuple[str, ...]:
        """Credential values, for logger redaction only."""
        values = (
            self.identifier.get_secret_value(),
            self.api_key.get_secret_value(),
            self.api_password.get_secret_value(),
        )
        return tuple(value for value in values if value)


@dataclass(frozen=True, slots=True)
class AccountProblem:
    """Why an account block was skipped. Holds a variable name, never a value."""

    number: str
    variable: str | None
    reason: str

    def __str__(self) -> str:
        where = f" ({self.variable})" if self.variable else ""
        return f"account {self.number}{where}: {self.reason}"


@dataclass(frozen=True, slots=True)
class AccountLoad:
    """Accounts that loaded and the blocks that were skipped, in account-number order."""

    accounts: tuple[AccountSettings, ...]
    problems: tuple[AccountProblem, ...]

    @property
    def active(self) -> tuple[AccountSettings, ...]:
        return tuple(account for account in self.accounts if account.enabled)


def _text(environ: Mapping[str, str], name: str) -> str:
    return (environ.get(name) or "").strip()


def _numbers(environ: Mapping[str, str]) -> list[str]:
    found: set[str] = set()
    for key in environ:
        match = _NUMBERED.match(key.upper())
        if match is not None:
            found.add(match.group(2).zfill(2))
    if _text(environ, "CAPITAL_IDENTIFIER"):
        found.add(_LEGACY_NUMBER)
    return sorted(found, key=int)


def _flag(raw: str, default: bool) -> bool | None:
    if not raw:
        return default
    lowered = raw.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    return None


def _decimal(raw: str, default: Decimal) -> Decimal | None:
    if not raw:
        return default
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None
    return value if value.is_finite() else None


def _build(
    number: str, environ: Mapping[str, str], *, legacy: bool
) -> AccountSettings | list[AccountProblem]:
    def numbered(name: str) -> str:
        return _text(environ, f"{name}_{number}")

    def credential(name: str) -> str:
        return _text(environ, name) if legacy else numbered(name)

    def shared(name: str) -> str:
        return numbered(name) or _text(environ, name)

    problems: list[AccountProblem] = []
    values = {name: credential(name) for name in _CREDENTIALS}
    for name, value in values.items():
        if not value:
            variable = name if legacy else f"{name}_{number}"
            problems.append(AccountProblem(number, variable, "missing"))
    enabled = _flag(numbered("ACCOUNT_ENABLED"), True)
    if enabled is None:
        problems.append(AccountProblem(number, f"ACCOUNT_ENABLED_{number}", "must be yes or no"))
    live_enabled = _flag(numbered("LIVE_ENABLED"), False)
    if live_enabled is None:
        problems.append(AccountProblem(number, f"LIVE_ENABLED_{number}", "must be yes or no"))
    broker_env = (shared("BROKER_ENV") or "demo").lower()
    if broker_env not in {"demo", "live"}:
        problems.append(AccountProblem(number, f"BROKER_ENV_{number}", "must be demo or live"))
    elif broker_env == "live" and live_enabled is not True:
        problems.append(
            AccountProblem(
                number, f"LIVE_ENABLED_{number}", "a live account also needs LIVE_ENABLED=yes"
            )
        )
    base_lot = _decimal(shared("TAI_DEMO_BASE_LOT"), _DEFAULT_LOT)
    if base_lot is None or base_lot <= 0:
        problems.append(
            AccountProblem(number, f"TAI_DEMO_BASE_LOT_{number}", "must be a number above zero")
        )
    start_balance = _decimal(shared("TAI_DEMO_START_BALANCE"), Decimal("0"))
    if start_balance is None or start_balance < 0:
        problems.append(
            AccountProblem(
                number, f"TAI_DEMO_START_BALANCE_{number}", "must be a number of zero or more"
            )
        )
    if problems:
        return problems
    assert enabled is not None and live_enabled is not None
    assert base_lot is not None and start_balance is not None
    epics = tuple(
        dict.fromkeys(
            part.strip().upper() for part in shared("TAI_DEMO_EPICS").split(",") if part.strip()
        )
    )
    return AccountSettings(
        number=number,
        name=numbered("ACCOUNT_NAME") or f"Account {number}",
        identifier=SecretStr(values["CAPITAL_IDENTIFIER"]),
        api_key=SecretStr(values["CAPITAL_API_KEY"]),
        api_password=SecretStr(values["CAPITAL_API_PASSWORD"]),
        broker_env="live" if broker_env == "live" else "demo",
        live_enabled=live_enabled,
        enabled=enabled,
        epics=epics,
        base_lot=base_lot,
        start_balance=start_balance,
    )


def load_accounts(environ: Mapping[str, str]) -> AccountLoad:
    """Read every account block. One bad block never blocks the others."""
    environ = {key.upper(): value for key, value in environ.items()}
    accounts: list[AccountSettings] = []
    problems: list[AccountProblem] = []
    logins: dict[str, str] = {}
    legacy_set = bool(_text(environ, "CAPITAL_IDENTIFIER"))
    for number in _numbers(environ):
        legacy = legacy_set and number == _LEGACY_NUMBER
        if legacy and any(_text(environ, f"{name}_{number}") for name in _CREDENTIALS):
            problems.append(
                AccountProblem(
                    number,
                    None,
                    "both the unnumbered and the _01 Capital.com variables are set; "
                    "keep only one set",
                )
            )
            continue
        built = _build(number, environ, legacy=legacy)
        if isinstance(built, list):
            problems.extend(built)
            continue
        login = built.identifier.get_secret_value().lower()
        if login in logins:
            problems.append(
                AccountProblem(
                    number,
                    f"CAPITAL_IDENTIFIER_{number}",
                    f"same login as account {logins[login]}; one login would trade twice",
                )
            )
            continue
        logins[login] = number
        accounts.append(built)
    return AccountLoad(tuple(accounts), tuple(problems))


def describe_accounts(load: AccountLoad) -> str:
    """One line per account for the owner, with no credentials."""
    lines = ["Accounts:"]
    for account in load.accounts:
        state = "active" if account.enabled else "parked"
        markets = ",".join(account.epics) or "shared markets"
        lines.append(f"{account.number} {account.name}: {account.broker_env}, {markets}, {state}")
    if not load.accounts:
        lines.append("none loaded")
    for problem in load.problems:
        lines.append(f"Skipped {problem}")
    return "\n".join(lines)
