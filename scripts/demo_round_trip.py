"""One-shot, owner-operated Capital.com demo NAS100 order round trip.

This command can place one real demo-account order. It refuses unsafe environment
settings and leaves a durable one-shot marker on /workspace before submission.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import ROUND_FLOOR, Decimal
from pathlib import Path
from typing import Any

from trader.core.config import load_config_file
from trader.core.settings import EnvironmentSettings
from trader.data.instruments import InstrumentMapper
from trader.execution.brokers.base import Direction, OrderRequest, Position
from trader.execution.brokers.capital_com import CapitalComBroker
from trader.integrations.capital_com.client import CapitalComError
from trader.integrations.capital_com.trading import CapitalComTradingClient

WORKSPACE = Path("/workspace")
LOCK_PATH = WORKSPACE / ".capital_demo_round_trip.once"
POLL_SECONDS = 0.5
POLL_ATTEMPTS = 20
ACCOUNT_READ_ATTEMPTS = 3
ACCOUNT_READ_BACKOFF_SECONDS = (1.0, 2.0)


class ObservedTradingClient:
    """Delegate broker calls while retaining only safe trade-state observations."""

    def __init__(
        self,
        client: CapitalComTradingClient,
        *,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.client = client
        self.environment = client.environment
        self._sleep = sleep or time.sleep
        self.position_observations: list[dict[str, object]] = []
        self.references: dict[str, str] = {}
        self.deal_statuses: list[str] = []

    def search_markets(self, search_term: str) -> list[Mapping[str, Any]]:
        return self.client.search_markets(search_term)

    def market_details(self, epic: str) -> Mapping[str, Any]:
        return self.client.market_details(epic)

    def account_info(self) -> Mapping[str, object]:
        """Read accounts with bounded backoff; never retry order operations."""
        for attempt in range(ACCOUNT_READ_ATTEMPTS):
            try:
                return self.client.account_info()
            except CapitalComError:
                if attempt == ACCOUNT_READ_ATTEMPTS - 1:
                    raise
                self._sleep(ACCOUNT_READ_BACKOFF_SECONDS[attempt])
        raise AssertionError("unreachable")

    def get_positions(self) -> list[Mapping[str, Any]]:
        rows = self.client.get_positions()
        for row in rows:
            position = row.get("position")
            market = row.get("market")
            if not isinstance(position, Mapping) or not isinstance(market, Mapping):
                continue
            self.position_observations.append(
                {
                    "deal_id": position.get("dealId"),
                    "epic": market.get("epic"),
                    "direction": position.get("direction"),
                    "size": position.get("size"),
                    "level": position.get("level"),
                    "stop_level": position.get("stopLevel"),
                }
            )
        return rows

    def get_working_orders(self) -> list[Mapping[str, Any]]:
        return self.client.get_working_orders()

    def get_activity(self, start: datetime, end: datetime) -> list[Mapping[str, Any]]:
        return self.client.get_activity(start, end)

    def open_position(self, payload: Mapping[str, object]) -> Mapping[str, Any]:
        reply = self.client.open_position(payload)
        self._reference("open", reply)
        return reply

    def confirm_deal(self, deal_reference: str) -> Mapping[str, Any]:
        reply = self.client.confirm_deal(deal_reference)
        status = reply.get("dealStatus")
        if isinstance(status, str):
            self.deal_statuses.append(status.upper())
        return reply

    def modify_position(self, deal_id: str, payload: Mapping[str, object]) -> Mapping[str, Any]:
        reply = self.client.modify_position(deal_id, payload)
        self._reference("modify", reply)
        return reply

    def close_position(self, deal_id: str) -> Mapping[str, Any]:
        reply = self.client.close_position(deal_id)
        self._reference("close", reply)
        return reply

    def cancel_working_order(self, deal_id: str) -> Mapping[str, Any]:
        reply = self.client.cancel_working_order(deal_id)
        self._reference("cancel", reply)
        return reply

    def _reference(self, operation: str, reply: Mapping[str, Any]) -> None:
        reference = reply.get("dealReference")
        if isinstance(reference, str) and reference:
            self.references[operation] = reference


def _decimal(value: object, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"invalid_{field}")
    try:
        result = Decimal(str(value))
    except Exception:
        raise ValueError(f"invalid_{field}") from None
    if not result.is_finite() or result <= 0:
        raise ValueError(f"invalid_{field}")
    return result


def _safe_position(position: Position | None) -> dict[str, str | None] | None:
    if position is None:
        return None
    return {
        "deal_id": position.deal_id,
        "epic": position.epic,
        "direction": position.direction.value,
        "size": str(position.size),
        "entry_level": str(position.level),
        "stop_level": str(position.stop_level) if position.stop_level is not None else None,
    }


def _redact_account_response(response: Mapping[str, object]) -> dict[str, object]:
    """Keep balance fields while excluding account identifiers and credentials."""
    accounts = response.get("accounts")
    if not isinstance(accounts, list):
        raise ValueError("account_response_invalid")
    redacted: list[dict[str, object]] = []
    for account in accounts:
        if not isinstance(account, Mapping):
            continue
        balance = account.get("balance")
        if not isinstance(balance, Mapping):
            continue
        fields = ("balance", "profitLoss", "deposit", "available")
        observed_values = {
            field: str(balance[field])
            for field in fields
            if field in balance and balance[field] is not None
        }
        redacted.append(
            {
                "currency": account.get("currency"),
                "fields_present": sorted(observed_values),
                "observed_values": observed_values,
            }
        )
    return {"account_count": len(redacted), "accounts": redacted}


def _check_environment() -> EnvironmentSettings:
    required = {
        "BROKER_ENV": "demo",
        "TRADING_MODE": "paper",
        "LIVE_TRADING_ENABLED": "false",
    }
    for name, expected in required.items():
        if os.environ.get(name, "").strip().lower() != expected:
            raise ValueError("demo_paper_environment_required")
    settings = EnvironmentSettings()
    if settings.broker_env != "demo" or settings.trading_mode != "paper":
        raise ValueError("demo_paper_environment_required")
    if settings.live_trading_enabled:
        raise ValueError("live_trading_must_be_disabled")
    return settings


def _market_inputs(
    client: ObservedTradingClient, config_path: Path
) -> tuple[Decimal, Decimal, Decimal, Decimal, Decimal, str, Decimal, str, Decimal]:
    raw_config = load_config_file(config_path).model_dump()
    mapper = InstrumentMapper(raw_config, client)
    terms = mapper.get("NAS100")
    details = client.market_details(terms.capital_epic)
    rules = details.get("dealingRules")
    snapshot = details.get("snapshot")
    if not isinstance(rules, Mapping) or not isinstance(snapshot, Mapping):
        raise ValueError("market_rules_or_snapshot_missing")
    if snapshot.get("marketStatus") != "TRADEABLE":
        raise ValueError("nas100_market_not_tradeable")
    bid = _decimal(snapshot.get("bid"), "bid")
    ask = _decimal(snapshot.get("offer"), "ask")
    if ask < bid:
        raise ValueError("invalid_quote")
    min_distance_rule = rules.get("minNormalStopOrLimitDistance")
    if not isinstance(min_distance_rule, Mapping):
        min_distance_rule = rules.get("minStopOrProfitDistance")
    if not isinstance(min_distance_rule, Mapping):
        raise ValueError("minimum_stop_distance_missing")
    min_distance_unit = str(min_distance_rule.get("unit", "")).upper()
    min_distance_value = _decimal(min_distance_rule.get("value"), "minimum_stop_distance")
    if min_distance_unit == "POINTS":
        min_stop_distance = min_distance_value
    elif min_distance_unit == "PERCENTAGE":
        # Use the higher BUY quote as a conservative reference, then verify the
        # final stop against bid, the side that triggers a long position's stop.
        min_stop_distance = ask * min_distance_value / Decimal("100")
    else:
        raise ValueError("minimum_stop_distance_unit_unsupported")
    minimum_size = terms.min_size
    step = terms.size_step
    if minimum_size > terms.max_size or (minimum_size - terms.min_size) % step != 0:
        raise ValueError("minimum_size_not_valid_for_broker_increment")
    return (
        bid,
        ask,
        minimum_size,
        step,
        terms.tick_size,
        terms.capital_epic,
        min_stop_distance,
        min_distance_unit,
        min_distance_value,
    )


def _floor_to_tick(value: Decimal, tick: Decimal) -> Decimal:
    return (value / tick).to_integral_value(rounding=ROUND_FLOOR) * tick


def _stop_plan(
    bid: Decimal, ask: Decimal, tick: Decimal, minimum_distance: Decimal
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    spread = ask - bid
    margin = max(tick * 5, spread, minimum_distance * Decimal("0.10"))
    stop = _floor_to_tick(bid - minimum_distance - margin, tick)
    tightened_stop = stop + tick
    if stop <= 0 or bid - tightened_stop < minimum_distance:
        raise ValueError("safe_stop_cannot_be_placed")
    return spread, margin, stop, tightened_stop


def _flat(broker: CapitalComBroker) -> bool:
    return not broker.get_positions() and not broker.get_open_orders()


def _wait_for_flat(broker: CapitalComBroker) -> bool:
    for attempt in range(POLL_ATTEMPTS):
        if _flat(broker):
            return True
        if attempt + 1 < POLL_ATTEMPTS:
            time.sleep(POLL_SECONDS)
    return False


def _cleanup(broker: CapitalComBroker) -> dict[str, object]:
    attempts: list[dict[str, str]] = []
    try:
        positions = tuple(broker.get_positions())
    except Exception as exc:
        positions = ()
        attempts.append({"action": "read_positions", "result": type(exc).__name__})
    for position in positions:
        try:
            broker.close_position(position.deal_id)
            attempts.append({"action": "close_position", "result": "requested"})
        except Exception as exc:
            attempts.append({"action": "close_position", "result": type(exc).__name__})
    try:
        orders = tuple(broker.get_open_orders())
    except Exception as exc:
        orders = ()
        attempts.append({"action": "read_working_orders", "result": type(exc).__name__})
    for order in orders:
        order_data = order.get("workingOrderData")
        deal_id = order_data.get("dealId") if isinstance(order_data, Mapping) else None
        if not isinstance(deal_id, str) or not deal_id:
            attempts.append({"action": "cancel_working_order", "result": "missing_deal_id"})
            continue
        try:
            broker.cancel_working_order(deal_id)
            attempts.append({"action": "cancel_working_order", "result": "requested"})
        except Exception as exc:
            attempts.append({"action": "cancel_working_order", "result": type(exc).__name__})
    try:
        flat = _wait_for_flat(broker)
        remaining_positions = len(broker.get_positions())
        remaining_orders = len(broker.get_open_orders())
    except Exception as exc:
        flat = False
        remaining_positions = -1
        remaining_orders = -1
        attempts.append({"action": "verify_flat", "result": type(exc).__name__})
    return {
        "attempts": attempts,
        "flat_confirmed": flat,
        "positions_remaining": remaining_positions,
        "working_orders_remaining": remaining_orders,
    }


def _record_rounding(report: dict[str, object], client: ObservedTradingClient) -> None:
    """Capture broker size/stop rounding even when strict adapter checks abort and close."""
    epic = report.get("epic")
    requested_size = report.get("requested_size")
    stop_levels = report.get("stop_levels")
    if not isinstance(stop_levels, dict):
        return
    observed = next(
        (item for item in reversed(client.position_observations) if item.get("epic") == epic),
        None,
    )
    if observed is None:
        return
    report["broker_observed_size"] = observed.get("size")
    report["broker_observed_stop"] = observed.get("stop_level")
    stop_levels["open_observed"] = observed.get("stop_level")
    rounding = report.get("rounding")
    if not isinstance(rounding, dict):
        rounding = {}
        report["rounding"] = rounding
    try:
        if requested_size is not None and observed.get("size") is not None:
            rounding["size_delta"] = str(
                Decimal(str(observed["size"])) - Decimal(str(requested_size))
            )
        open_stop = stop_levels.get("open_requested")
        if open_stop is not None and observed.get("stop_level") is not None:
            rounding["open_stop_delta"] = str(
                Decimal(str(observed["stop_level"])) - Decimal(str(open_stop))
            )
        tight_stop = stop_levels.get("tightened_requested")
        if tight_stop is not None and observed.get("stop_level") is not None:
            rounding["tightened_stop_delta"] = str(
                Decimal(str(observed["stop_level"])) - Decimal(str(tight_stop))
            )
    except Exception:
        rounding["delta_calculation"] = "unavailable"


def _write_report(report: dict[str, object]) -> Path | None:
    try:
        WORKSPACE.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        path = WORKSPACE / f"demo_round_trip_{stamp}_{uuid.uuid4().hex[:8]}.json"
        report["report_path"] = str(path)
        path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2, sort_keys=True))
        return path
    except OSError:
        # Never print an exception string: file-system paths may contain local details.
        print(json.dumps({"result": "report_write_failed", "error": "OSError"}, sort_keys=True))
        return None


def run_round_trip(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preflight",
        action="store_true",
        help="read-only check; never reserves or submits an order",
    )
    args = parser.parse_args(argv)
    started = time.monotonic()
    report: dict[str, object] = {
        "schema_version": 1,
        "result": "aborted",
        "created_at": datetime.now(UTC).isoformat(),
        "environment": "demo",
        "mode": "paper",
        "live_trading_enabled": False,
        "symbol": "NAS100",
        "epic": None,
        "direction": "BUY",
        "requested_size": None,
        "broker_observations": [],
        "rounding": {},
        "stop_levels": {},
        "deal_references": {},
        "timings_ms": {},
        "cleanup": {"attempted": False},
        "account_observations": {},
        "error": None,
    }
    try:
        settings = _check_environment()
    except Exception:
        report["error"] = "demo_paper_environment_required"
        _write_report(report)
        return 2

    if not WORKSPACE.is_dir():
        report["error"] = "workspace_not_mounted"
        _write_report(report)
        return 2
    observed: ObservedTradingClient | None = None
    broker: CapitalComBroker | None = None
    trading_client: CapitalComTradingClient | None = None
    order_attempted = False
    current_step = "initialization"
    phase_started = time.monotonic()
    try:
        config_path = Path(__file__).resolve().parents[1] / "config" / "instruments.yaml"
        trading_client = CapitalComTradingClient(settings)
        observed = ObservedTradingClient(trading_client)
        broker = CapitalComBroker(observed, enable_demo_orders=True)
        account_observations = report["account_observations"]
        if not isinstance(account_observations, dict):
            raise AssertionError("account observations must be a mapping")
        current_step = "before_open_account_capture"
        account_observations["before_open"] = _redact_account_response(observed.account_info())
        current_step = "flat_account_check"
        if not _flat(broker):
            raise ValueError("account_must_have_no_open_positions_or_working_orders")
        current_step = "market_preflight"
        (
            bid,
            ask,
            size,
            size_step,
            tick,
            epic,
            minimum_stop_distance,
            stop_distance_unit,
            stop_distance_value,
        ) = _market_inputs(observed, config_path)
        spread, margin, stop, tightened_stop = _stop_plan(bid, ask, tick, minimum_stop_distance)
        report["epic"] = epic
        report["requested_size"] = str(size)
        report["requested_reference_price"] = str(ask)
        report["quote"] = {"bid": str(bid), "ask": str(ask), "spread": str(spread)}
        report["minimum_size_step"] = str(size_step)
        report["stop_levels"] = {
            "open_requested": str(stop),
            "tightened_requested": str(tightened_stop),
            "tick_size": str(tick),
            "minimum_stop_distance": str(minimum_stop_distance),
            "minimum_stop_distance_rule": {
                "value": str(stop_distance_value),
                "unit": stop_distance_unit,
                "reference_price": str(ask) if stop_distance_unit == "PERCENTAGE" else None,
            },
            "safety_margin": str(margin),
            "tightened_distance_from_bid": str(bid - tightened_stop),
        }
        report["timings_ms"]["preflight"] = round((time.monotonic() - phase_started) * 1000, 3)
        if args.preflight:
            report["result"] = "preflight"
            return 0

        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                os.write(fd, b"Capital.com demo round trip attempted; do not remove to rerun.\\n")
            finally:
                os.close(fd)
        except FileExistsError:
            raise ValueError("one_shot_already_used") from None
        except OSError as exc:
            raise ValueError(type(exc).__name__) from None

        request = OrderRequest(
            client_order_id=f"q029-{uuid.uuid4().hex}",
            epic=epic,
            direction=Direction.BUY,
            size=size,
            reference_price=ask,
            stop_level=stop,
        )
        order_attempted = True
        current_step = "open_order"
        open_started = time.monotonic()
        receipt = broker.place_order(request)
        report["deal_references"]["open"] = receipt.deal_reference
        report["opened_position"] = _safe_position(receipt.position)
        current_step = "during_open_account_capture"
        account_observations["during_open"] = _redact_account_response(observed.account_info())
        current_step = "during_open_position_capture"
        observed.get_positions()
        report["broker_observations"] = list(observed.position_observations)
        report["rounding"] = {
            "size_delta": str(receipt.position.size - size),
            "open_stop_delta": (
                str(receipt.position.stop_level - stop)
                if receipt.position.stop_level is not None
                else None
            ),
        }
        report["stop_levels"]["open_observed"] = (
            str(receipt.position.stop_level) if receipt.position.stop_level else None
        )
        if receipt.position.size != size or receipt.position.stop_level != stop:
            raise ValueError("broker_open_rounding_mismatch")
        report["timings_ms"]["open_and_stop_verify"] = round(
            (time.monotonic() - open_started) * 1000, 3
        )

        current_step = "modify_stop"
        tighten_started = time.monotonic()
        modify_reference = broker.modify_position(receipt.deal_id, stop_level=tightened_stop)
        report["deal_references"]["modify"] = modify_reference
        updated = next(
            (item for item in broker.get_positions() if item.deal_id == receipt.deal_id),
            None,
        )
        if updated is None or updated.stop_level != tightened_stop or updated.size != size:
            if updated is not None:
                report["rounding"]["size_delta_after_modify"] = str(updated.size - size)
                report["rounding"]["tightened_stop_delta"] = (
                    str(updated.stop_level - tightened_stop)
                    if updated.stop_level is not None
                    else None
                )
            raise ValueError("broker_tightened_stop_verification_failed")
        report["stop_levels"]["tightened_observed"] = str(updated.stop_level)
        report["rounding"]["size_delta_after_modify"] = str(updated.size - size)
        report["rounding"]["tightened_stop_delta"] = str(updated.stop_level - tightened_stop)
        report["timings_ms"]["tighten_and_verify"] = round(
            (time.monotonic() - tighten_started) * 1000, 3
        )

        current_step = "close_order"
        close_started = time.monotonic()
        report["deal_references"]["close"] = broker.close_position(receipt.deal_id)
        current_step = "after_close_account_capture"
        if not _wait_for_flat(broker):
            raise ValueError("account_not_flat_after_close")
        account_observations["after_close"] = _redact_account_response(observed.account_info())
        report["cleanup"] = {
            "attempted": False,
            "flat_confirmed": True,
            "positions_remaining": 0,
            "working_orders_remaining": 0,
        }
        report["timings_ms"]["close_and_flat_verify"] = round(
            (time.monotonic() - close_started) * 1000, 3
        )
        report["result"] = "success"
    except Exception as exc:
        message = str(exc)
        if isinstance(exc, CapitalComError):
            report["capital_com_error"] = {
                "call": (
                    f"GET /{exc.path}" if current_step.endswith("account_capture") else exc.path
                ),
                "http_status": exc.status_code,
                "error_code": exc.error_code,
                "step": current_step,
            }
        report["error"] = (
            message
            if message.startswith(
                ("invalid_", "nas100_", "minimum_", "safe_", "account_", "broker_", "one_shot_")
            )
            else type(exc).__name__
        )
        if order_attempted and broker is not None and observed is not None:
            report["cleanup"] = {"attempted": True, **_cleanup(broker)}
            report["broker_observations"] = list(observed.position_observations)
            cleanup = report["cleanup"]
            if (
                observed.deal_statuses
                and observed.deal_statuses[0] == "REJECTED"
                and isinstance(cleanup, dict)
                and cleanup.get("flat_confirmed") is True
                and cleanup.get("positions_remaining") == 0
                and cleanup.get("working_orders_remaining") == 0
            ):
                report["result"] = "rejected_flat"
                report["error"] = "open_order_rejected_flat_confirmed_owner_decides_retry"
            references = report["deal_references"]
            if isinstance(references, dict):
                references.update(
                    {f"{key}_observed": value for key, value in observed.references.items()}
                )
            _record_rounding(report, observed)
    finally:
        if trading_client is not None:
            trading_client.close()
        timings = report["timings_ms"]
        if isinstance(timings, dict):
            timings["total"] = round((time.monotonic() - started) * 1000, 3)
        _write_report(report)

    return 0 if report["result"] in {"success", "preflight"} else 1


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    sys.exit(run_round_trip())
