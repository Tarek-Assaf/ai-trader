"""Known environment secrets are redacted before logs reach handlers."""

from __future__ import annotations

import logging
import sys
from io import StringIO

from pydantic import SecretStr

from trader.core.logging import SecretRedactionFilter, configure_logging


def test_redaction_filter_masks_secret_from_formatted_message() -> None:
    """A secret interpolated into a log record is replaced before emission."""
    record = logging.LogRecord("test", logging.INFO, "test.py", 1, "token=%s", ("secret",), None)
    assert SecretRedactionFilter(("secret",)).filter(record)
    assert record.getMessage() == "token=[REDACTED]"


def test_child_logger_handler_redacts_interpolated_message_and_secret_extra() -> None:
    """Child logger messages, %-args, and structured secret fields are redacted."""
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s %(credential)s"))
    root = logging.getLogger()
    logger = logging.getLogger("trader.child")
    affected = [
        root,
        logging.getLogger("uvicorn"),
        logging.getLogger("uvicorn.access"),
        logging.getLogger("uvicorn.error"),
    ]
    old_handlers = root.handlers[:]
    old_filters = {
        id(handler): (handler, handler.filters[:])
        for configured_logger in affected
        for handler in configured_logger.handlers
    }
    old_level, old_propagate = logger.level, logger.propagate
    root.handlers[:] = [handler]
    logger.handlers.clear()
    logger.propagate = True
    logger.setLevel(logging.INFO)
    try:
        configure_logging(("top-secret",))
        logger.warning(
            "credentials=%s", "top-secret", extra={"credential": SecretStr("top-secret")}
        )
    finally:
        root.handlers[:] = old_handlers
        for original, filters in old_filters.values():
            original.filters[:] = filters
        logger.handlers.clear()
        logger.propagate = old_propagate
        logger.setLevel(old_level)

    output = stream.getvalue()
    assert "top-secret" not in output
    assert output.count("[REDACTED]") == 2


def test_structured_secret_value_is_redacted() -> None:
    """SecretStr values in LogRecord extras are redacted before output."""
    record = logging.LogRecord("trader.child", logging.INFO, "test.py", 1, "ok", (), None)
    record.credential = SecretStr("top-secret")
    assert SecretRedactionFilter(("top-secret",)).filter(record)
    assert record.credential == "[REDACTED]"


def test_exception_traceback_is_redacted() -> None:
    """Full exception tracebacks do not leak configured secret values."""
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(message)s"))
    record = logging.LogRecord("test", logging.ERROR, "test.py", 1, "runtime failed", (), None)
    try:
        raise RuntimeError("top-secret")
    except RuntimeError:
        record.exc_info = sys.exc_info()

    assert SecretRedactionFilter(("top-secret",)).filter(record)
    handler.handle(record)
    output = stream.getvalue()
    assert "Traceback" in output
    assert "top-secret" not in output
