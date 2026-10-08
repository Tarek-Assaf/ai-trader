"""Read-only command data and report scheduling adapters.

The Telegram channel owns routing and safety controls; this module supplies
only deterministic, read-only values.  Every provider is injected so a paper
fixture can exercise the exact command path without a broker or an LLM.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol, cast

from trader.execution.brokers.base import Position
from trader.reports.core import ReportDocument, ReportPeriod, ReportSnapshot, run_report
from trader.viz import equity_curve

LOGGER = logging.getLogger(__name__)


class AccountReader(Protocol):
    def get_account(self) -> Mapping[str, object]: ...

    def get_positions(self) -> Sequence[Position]: ...


class CommandDataService:
    """Render owner commands from explicit, read-only providers.

    Missing providers never become invented zeros.  They produce a bounded
    unavailable response and log only the exception type, preserving the
    channel's ability to answer while the LLM or a dependency is down.
    """

    def __init__(
        self,
        broker: AccountReader | None = None,
        *,
        pnl_provider: Callable[[], object] | None = None,
        risk_provider: Callable[[], object] | None = None,
        report_provider: Callable[[ReportPeriod], object] | None = None,
        chart_provider: Callable[[str], bytes] | None = None,
        soak_provider: Callable[[], object] | None = None,
    ) -> None:
        self._broker = broker
        self._pnl_provider = pnl_provider
        self._risk_provider = risk_provider
        self._report_provider = report_provider
        self._chart_provider = chart_provider
        self._soak_provider = soak_provider

    def handlers(self) -> dict[str, Callable[[str], str]]:
        return {
            "/status": lambda _command: self.status(),
            "/positions": lambda _command: self.positions(),
            "/pnl": lambda _command: self.pnl(),
            "/risk": lambda _command: self.risk(),
            "/report": lambda command: self.report(command),
            "/soak": lambda _command: self.soak(),
        }

    def status(self) -> str:
        account = self._account()
        required = ("balance", "equity", "currency")
        if any(key not in account for key in required):
            return "Trading status is unavailable right now."
        available = f"; available {account['available']}" if "available" in account else ""
        return (
            f"Status: account {account['currency']}; balance {account['balance']}; "
            f"equity {account['equity']}{available}."
        )

    def positions(self) -> str:
        if self._broker is None:
            return "Positions are unavailable right now."
        try:
            values = tuple(self._broker.get_positions())
        except Exception as exc:
            self._log_provider_failure("positions", exc)
            return "Positions are unavailable right now."
        if not values:
            return "Positions: none."
        lines = ["Positions:"]
        for position in values:
            stop = position.stop_level if position.stop_level is not None else "missing"
            lines.append(
                f"- {position.epic} {position.direction.value} size {position.size} "
                f"entry {position.level} stop {stop}"
            )
        return "\n".join(lines)

    def pnl(self) -> str:
        if self._pnl_provider is not None:
            try:
                value = self._pnl_provider()
            except Exception as exc:
                self._log_provider_failure("pnl", exc)
                return "P&L is unavailable right now."
            rendered = self._render_value(value, "P&L")
            if rendered is not None:
                return rendered
        account = self._account()
        profit = account.get("profitLoss", account.get("profit"))
        if profit is None or "currency" not in account:
            return "P&L is unavailable right now."
        return f"P&L: {account['currency']} {profit}."

    def risk(self) -> str:
        if self._risk_provider is None:
            return "Risk status is unavailable right now."
        try:
            value = self._risk_provider()
        except Exception as exc:
            self._log_provider_failure("risk", exc)
            return "Risk status is unavailable right now."
        rendered = self._render_risk(value)
        return rendered or "Risk status is unavailable right now."

    def report(self, command: str = "/report") -> str:
        period = self._period(command)
        if self._report_provider is None:
            return "Report data is unavailable right now."
        try:
            document = self._report_provider(period)
        except Exception as exc:
            self._log_provider_failure("report", exc)
            return "Report data is unavailable right now."
        if document is None:
            return "Report data is unavailable right now."
        return document.plain_text if isinstance(document, ReportDocument) else str(document)

    def chart(self, _command: str, argument: str = "") -> bytes:
        if self._chart_provider is not None:
            return self._chart_provider(argument)
        if self._pnl_provider is None:
            raise RuntimeError("chart data is unavailable")
        value = self._pnl_provider()
        points = getattr(value, "equity_curve", None)
        if points is None:
            raise RuntimeError("chart data is unavailable")
        figure = equity_curve(
            ({"equity": point.equity, "at": point.timestamp.isoformat()} for point in points),
            title=argument or "Equity curve",
        )
        buffer = io.BytesIO()
        try:
            figure.savefig(buffer, format="png", dpi=120)
            return buffer.getvalue()
        finally:
            clear = getattr(figure, "clear", None)
            if callable(clear):
                clear()

    def soak(self) -> str:
        """Return the deterministic soak summary without invoking an LLM."""
        if self._soak_provider is None:
            return "Soak status is unavailable right now."
        try:
            value = self._soak_provider()
        except Exception as exc:
            self._log_provider_failure("soak", exc)
            return "Soak status is unavailable right now."
        summary = getattr(value, "summary_command", None)
        if callable(summary):
            rendered = summary()
            if isinstance(rendered, str) and rendered.strip():
                return rendered
        rendered = self._render_value(value, "Soak")
        return rendered or "Soak status is unavailable right now."

    def _account(self) -> Mapping[str, object]:
        if self._broker is None:
            return {}
        try:
            account = self._broker.get_account()
        except Exception as exc:
            self._log_provider_failure("account", exc)
            return {}
        return account if isinstance(account, Mapping) else {}

    @staticmethod
    def _period(command: str) -> ReportPeriod:
        value = command.strip().lower().rsplit(":", 1)[-1]
        if value in {"daily", "weekly", "monthly"}:
            return cast(ReportPeriod, value)
        return "daily"

    @staticmethod
    def _render_value(value: object, label: str) -> str | None:
        if isinstance(value, Decimal):
            return f"{label}: {value}."
        if isinstance(value, Mapping):
            safe = ", ".join(f"{key}={item}" for key, item in value.items())
            return f"{label}: {safe}."
        if isinstance(value, str) and value.strip():
            return f"{label}: {value}"
        return None

    @staticmethod
    def _render_risk(value: object) -> str | None:
        """Render configured risk limits as short, stable plain text."""
        if not isinstance(value, Mapping):
            return None
        limits = value.get("limits")
        if not isinstance(limits, Mapping):
            limits = value

        def scalar(*names: str) -> str:
            for name in names:
                item = limits.get(name)
                if isinstance(item, (str, int, float, Decimal)) and not isinstance(item, bool):
                    text = str(item).strip()
                    if text:
                        return text[:64]
            return "unavailable"

        mode = value.get("mode")
        mode_text = (
            str(mode).strip()[:32] if isinstance(mode, str) and mode.strip() else "unavailable"
        )
        return "\n".join(
            (
                "Risk:",
                f"mode: {mode_text}",
                f"daily_loss_limit: {scalar('max_daily_loss_pct')}%",
                f"weekly_loss_limit: {scalar('max_weekly_loss_pct')}%",
                f"drawdown_limit: {scalar('max_drawdown_pct')}%",
                f"risk_per_trade: {scalar('risk_per_trade_pct')}%",
                f"max_open_positions: {scalar('max_open_positions_total', 'max_open_positions')}",
                f"orders_per_hour: {scalar('max_orders_per_hour')}",
            )
        ).rstrip()

    @staticmethod
    def _log_provider_failure(name: str, exc: Exception) -> None:
        LOGGER.warning(
            "command data provider failed",
            extra={"provider": name, "error": type(exc).__name__},
        )


class ScheduledReportRunner:
    """Small scheduler adapter used by the runtime and deterministic fixtures."""

    def __init__(
        self,
        schedule: object,
        snapshot_provider: Callable[[ReportPeriod], ReportSnapshot],
        *,
        telegram: Any = None,
        email: Any = None,
        owner_chat_ids: Sequence[str] = (),
        usage_summary: Callable[[], Mapping[str, object]] | None = None,
    ) -> None:
        self.schedule = schedule
        self._snapshot_provider = snapshot_provider
        self._telegram = telegram
        self._email = email
        self._owner_chat_ids = tuple(owner_chat_ids)
        self._usage_summary = usage_summary

    def run(self, period: ReportPeriod) -> ReportDocument | None:
        return self._execute(period, deliver=True)

    def configure_delivery(
        self, *, telegram: object | None, email: object | None, owner_chat_ids: Sequence[str]
    ) -> None:
        self._telegram = telegram
        self._email = email
        self._owner_chat_ids = tuple(owner_chat_ids)

    def render(self, period: ReportPeriod) -> ReportDocument | None:
        """Render an interactive reply without sending a second Telegram message."""
        return self._execute(period, deliver=False)

    def _execute(self, period: ReportPeriod, *, deliver: bool) -> ReportDocument | None:
        def snapshot_provider(current_period: ReportPeriod) -> ReportSnapshot:
            snapshot = self._snapshot_provider(current_period)
            if self._usage_summary is None:
                return snapshot
            usage = self._usage_summary()
            raw_cost = usage.get("cost_usd")
            if raw_cost is None:
                return snapshot
            try:
                cost = Decimal(str(raw_cost))
            except (InvalidOperation, ValueError):
                return snapshot
            if not cost.is_finite() or cost < 0:
                return snapshot
            return replace(snapshot, llm_spend=cost)

        return run_report(
            period,
            snapshot_provider,
            self._telegram if deliver else None,
            self._email if deliver else None,
            self._owner_chat_ids if deliver else (),
        )


__all__ = ["CommandDataService", "ScheduledReportRunner"]
