"""Pure report rendering and small delivery adapters.

Numbers are always supplied by :class:`trader.execution.pnl.PnlEngine` (or a
persisted equivalent). This module only formats those numbers and delivers the
result; it never queries a broker, executes trades or lets commentary change a
number.
"""

from __future__ import annotations

import html
import logging
import smtplib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from email.message import EmailMessage
from typing import Literal, Protocol

from trader.execution.pnl import PnlReport

LOGGER = logging.getLogger(__name__)
ReportPeriod = Literal["daily", "weekly", "monthly"]
_ZERO = Decimal("0")


class TelegramReportTransport(Protocol):
    """The tiny transport surface needed by report delivery."""

    def send_message(
        self, chat_id: str, text: str, *, reply_markup: Mapping[str, object] | None = None
    ) -> None: ...

    def send_photo(self, chat_id: str, photo: bytes, *, caption: str = "") -> None: ...


@dataclass(frozen=True, slots=True)
class ReportSnapshot:
    """Deterministic facts used to render one owner report."""

    period: ReportPeriod
    generated_at: datetime
    pnl: PnlReport
    trade_count: int = 0
    winning_trades: int = 0
    expectancy: Decimal = _ZERO
    strategy_pnl: Mapping[str, Decimal] = field(default_factory=dict)
    strategy_stage_changes: tuple[str, ...] = ()
    risk_events: tuple[str, ...] = ()
    llm_spend: Decimal = _ZERO
    llm_cap: Decimal | None = None
    issues: tuple[str, ...] = ()
    chart_images: tuple[bytes, ...] = ()

    def __post_init__(self) -> None:
        if self.period not in {"daily", "weekly", "monthly"}:
            raise ValueError("period must be daily, weekly or monthly")
        if self.generated_at.tzinfo is None or self.generated_at.utcoffset() is None:
            raise ValueError("generated_at must be timezone-aware")
        if self.trade_count < 0 or self.winning_trades < 0:
            raise ValueError("trade counts must be non-negative")
        if self.winning_trades > self.trade_count:
            raise ValueError("winning_trades cannot exceed trade_count")
        if not isinstance(self.expectancy, Decimal) or not self.expectancy.is_finite():
            raise ValueError("expectancy must be a finite Decimal")
        if (
            not isinstance(self.llm_spend, Decimal)
            or not self.llm_spend.is_finite()
            or self.llm_spend < 0
        ):
            raise ValueError("llm_spend must be a non-negative Decimal")
        if self.llm_cap is not None and (
            not isinstance(self.llm_cap, Decimal)
            or not self.llm_cap.is_finite()
            or self.llm_cap < 0
        ):
            raise ValueError("llm_cap must be a non-negative Decimal")
        if any(not isinstance(image, bytes) or not image for image in self.chart_images):
            raise ValueError("chart_images must contain non-empty bytes")


@dataclass(frozen=True, slots=True)
class ReportDocument:
    """Rendered report with plain text, HTML and optional chart PNGs."""

    period: ReportPeriod
    generated_at: datetime
    subject: str
    plain_text: str
    html: str
    chart_images: tuple[bytes, ...] = ()


def _money(value: Decimal, currency: str) -> str:
    return f"{currency} {value:,.2f}"


def _pct(value: Decimal) -> str:
    return f"{value:.2f}%"


def _section(title: str, values: Sequence[tuple[str, str]]) -> list[str]:
    lines = [title]
    lines.extend(f"- {label}: {value}" for label, value in values)
    return lines


def build_report(snapshot: ReportSnapshot, *, commentary: str = "") -> ReportDocument:
    """Render a snapshot without allowing commentary to alter its figures."""
    pnl = snapshot.pnl
    currency = pnl.currency
    win_rate = (
        Decimal(snapshot.winning_trades) / Decimal(snapshot.trade_count) * Decimal(100)
        if snapshot.trade_count
        else _ZERO
    )
    title = f"{snapshot.period.title()} trading report"
    headline = [
        f"{title} ({snapshot.generated_at.isoformat()})",
        *_section(
            "P&L",
            (
                ("Realized gross", _money(pnl.realized_gross, currency)),
                ("Realized net", _money(pnl.realized_net, currency)),
                ("Unrealized net", _money(pnl.unrealized_net, currency)),
                ("Fees", _money(pnl.fees, currency)),
                ("Financing", _money(pnl.financing, currency)),
            ),
        ),
        *_section(
            "Account and risk",
            (
                ("Balance", _money(pnl.balance, currency)),
                ("Equity", _money(pnl.equity, currency)),
                ("Drawdown", _money(pnl.drawdown, currency)),
                ("Drawdown percent", _pct(pnl.drawdown_pct)),
                ("Margin used", _money(pnl.margin_used, currency)),
                ("Margin usage", _pct(pnl.margin_usage_pct)),
                ("Effective leverage", f"{pnl.effective_leverage:.3f}x"),
                ("Total exposure", _money(pnl.total_exposure, currency)),
            ),
        ),
        *_section(
            "Trades",
            (
                ("Trades", str(snapshot.trade_count)),
                ("Win rate", _pct(win_rate)),
                ("Expectancy", _money(snapshot.expectancy, currency)),
            ),
        ),
    ]
    if pnl.exposure_by_epic:
        headline.extend(
            _section(
                "Exposure by instrument",
                tuple(
                    (epic, _money(value, currency)) for epic, value in pnl.exposure_by_epic.items()
                ),
            )
        )
    if snapshot.strategy_pnl:
        headline.extend(
            _section(
                "Strategy performance",
                tuple(
                    (name, _money(value, currency)) for name, value in snapshot.strategy_pnl.items()
                ),
            )
        )
    headline.extend(_section("LLM budget", (("Spend", _money(snapshot.llm_spend, currency)),)))
    if snapshot.llm_cap is not None:
        headline.append(f"- Cap: {_money(snapshot.llm_cap, currency)}")
    for section_title, values in (
        ("Strategy stage changes", snapshot.strategy_stage_changes),
        ("Risk events", snapshot.risk_events),
        ("Open issues", snapshot.issues),
    ):
        if values:
            headline.extend(
                _section(
                    section_title,
                    tuple((str(index), value) for index, value in enumerate(values, 1)),
                )
            )
    if commentary.strip():
        headline.extend(("Commentary", commentary.strip()))

    plain_text = "\n".join(headline) + "\n"
    escaped_lines = "\n".join(f"<p>{html.escape(line)}</p>" for line in headline)
    html_text = (
        '<!doctype html><html><head><meta charset="utf-8"><title>'
        + html.escape(title)
        + "</title></head><body>"
        + escaped_lines
        + "</body></html>\n"
    )
    return ReportDocument(
        period=snapshot.period,
        generated_at=snapshot.generated_at,
        subject=title,
        plain_text=plain_text,
        html=html_text,
        chart_images=snapshot.chart_images,
    )


def deliver_telegram(
    document: ReportDocument,
    transport: TelegramReportTransport,
    owner_chat_ids: Sequence[str],
) -> None:
    """Deliver a report summary and its chart images to the owner chats."""
    for chat_id in owner_chat_ids:
        transport.send_message(chat_id, document.plain_text)
        for index, image in enumerate(document.chart_images, 1):
            transport.send_photo(
                chat_id, image, caption=f"{document.period.title()} report chart {index}"
            )


class SmtpEmailSender:
    """SMTP delivery that is a logged no-op when email is not configured."""

    def __init__(
        self,
        host: str | None,
        port: int | None,
        user: str | None,
        password: str | None,
        recipient: str | None,
        *,
        smtp_factory: Callable[..., smtplib.SMTP] = smtplib.SMTP,
    ) -> None:
        self._host = host.strip() if isinstance(host, str) else None
        self._port = port or 587
        self._user = user
        self._password = password
        self._recipient = recipient.strip() if isinstance(recipient, str) else None
        self._smtp_factory = smtp_factory
        self._warned_missing = False

    def send(self, document: ReportDocument) -> bool:
        """Send one document; return false for a disabled or failed delivery."""
        if not self._host or not self._recipient:
            if not self._warned_missing:
                LOGGER.info("report email is not configured; skipping email delivery")
                self._warned_missing = True
            return False
        message = EmailMessage()
        message["Subject"] = document.subject
        message["From"] = self._user or self._recipient
        message["To"] = self._recipient
        message.set_content(document.plain_text)
        message.add_alternative(document.html, subtype="html")
        try:
            with self._smtp_factory(self._host, self._port, timeout=30) as smtp:
                if self._user and self._password:
                    smtp.starttls()
                    smtp.login(self._user, self._password)
                smtp.send_message(message)
        except Exception:
            LOGGER.exception("report email delivery failed")
            return False
        return True
