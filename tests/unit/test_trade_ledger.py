from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine

from trader.core.audit import AuditEvent, SqlAuditWriter
from trader.core.db import metadata
from trader.reports.trade_ledger import (
    ClosedTradeSync,
    TradeLedger,
    closing_reason,
    money,
    parse_transactions,
    result_text,
)

NOW = datetime(2026, 10, 29, 15, 40, tzinfo=UTC)
ROWS = [
    {
        "dateUtc": "2026-10-29T15:30:28.441",
        "instrumentName": "US100",
        "transactionType": "TRADE",
        "note": "Trade closed",
        "reference": "142966855442902",
        "size": "-11.79",
        "currency": "USD",
        "dealId": "deal-b",
    },
    {
        "dateUtc": "2026-10-29T12:28:08.596",
        "instrumentName": "US100",
        "transactionType": "TRADE",
        "note": "Trade closed",
        "reference": "142955384107269",
        "size": "-2.41",
        "currency": "USD",
        "dealId": "deal-a",
    },
    {"transactionType": "DEPOSIT", "note": "Deposit", "size": "100"},
    {"transactionType": "TRADE", "note": "Trade opened", "reference": "x", "size": "0"},
    {"transactionType": "TRADE", "note": "Trade closed", "size": "bad"},
]


class FakeClient:
    def __init__(self, rows, activities=()):
        self.rows, self.activities, self.calls = rows, list(activities), 0

    @staticmethod
    def _timestamp(value):
        return value.strftime("%Y-%m-%dT%H:%M:%S")

    def _request_trading(self, method, path, **kwargs):
        self.calls += 1
        self.params = kwargs.get("params")
        assert path == "history/transactions"
        return {"transactions": self.rows}

    def get_activity(self, start, end):
        return self.activities


def _ledger():
    engine = create_engine("sqlite://")
    metadata.create_all(engine)
    writer = SqlAuditWriter(engine)
    return TradeLedger(engine, writer), writer


def test_parse_keeps_only_closed_trade_lines_with_real_results() -> None:
    legs = parse_transactions(ROWS)
    assert [(leg.deal_id, leg.pnl) for leg in legs] == [
        ("deal-b", Decimal("-11.79")),
        ("deal-a", Decimal("-2.41")),
    ]
    assert legs[0].closed_at == datetime(2026, 10, 29, 15, 30, 28, 441000, tzinfo=UTC)


def test_reason_comes_from_the_nearest_activity_for_the_same_deal() -> None:
    leg = parse_transactions(ROWS)[0]
    acts = [
        {"dealId": "deal-b", "dateUTC": "2026-10-29T12:00:00.000", "source": "USER"},
        {"dealId": "deal-b", "dateUTC": "2026-10-29T15:30:27.000", "source": "SL"},
        {"dealId": "other", "dateUTC": "2026-10-29T15:30:28.000", "source": "TP"},
    ]
    assert closing_reason(leg, acts) == "stop loss"
    assert closing_reason(leg, acts[:1]) == ""


def test_money_and_result_text_layout() -> None:
    assert money(Decimal("-2.4")) == "-$2.40"
    assert money(Decimal("3")) == "+$3.00"
    text = result_text(parse_transactions(ROWS)[0], "stop loss", Decimal("-14.20"))
    lines = text.splitlines()
    assert lines[0] == "RESULT US100: LOSS -$11.79"
    assert "Time: 29 Oct 18:30 (Riyadh)" in lines
    assert "Closed by: stop loss" in lines
    assert "Today so far: -$14.20" in lines


def test_sync_stores_once_alerts_only_fresh_trades_and_survives_restarts() -> None:
    ledger, _writer = _ledger()
    alerts: list[str] = []
    client = FakeClient(
        ROWS, [{"dealId": "deal-b", "dateUTC": "2026-10-29T15:30:27.000", "source": "SL"}]
    )
    sync = ClosedTradeSync(client, ledger, alerts.append, now=lambda: NOW)
    assert sync.poll() == 2
    assert len(alerts) == 1  # only the trade closed 10 minutes ago, not the 3-hour-old one
    assert "LOSS -$11.79" in alerts[0] and "stop loss" in alerts[0]
    assert sync.poll() == 0
    again = ClosedTradeSync(client, ledger, alerts.append, now=lambda: NOW)
    assert again.poll() == 0 and len(alerts) == 1


def test_r_multiples_sum_partials_per_deal_and_use_the_entry_risk() -> None:
    ledger, writer = _ledger()
    writer.append(AuditEvent("entry", {"event": "entry", "deal_id": "deal-b", "one_r_value": "10"}))
    ClosedTradeSync(FakeClient(ROWS), ledger, lambda _t: None, now=lambda: NOW).poll()
    items = ledger.r_multiples(NOW - timedelta(days=1))
    by_deal = {item.trade_id: item for item in items}
    assert by_deal["deal-b"].net_profit == Decimal("-11.79")
    assert by_deal["deal-b"].r_multiple == Decimal("-1.179")
    assert by_deal["deal-a"].r_multiple == Decimal(0)  # unknown risk
    assert ledger.r_multiples(NOW - timedelta(minutes=30))[0].trade_id == "deal-b"


def test_poll_asks_for_history_that_ended_a_moment_ago() -> None:
    ledger, _writer = _ledger()
    client = FakeClient(ROWS)
    ClosedTradeSync(client, ledger, lambda _t: None, now=lambda: NOW).poll()
    expected_end = (NOW - timedelta(seconds=15)).strftime("%Y-%m-%dT%H:%M:%S")
    assert client.params["to"] == expected_end
