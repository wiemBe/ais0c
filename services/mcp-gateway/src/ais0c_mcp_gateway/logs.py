"""Logging without secrets (T-011 criterion 10).

The gateway knows its secrets: the profile tokens, the MCP tokens and the database password.
Every log line goes through a formatter that replaces each of them, so a secret cannot reach
the logs even inside an exception message or a library's debug output. On top of that, the
gateway logs no request headers, bodies or results: a call is logged as profile, agent, tool,
decision, status, reason code, evidence ID and latency.
"""

import logging
import sys
from collections.abc import Iterable
from typing import Final, TextIO

REDACTED: Final = "[REDACTED]"
# Shorter values would also replace ordinary text; real secrets are much longer.
MIN_SECRET_LENGTH: Final = 8
LOG_FORMAT: Final = "%(asctime)s %(levelname)s %(name)s %(message)s"


class Redactor:
    def __init__(self, secrets: Iterable[str]) -> None:
        unique = {secret for secret in secrets if len(secret) >= MIN_SECRET_LENGTH}
        # Longest first, so a secret that contains another is replaced whole.
        self._secrets = sorted(unique, key=len, reverse=True)

    def redact(self, text: str) -> str:
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        return text


class RedactingFormatter(logging.Formatter):
    """Formats a record as usual, exception and stack included, then removes the secrets."""

    def __init__(self, redactor: Redactor, fmt: str = LOG_FORMAT) -> None:
        super().__init__(fmt)
        self._redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        return self._redactor.redact(super().format(record))


def configure_logging(
    redactor: Redactor, *, level: int = logging.INFO, stream: TextIO | None = None
) -> logging.Handler:
    """Send every log record, the libraries' included, through one redacting handler."""
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(RedactingFormatter(redactor))
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)
    # Uvicorn and the MCP SDK log through their own loggers; they propagate to this handler.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        library_logger = logging.getLogger(name)
        library_logger.handlers.clear()
        library_logger.propagate = True
    return handler
