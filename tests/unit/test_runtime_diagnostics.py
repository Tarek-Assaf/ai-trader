from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from trader.core.diagnostics import bounded_text, exception_chain
from trader.integrations.capital_com.stream import CapitalComStreamClient


class RestStub:
    def _auth_headers(self) -> dict[str, str]:
        return {"CST": "cst", "X-SECURITY-TOKEN": "security"}


def test_exception_chain_is_single_line_bounded_and_redacted() -> None:
    cause = OSError(111, "password=top-secret token=hidden")
    error = RuntimeError("outer\nmessage api-key=another-secret")
    error.__cause__ = cause

    rendered = exception_chain(error)

    assert "RuntimeError" in rendered
    assert "OSError" in rendered
    assert "errno=111" in rendered
    assert "top-secret" not in rendered
    assert "another-secret" not in rendered
    assert "\n" not in rendered
    assert len(bounded_text("x" * 400)) == 160


def test_event_loop_lag_sampler_logs_material_delay(caplog) -> None:
    client = CapitalComStreamClient(RestStub(), ["US100"], lambda _update: None)
    client._next_lag_sample = 0.0

    with caplog.at_level(logging.WARNING, logger="trader.integrations.capital_com.stream"):
        client._sample_loop_lag(1.4)

    assert any("event-loop lag" in record.getMessage() for record in caplog.records)
    assert any("lag_seconds=1.400" in record.getMessage() for record in caplog.records)


def test_drop_record_contains_exception_chain_and_errno(caplog) -> None:
    client = CapitalComStreamClient(
        RestStub(),
        ["US100"],
        lambda _update: None,
        clock=lambda: datetime(2026, 10, 21, 16, 0, tzinfo=UTC),
    )
    cause = OSError(104, "connection reset")
    error = RuntimeError("stream wrapper")
    error.__cause__ = cause
    client._record_frame(json.dumps({"destination": "ohlc.event"}))

    with caplog.at_level(logging.INFO, logger="trader.integrations.capital_com.stream"):
        client._record_reconnect(error, "test")

    message = next(
        record.getMessage()
        for record in caplog.records
        if "Capital.com stream reconnect:" in record.getMessage()
    )
    assert "exception_chain=root:RuntimeError" in message
    assert "cause:ConnectionResetError" in message
    assert "errno=104" in message
    assert "last_frame_type=ohlc.event" in message
