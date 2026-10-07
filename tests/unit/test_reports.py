from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from email.message import EmailMessage
from typing import Any, ClassVar, cast

from trader.execution.pnl import BrokerAccountFigures, PnlEngine
from trader.reports import (
    ReportSnapshot,
    SmtpEmailSender,
    build_report,
    deliver_telegram,
    run_report,
)


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
        instrument_pnl={"NAS100": Decimal("50")},
        best_trade="trade-4 (+USD 30)",
        worst_trade="trade-2 (-USD 10)",
        strategy_stage_changes=("trend: stage 2 -> stage 3",),
        risk_events=("daily loss limit monitored",),
        data_health="healthy",
        backup_health="fresh",
        llm_spend=Decimal("0.40"),
        llm_cap=Decimal("2.00"),
        llm_currency="USD",
        issues=("No open issues",),
        chart_images=charts,
    )


def test_report_renders_deterministic_plain_and_html_outputs() -> None:
    document = build_report(_snapshot(), commentary="Watch the next session <carefully>.")
    assert "Daily trading report" in document.plain_text
    assert "Realized net: USD 0.00" in document.plain_text
    assert "Win rate: 75.00%" in document.plain_text
    assert "Watch the next session <carefully>." in document.plain_text
    assert "P&L by instrument" in document.plain_text
    assert "Best trade: trade-4 (+USD 30)" in document.plain_text
    assert "Data: healthy" in document.plain_text
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


def test_telegram_delivery_continues_after_transport_failures() -> None:
    class FailingTransport(_FakeTelegram):
        def send_message(self, chat_id: str, text: str, *, reply_markup: Any = None) -> None:
            raise RuntimeError("offline")

        def send_photo(self, chat_id: str, photo: bytes, *, caption: str = "") -> None:
            raise RuntimeError("offline")

    transport = FailingTransport()
    assert deliver_telegram(build_report(_snapshot(charts=(b"png",))), transport, ["owner"]) == 2


def test_all_report_periods_render() -> None:
    for period in ("daily", "weekly", "monthly"):
        snapshot = _snapshot()
        snapshot = ReportSnapshot(
            period=period,
            generated_at=snapshot.generated_at,
            pnl=snapshot.pnl,
        )
        assert period.title() in build_report(snapshot).plain_text


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


def test_missing_smtp_configuration_warns_once_and_telegram_still_delivers(caplog: Any) -> None:
    sender = SmtpEmailSender(None, None, None, None, None)
    transport = _FakeTelegram()
    document = build_report(_snapshot())
    assert not sender.send(document)
    assert not sender.send(document)
    deliver_telegram(document, transport, ["owner"])
    assert len(transport.messages) == 1
    warnings = [record for record in caplog.records if "using Telegram only" in record.getMessage()]
    assert len(warnings) == 1


def test_report_delivery_logs_never_include_transport_secrets(caplog: Any) -> None:
    sender = SmtpEmailSender(None, None, "reporter", "smtp-password", "owner@example.com")
    transport = _FakeTelegram()
    document = build_report(_snapshot())
    sender.send(document)
    deliver_telegram(document, transport, ["123456789"])
    output = "\n".join(record.getMessage() for record in caplog.records)
    assert "smtp-password" not in output
    assert "123456789" not in output


def test_smtp_from_env_bad_port_disables_delivery() -> None:
    sender = SmtpEmailSender.from_env(
        {
            "SMTP_HOST": "smtp.example",
            "SMTP_PORT": "not-a-port",
            "REPORT_EMAIL_TO": "owner@example.com",
        }
    )
    assert not sender.send(build_report(_snapshot()))


def test_run_report_never_raises_when_snapshot_provider_fails() -> None:
    def failing_provider(_period: str) -> ReportSnapshot:
        raise RuntimeError("database offline")

    assert run_report("daily", failing_provider, None, None, ["owner"]) is None
