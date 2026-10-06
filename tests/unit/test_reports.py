from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from email.message import EmailMessage
from typing import Any, ClassVar, cast

from trader.execution.pnl import BrokerAccountFigures, PnlEngine
from trader.reports import ReportSnapshot, SmtpEmailSender, build_report, deliver_telegram


def _snapshot(*, charts: tuple[bytes, ...] = ()) -> ReportSnapshot:
    now = datetime(2026, 10, 6, 12, tzinfo=UTC)
    broker = BrokerAccountFigures(
        balance=Decimal("1000"),
        equity=Decimal("1000"),
        currency="USD",
        margin_used=Decimal("0"),
        notional_exposure=Decimal("0"),
    )
    pnl = PnlEngine.calculate(
        [],
        starting_balance=Decimal("1000"),
        currency="USD",
        broker=broker,
        as_of=now,
    )
    return ReportSnapshot(
        period="daily",
        generated_at=now,
        pnl=pnl,
        trade_count=4,
        winning_trades=3,
        expectancy=Decimal("12.50"),
        strategy_pnl={"trend": Decimal("50")},
        strategy_stage_changes=("trend: stage 2 -> stage 3",),
        risk_events=("daily loss limit monitored",),
        llm_spend=Decimal("0.40"),
        llm_cap=Decimal("2.00"),
        issues=("No open issues",),
        chart_images=charts,
    )


def test_report_renders_deterministic_plain_and_html_outputs() -> None:
    document = build_report(_snapshot(), commentary="Watch the next session <carefully>.")
    assert "Daily trading report" in document.plain_text
    assert "Realized net: USD 0.00" in document.plain_text
    assert "Win rate: 75.00%" in document.plain_text
    assert "Watch the next session <carefully>." in document.plain_text
    assert "&lt;carefully&gt;" in document.html
    assert "USD 0.00" in document.html


class _FakeTelegram:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []
        self.photos: list[tuple[str, bytes, str]] = []

    def send_message(self, chat_id: str, text: str, *, reply_markup: Any = None) -> None:
        self.messages.append((chat_id, text))

    def send_photo(self, chat_id: str, photo: bytes, *, caption: str = "") -> None:
        self.photos.append((chat_id, photo, caption))


def test_report_delivery_sends_summary_and_charts() -> None:
    transport = _FakeTelegram()
    deliver_telegram(build_report(_snapshot(charts=(b"png",))), transport, ["owner"])
    assert transport.messages[0][0] == "owner"
    assert transport.photos == [("owner", b"png", "Daily report chart 1")]


class _FakeSMTP:
    sent: ClassVar[list[EmailMessage]] = []

    def __init__(self, host: str, port: int, timeout: int) -> None:
        self.host, self.port, self.timeout = host, port, timeout

    def __enter__(self) -> _FakeSMTP:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def starttls(self) -> None:
        pass

    def login(self, user: str, password: str) -> None:
        assert user == "reporter"
        assert password == "secret"

    def send_message(self, message: EmailMessage) -> None:
        self.sent.append(message)


def test_smtp_sender_uses_plain_and_html_parts_without_secret_logging() -> None:
    sender = SmtpEmailSender(
        "smtp.example",
        587,
        "reporter",
        "secret",
        "owner@example.com",
        smtp_factory=cast(Any, _FakeSMTP),
    )
    assert sender.send(build_report(_snapshot()))
    assert _FakeSMTP.sent[-1].get_content_type() == "multipart/alternative"
    body = _FakeSMTP.sent[-1].get_body(preferencelist=("plain",))
    assert body is not None
    assert "Daily trading report" in body.get_content()


def test_missing_smtp_configuration_is_a_noop() -> None:
    sender = SmtpEmailSender(None, None, None, None, None)
    assert not sender.send(build_report(_snapshot()))
