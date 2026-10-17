"""Read-only, redacted Capital.com demo verification for the owner-operated RunPod pod."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from typing import Any

from trader.core.settings import EnvironmentSettings
from trader.integrations.capital_com import CapitalComClient, CapitalComError


def _market_summary(client: CapitalComClient, term: str) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for market in client.search_markets(term)[:5]:
        epic = market.get("epic")
        if not isinstance(epic, str) or not epic:
            continue
        details = client.market_details(epic)
        instrument = details.get("instrument", {})
        snapshot = details.get("snapshot", {})
        instrument = instrument if isinstance(instrument, Mapping) else {}
        snapshot = snapshot if isinstance(snapshot, Mapping) else {}
        results.append(
            {
                "epic": epic,
                "name": instrument.get("name"),
                "market_status": snapshot.get("marketStatus"),
            }
        )
    return results


def main() -> int:
    settings = EnvironmentSettings()
    if settings.broker_env != "demo" or settings.live_trading_enabled:
        print("Verification refused: set BROKER_ENV=demo and LIVE_TRADING_ENABLED=false.")
        return 2
    try:
        with CapitalComClient(settings) as client:
            client.ping()
            accounts = client.account_info().get("accounts", [])
            account_count = len(accounts) if isinstance(accounts, list) else 0
            nasdaq = _market_summary(client, "US100")
            gold = _market_summary(client, "GOLD")
    except (ValueError, CapitalComError) as exc:
        print(f"Capital.com demo verification failed: {exc}")
        return 1

    print(
        json.dumps(
            {
                "environment": "demo",
                "session": "ok",
                "account_count": account_count,
                "nasdaq_search": nasdaq,
                "gold_search": gold,
            },
            sort_keys=True,
        )
    )
    if not nasdaq or not gold:
        print("Search returned no candidate for one or both instruments; review with the owner.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
