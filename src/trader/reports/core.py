"""Pure report rendering and small delivery adapters.

Numbers are always supplied by :class:`trader.execution.pnl.PnlEngine` (or a
persisted equivalent). This module only formats those numbers and delivers the
result; it never queries a broker, executes trades or lets commentary change a
number.
"""

from __future__ import annotations

import hashlib
import html
import logging
import os
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


class EmailReportSender(Protocol):
    def send(self, document: ReportDocument) -> bool: ...


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
    instrument_pnl: Mapping[str, Decimal] = field(default_factory=dict)
    best_trade: str | None = None
    worst_trade: str | None = None
    strategy_stage_changes: tuple[str, ...] = ()
    risk_events: tuple[str, ...] = ()
    data_health: str = "unknown"
    backup_health: str = "unknown"
    llm_spend: Decimal = _ZERO
    llm_cap: Decimal | None = None
    llm_currency: str = "USD"
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
        if not isinstance(self.llm_currency, str) or not self.llm_currency.strip():
            raise ValueError("llm_currency must be non-empty")
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
    if snapshot.instrument_pnl:
        headline.extend(
            _section(
                "P&L by instrument",
                tuple(
                    (name, _money(value, currency))
                    for name, value in snapshot.instrument_pnl.items()
                ),
            )
        )
    for label, value in (
        ("Best trade", snapshot.best_trade),
        ("Worst trade", snapshot.worst_trade),
    ):
        if value is not None:
            headline.append(f"- {label}: {value}")
    headline.extend(
        _section(
            "Health",
            (("Data", snapshot.data_health), ("Backups", snapshot.backup_health)),
        )
    )
    headline.extend(
        _section(
            "LLM budget",
            (("Spend", _money(snapshot.llm_spend, snapshot.llm_currency)),),
        )
    )
    if snapshot.llm_cap is not None:
        headline.append(f"- Cap: {_money(snapshot.llm_cap, snapshot.llm_currency)}")
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
) -> int:
    """Deliver a report summary and charts, returning the number of failures."""
    failures = 0
    for chat_id in owner_chat_ids:
        chat_hash = hashlib.sha256(chat_id.encode("utf-8")).hexdigest()[:16]
        try:
            transport.send_message(chat_id, document.plain_text)
        except Exception:
            failures += 1
            LOGGER.warning(
                "report Telegram summary delivery failed", extra={"chat_hash": chat_hash}
            )
        for index, image in enumerate(document.chart_images, 1):
            try:
                transport.send_photo(
                    chat_id, image, caption=f"{document.period.title()} report chart {index}"
                )
            except Exception:
                failures += 1
                LOGGER.warning(
                    "report Telegram chart delivery failed",
                    extra={"chat_hash": chat_hash, "chart_index": index},
                )
    return failures


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
        smtp_ssl_factory: Callable[..., smtplib.SMTP_SSL] = smtplib.SMTP_SSL,
    ) -> None:
        self._host = host.strip() if isinstance(host, str) else None
        self._port = port if port is not None else 0
        self._user = user
        self._password = password
        self._recipient = recipient.strip() if isinstance(recipient, str) else None
        self._smtp_factory = smtp_factory
        self._smtp_ssl_factory = smtp_ssl_factory
        self._warned_missing = False

    @property
    def enabled(self) -> bool:
        """Whether the minimum settings needed for email delivery are present."""
        return bool(self._host and self._port and self._recipient)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> SmtpEmailSender:
        """Build from environment names without raising on malformed settings."""
        values = os.environ if environ is None else environ
        raw_port = values.get("SMTP_PORT", "587").strip()
        try:
            port = int(raw_port)
            if port <= 0 or port > 65535:
                raise ValueError
        except (TypeError, ValueError):
            LOGGER.info("report email SMTP_PORT is invalid; skipping email delivery")
            port = None
        return cls(
            values.get("SMTP_HOST"),
            port,
            values.get("SMTP_USER"),
            values.get("SMTP_PASSWORD"),
            values.get("REPORT_EMAIL_TO"),
        )

    def send(self, document: ReportDocument) -> bool:
        """Send one document; return false for a disabled or failed delivery."""
        if not self.enabled:
            if not self._warned_missing:
                LOGGER.warning("report email is not configured; using Telegram only")
                self._warned_missing = True
            return False
        message = EmailMessage()
        message["Subject"] = document.subject
        message["From"] = self._user or self._recipient
        message["To"] = self._recipient
        message.set_content(document.plain_text)
        message.add_alternative(document.html, subtype="html")
        stage = "SMTP connection"
        try:
            factory = self._smtp_ssl_factory if self._port == 465 else self._smtp_factory
            with factory(self._host, self._port, timeout=30) as smtp:
                if self._user and self._password:
                    if self._port != 465:
                        stage = "SMTP STARTTLS"
                        smtp.starttls()
                    stage = "SMTP authentication"
                    smtp.login(self._user, self._password)
                stage = "SMTP message delivery"
                smtp.send_message(message)
        except Exception as exc:  # noqa: BLE001 - delivery must not escape the scheduler
            LOGGER.warning(
                "report email delivery failed during %s (%s)", stage, type(exc).__name__
            )
            return False
        return True


def run_report(
    period: ReportPeriod,
    snapshot_provider: Callable[[ReportPeriod], ReportSnapshot],
    telegram: TelegramReportTransport | None,
    email: EmailReportSender | None,
    owner_chat_ids: Sequence[str],
) -> ReportDocument | None:
    """Build and deliver a scheduled report without raising into the scheduler."""
    try:
        document = build_report(snapshot_provider(period))
    except Exception:
        LOGGER.exception("report snapshot generation failed")
        return None
    if telegram is not None:
        try:
            deliver_telegram(document, telegram, owner_chat_ids)
        except Exception:
            LOGGER.exception("report Telegram delivery failed")
    if email is not None:
        try:
            email.send(document)
        except Exception:
            LOGGER.exception("report email delivery failed")
    return document
