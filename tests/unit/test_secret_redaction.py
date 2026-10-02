"""Known environment secrets are redacted before logs reach handlers."""

from __future__ import annotations

import logging

from trader.core.logging import SecretRedactionFilter


def test_redaction_filter_masks_secret_from_formatted_message() -> None:
    """A secret interpolated into a log record is replaced before emission."""
    record = logging.LogRecord("test", logging.INFO, "test.py", 1, "token=%s", ("secret",), None)
    assert SecretRedactionFilter(("secret",)).filter(record)
    assert record.getMessage() == "token=[REDACTED]"
