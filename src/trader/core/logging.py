"""Structured logging setup with an explicit sensitive-value redaction filter."""

from __future__ import annotations

import logging
from collections.abc import Iterable


class SecretRedactionFilter(logging.Filter):
    """Replace known secret values before records are emitted by any handler."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets = tuple(secret for secret in secrets if secret)

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact secrets from the rendered message and clear interpolation args."""
        message = record.getMessage()
        for secret in self._secrets:
            message = message.replace(secret, "[REDACTED]")
        record.msg = message
        record.args = ()
        return True


def configure_logging(secrets: Iterable[str] = ()) -> None:
    """Configure standard logging with secret redaction on the root logger."""
    root_logger = logging.getLogger()
    root_logger.addFilter(SecretRedactionFilter(secrets))
    if not root_logger.handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
