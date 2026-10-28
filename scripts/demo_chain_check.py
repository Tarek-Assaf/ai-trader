"""One-shot, owner-operated check of the real Capital.com DEMO execution path.

Runs the exact broker stack the bot uses when EXECUTION_BROKER=capital_demo: account view,
BUY with stop and take-profit, break-even stop move, partial close (netting), full close.
Uses the smallest sizes and leaves the account flat. Stop the bot first: an open position it
did not place would make the order manager pause.

    python /app/scripts/demo_chain_check.py --confirm-demo-order
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import UTC, datetime
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Any

import yaml

from trader.core.settings import EnvironmentSettings
from trader.execution.brokers.base import Direction, OrderRequest
from trader.execution.brokers.capital_runtime import CapitalRuntimeBroker
from trader.execution.reconciliation import capital_account_field_map_from_config
from trader.experiments.tai_wiring import CapitalInstrumentRulesProvider
from trader.integrations.capital_com.trading import CapitalComTradingClient

EPIC = "US100"
REPORT_DIR = Path("/workspace")


def _quantize(value: Decimal, step: Decimal, rounding: str) -> Decimal:
    return (value / step).to_integral_value(rounding=rounding) * step


def _wait_for(read: Any, predicate: Any, attempts: int = 20, delay: float = 0.5) -> Any:
    last = None
    for _ in range(attempts):
        last = read()
        if predicate(last):
            return last
        time.sleep(delay)
    return last


def run(settings: EnvironmentSettings, client: Any | None = None) -> dict[str, Any]:
    report: dict[str, Any] = {
        "started_at": datetime.now(UTC).isoformat(),
        "epic": EPIC,
        "environment": settings.broker_env,
        "steps": [],
        "result": "fail",
    }
    steps: list[dict[str, Any]] = report["steps"]

    def step(name: str, **details: Any) -> None:
        steps.append({"step": name, **{k: str(v) for k, v in details.items()}})
        print(f"OK   {name} {details if details else ''}", flush=True)

    settings_yaml = Path(__file__).resolve().parents[1] / "config" / "settings.yaml"
    fields = capital_account_field_map_from_config(yaml.safe_load(settings_yaml.read_text()))
    client = client or CapitalComTradingClient(settings)
    rules_provider = CapitalInstrumentRulesProvider(client, EPIC)
    details = client.market_details(EPIC)
    lot_size = Decimal(str(details["instrument"]["lotSize"]))
    broker = CapitalRuntimeBroker(client, fields, contract_size=lambda _epic: lot_size)
    deal_id: str | None = None
    try:
        account = broker.get_account()
        step(
            "account",
            equity=account["equity"],
            available=account["available"],
            margin_used=account["margin_used"],
            currency=account["currency"],
        )
        if broker.get_positions() or broker.get_open_orders():
            raise RuntimeError("account is not flat: close positions and working orders first")
        step("flat_before")

        rules = rules_provider()
        if rules.bid is None or rules.ask is None or rules.tick_size is None:
            raise RuntimeError("market quote is unavailable (is the market open?)")
        if rules.min_stop_distance is None or rules.min_limit_distance is None:
            raise RuntimeError("broker stop/limit distance rules are unavailable")
        size = _quantize(rules.min_size * 2, rules.size_step, ROUND_CEILING)
        partial = rules.min_size
        distance = max(rules.min_stop_distance * 3, rules.min_limit_distance * 3, Decimal("10"))
        stop = _quantize(rules.ask - distance, rules.tick_size, ROUND_FLOOR)
        target = _quantize(rules.ask + distance * 2, rules.tick_size, ROUND_CEILING)
        step(
            "rules",
            bid=rules.bid,
            ask=rules.ask,
            tick=rules.tick_size,
            size=size,
            partial=partial,
            stop=stop,
            target=target,
        )

        order = OrderRequest(
            f"chk-{uuid.uuid4().hex[:12]}", EPIC, Direction.BUY, size, rules.ask, stop, target
        )
        receipt = broker.place_order(order)
        deal_id = receipt.deal_id
        position = receipt.position
        step(
            "open_with_stop_and_target",
            deal=deal_id,
            level=position.level,
            size=position.size,
            stop=position.stop_level,
            target=position.profit_level,
        )
        if position.profit_level != target:
            raise RuntimeError(
                f"take-profit not retained: asked {target}, got {position.profit_level}"
            )

        # Tighten by a quarter of the way: far enough from the market to be accepted.
        stop_only = _quantize(
            position.stop_level + (position.level - position.stop_level) / 4,
            rules.tick_size,
            ROUND_FLOOR,
        )
        broker.modify_position(deal_id, stop_level=stop_only)
        kept = [p for p in broker.get_positions() if p.deal_id == deal_id]
        step(
            "stop_only_modify",
            stop=stop_only,
            target_kept=bool(kept) and kept[0].profit_level == target,
        )
        new_stop = stop_only + rules.tick_size * 2
        broker.modify_position(deal_id, stop_level=new_stop, profit_level=target)
        step("stop_and_target_modify", stop=new_stop, target=target)

        broker.close_partial(deal_id, partial)
        left = _wait_for(
            lambda: [p for p in broker.get_positions() if p.deal_id == deal_id],
            lambda found: bool(found) and found[0].size == size - partial,
        )
        if not left or left[0].size != size - partial:
            raise RuntimeError("partial close did not leave the expected size")
        if left[0].stop_level != new_stop or left[0].profit_level != target:
            raise RuntimeError("stop or target changed during the partial close")
        step(
            "partial_close",
            remaining=left[0].size,
            stop=left[0].stop_level,
            target=left[0].profit_level,
        )

        broker.close_position(deal_id)
        flat = _wait_for(lambda: broker.get_positions(), lambda found: not found)
        if flat or broker.get_open_orders():
            raise RuntimeError("account is not flat after the full close")
        deal_id = None
        step("flat_after")
        account = broker.get_account()
        step("account_after", equity=account["equity"], available=account["available"])
        report["result"] = "pass"
    except Exception as exc:  # noqa: BLE001 - report, then clean up
        code = getattr(exc, "error_code", None)
        report["error"] = f"{type(exc).__name__}: {exc}" + (f" [{code}]" if code else "")
        print(f"FAIL {report['error']}", flush=True)
    finally:
        try:
            for leftover in broker.get_positions():
                if leftover.epic == EPIC:
                    broker.close_position(leftover.deal_id)
                    print(f"CLEANUP closed {leftover.deal_id}", flush=True)
            report["flat_at_end"] = not broker.get_positions()
        except Exception as exc:  # noqa: BLE001
            report["cleanup_error"] = f"{type(exc).__name__}: {exc}"
            print(
                f"CLEANUP FAILED {report['cleanup_error']} - close the position by hand", flush=True
            )
        client.close()
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-demo-order", action="store_true")
    args = parser.parse_args()
    settings = EnvironmentSettings()
    if settings.broker_env != "demo" or settings.live_trading_enabled:
        print("REFUSED: needs broker_env=demo and live trading disabled")
        return 2
    if not args.confirm_demo_order:
        print("REFUSED: this places real DEMO orders; pass --confirm-demo-order")
        return 2
    report = run(settings)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"demo_chain_check_{datetime.now(UTC):%Y%m%dT%H%M%SZ}.json"
    path.write_text(json.dumps(report, indent=2))
    print(f"\nRESULT: {report['result'].upper()}  (report: {path})")
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
