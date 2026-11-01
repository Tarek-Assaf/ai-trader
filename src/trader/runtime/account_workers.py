"""Extra accounts run as worker processes inside the same container.

Account 01 is the main process.  Each further active account (``CAPITAL_IDENTIFIER_02`` and so
on) gets its own API process on its own port, with its own broker login, database, state folder,
leader locks, pause/kill switch and lot sizing.  They share the container's PostgreSQL server,
Redis, market-data recorder and data lake, and they report through the one Telegram bot with the
account's name on each message.  One TradingView alert reaches all of them: the main process
copies it to the workers' loopback addresses listed in the forward file this module writes.

Credentials are never written to a file: the launcher script asks this module for the worker's
environment at start-up and keeps it in the process only.

``python -m trader.runtime.account_workers render``   writes the supervisor include and forward file
``python -m trader.runtime.account_workers env NN``   prints shell exports for worker NN
``python -m trader.runtime.account_workers database NN``   creates the worker's database if absent
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.parse import unquote, urlsplit, urlunsplit

from trader.core.accounts import (
    DEFAULT_ACCOUNT_ID,
    AccountSettings,
    load_accounts,
)

BASE_PORT = 8000
SUPERVISOR_INCLUDE = Path("/workspace/supervisord.accounts.conf")
FORWARD_FILE = Path("/workspace/state/forward_urls")
LAUNCHER = "/app/scripts/start_account_api.sh"
_NUMBER = re.compile(r"^\d{2,3}$")


def worker_port(number: str) -> int:
    """Loopback port of account ``number``'s API: 8002 for account 02."""
    if not _NUMBER.match(number):
        raise ValueError("account number must be two or three digits")
    return BASE_PORT + int(number)


def worker_accounts(environ: Mapping[str, str]) -> tuple[AccountSettings, ...]:
    """Active accounts that run as workers: every active account except the main one."""
    load = load_accounts(environ)
    return tuple(a for a in load.active if a.account_id != DEFAULT_ACCOUNT_ID)


def worker_database_name(number: str) -> str:
    if not _NUMBER.match(number):
        raise ValueError("account number must be two or three digits")
    return f"trader_{number}"


def _database_url(environ: Mapping[str, str], number: str) -> str:
    """The shared server's address with this worker's own database name."""
    base = environ.get("DATABASE_URL") or "postgresql+psycopg://trader@127.0.0.1:5432/trader"
    parts = urlsplit(base)
    return urlunsplit(parts._replace(path="/" + worker_database_name(number)))


def worker_environment(account: AccountSettings, environ: Mapping[str, str]) -> dict[str, str]:
    """What differs for this account's process.  Everything else is inherited unchanged."""
    number = account.number
    base_workspace = Path(environ.get("WORKSPACE_DIR") or "/workspace")
    env = {
        "ACCOUNT_ID": account.account_id,
        "ACCOUNT_LABEL": f"{number} {account.name}",
        "ACCOUNT_PORT": str(worker_port(number)),
        # Their own broker login.
        "CAPITAL_IDENTIFIER": account.identifier.get_secret_value(),
        "CAPITAL_API_KEY": account.api_key.get_secret_value(),
        "CAPITAL_API_PASSWORD": account.api_password.get_secret_value(),
        "BROKER_ENV": account.broker_env,
        # Their own sizing; zero start balance means "record it from this account".
        "TAI_DEMO_BASE_LOT": str(account.base_lot),
        "TAI_DEMO_START_BALANCE": str(account.start_balance),
        # Their own records and state; the market data is shared and read-only for them.
        "DATABASE_URL": _database_url(environ, number),
        "WORKSPACE_DIR": str(base_workspace / "accounts" / number),
        "DATA_LAKE_DIR": environ.get("DATA_LAKE_DIR") or str(base_workspace / "data" / "lake"),
        "TRADER_LOG_PATH": str(base_workspace / "logs" / f"trader_{number}.log"),
        # Only the main bot listens to Telegram; workers send through it.
        "TELEGRAM_POLLING": "off",
    }
    if account.epics:
        env["TAI_DEMO_EPICS"] = ",".join(account.epics)
    return env


def render_supervisor(accounts: Sequence[AccountSettings]) -> str:
    """The supervisor include: one API process per worker account, no credentials in it."""
    blocks = ["; Written at container start from the numbered account variables. Do not edit.\n"]
    for account in accounts:
        number = account.number
        blocks.append(
            f"""[program:account_{number}]
command={LAUNCHER} {number}
priority=35
autostart=true
autorestart=true
startsecs=5
startretries=30
stdout_logfile=/workspace/logs/account_{number}.log
stdout_logfile_maxbytes=0
stderr_logfile=/workspace/logs/account_{number}.err.log
stderr_logfile_maxbytes=0
"""
        )
    return "\n".join(blocks)


def render_forward_urls(accounts: Sequence[AccountSettings]) -> str:
    lines = ["# Written at container start. Each line is one worker account's alert address."]
    lines += [
        f"http://127.0.0.1:{worker_port(account.number)}/webhooks/tradingview"
        for account in accounts
    ]
    return "\n".join(lines) + "\n"


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def render(
    environ: Mapping[str, str],
    *,
    supervisor_path: Path = SUPERVISOR_INCLUDE,
    forward_path: Path = FORWARD_FILE,
) -> list[str]:
    """Write the supervisor include and forward file; return report lines with no secrets."""
    load = load_accounts(environ)
    accounts = tuple(a for a in load.active if a.account_id != DEFAULT_ACCOUNT_ID)
    _write_atomic(supervisor_path, render_supervisor(accounts))
    _write_atomic(forward_path, render_forward_urls(accounts))
    report = [
        f"worker account {a.number} ({a.name}) on port {worker_port(a.number)}" for a in accounts
    ]
    report += [f"skipped {problem}" for problem in load.problems]
    parked = [a for a in load.accounts if not a.enabled]
    report += [f"account {a.number} is parked (ACCOUNT_ENABLED_{a.number}=no)" for a in parked]
    if not accounts:
        report.append("no worker accounts: only account 01 runs")
    return report


def shell_exports(account: AccountSettings, environ: Mapping[str, str]) -> str:
    return "\n".join(
        f"export {name}={shlex.quote(value)}"
        for name, value in sorted(worker_environment(account, environ).items())
    )


def ensure_database(url: str) -> bool:
    """Create the worker's database when it does not exist yet; True when it was created."""
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import make_conninfo

    parts = urlsplit(url)
    name = parts.path.lstrip("/")
    if not re.match(r"^trader_\d{2,3}$", name):
        raise ValueError("refusing to create a database that is not a worker database")
    conninfo = make_conninfo(
        host=parts.hostname or "127.0.0.1",
        port=parts.port or 5432,
        user=unquote(parts.username or "trader"),
        password=unquote(parts.password) if parts.password else None,
        dbname="postgres",
    )
    with psycopg.connect(conninfo, autocommit=True) as conn:
        found = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,)).fetchone()
        if found:
            return False
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        return True


def _account_for(number: str, environ: Mapping[str, str]) -> AccountSettings:
    for account in worker_accounts(environ):
        if account.number == number:
            return account
    raise SystemExit(f"account {number} is not an active worker account")


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    source = os.environ if environ is None else environ
    parser = argparse.ArgumentParser(prog="account_workers")
    commands = parser.add_subparsers(dest="command", required=True)
    render_parser = commands.add_parser("render")
    render_parser.add_argument("--supervisor", type=Path, default=SUPERVISOR_INCLUDE)
    render_parser.add_argument("--forward", type=Path, default=FORWARD_FILE)
    for name in ("env", "database"):
        commands.add_parser(name).add_argument("number")
    args = parser.parse_args(argv)
    if args.command == "render":
        for line in render(source, supervisor_path=args.supervisor, forward_path=args.forward):
            print(line)
        return 0
    account = _account_for(args.number, source)
    if args.command == "env":
        print(shell_exports(account, source))
        return 0
    created = ensure_database(_database_url(source, account.number))
    print(f"database {worker_database_name(account.number)} {'created' if created else 'ready'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
