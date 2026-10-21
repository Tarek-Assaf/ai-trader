"""Structured logging setup with an explicit sensitive-value redaction filter."""

from __future__ import annotations

import logging
import os
import traceback
from collections.abc import Iterable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any


class SecretRedactionFilter(logging.Filter):
    """Replace known secret values before records are emitted by any handler."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets = tuple(secret for secret in secrets if secret)

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact secrets from the rendered message and clear interpolation args."""
        message = record.getMessage()
        message = self._redact_text(message)
        record.msg = message
        record.args = ()
        if record.exc_info:
            # Format while the exception is still available, then replace the
            # exception tuple so the handler cannot append an unredacted
            # traceback after this filter runs.
            record.exc_text = self._redact_text(
                "".join(traceback.format_exception(*record.exc_info))
            )
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = self._redact_text(record.exc_text)
        for name, value in tuple(record.__dict__.items()):
            if isinstance(value, logging.LogRecord):
                continue
            record.__dict__[name] = self._redact_value(value)
        return True

    def _redact_text(self, value: str) -> str:
        for secret in self._secrets:
            value = value.replace(secret, "[REDACTED]")
        return value

    def _redact_value(self, value: Any) -> Any:
        """Redact secret strings and SecretStr values recursively in structured fields."""
        from pydantic import SecretStr

        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        if isinstance(value, str):
            return self._redact_text(value)
        if isinstance(value, dict):
            return {key: self._redact_value(item) for key, item in value.items()}
        if isinstance(value, tuple):
            return tuple(self._redact_value(item) for item in value)
        if isinstance(value, list):
            return [self._redact_value(item) for item in value]
        return value


def configure_logging(secrets: Iterable[str] = ()) -> None:
    """Configure redacted console logging and a bounded volume log."""
    logging.getLogger("websockets").setLevel(logging.WARNING)
    root_logger = logging.getLogger()
    redactor = SecretRedactionFilter(secrets)
    if not root_logger.handlers:
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    handlers = {id(handler): handler for handler in root_logger.handlers}
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(name)
        handlers.update({id(handler): handler for handler in logger.handlers})
    log_path = Path(os.getenv("TRADER_LOG_PATH", "/workspace/logs/trader.log"))
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        existing_file = next(
            (
                handler
                for handler in root_logger.handlers
                if isinstance(handler, RotatingFileHandler)
                and Path(handler.baseFilename).resolve() == log_path.resolve()
            ),
            None,
        )
        if existing_file is None:
            existing_file = RotatingFileHandler(
                log_path,
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
            existing_file.setFormatter(
                logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
            )
            root_logger.addHandler(existing_file)
        handlers[id(existing_file)] = existing_file
    except OSError:
        # The API must remain available when the optional volume is absent or
        # read-only; container output remains the primary log sink.
        pass
    for handler in handlers.values():
        handler.addFilter(redactor)
