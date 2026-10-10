# SPDX-License-Identifier: Apache-2.0
"""Process-wide logging that never prints a secret.

All log output goes through one handler whose formatter replaces secret values in the
finished line: message, arguments, exception traceback and stack all included. Loggers
that ship their own handlers (FastMCP's rich handlers, upstream's ``qradar-mcp`` logger)
are pointed at the same handler. Uvicorn runs with ``log_config=None`` and propagates to
the root logger.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterable
from typing import TextIO

from qradar_mcp.fork.redaction import Redactor
from qradar_mcp.utils import mcp_logger

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
UPSTREAM_LOGGER = "qradar-mcp"


class RedactingFormatter(logging.Formatter):
    """Formats a record, then removes secret values from the whole line."""

    def __init__(self, redactor: Redactor, fmt: str = LOG_FORMAT) -> None:
        super().__init__(fmt)
        self._redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        return self._redactor.text(super().format(record))


def configure_logging(
    level: str, secrets: Iterable[str], stream: TextIO | None = None
) -> logging.Handler:
    """Route every logger through one redacting handler and return that handler."""
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(RedactingFormatter(Redactor(secrets)))

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # Loggers that already have handlers do not propagate to root; give them ours.
    for logger in list(logging.Logger.manager.loggerDict.values()):
        if isinstance(logger, logging.Logger) and logger.handlers:
            logger.handlers = [handler]
            logger.propagate = False

    # Upstream's MCPLogger adds its own stdout handler when this logger has none, or switches
    # to qpylib when there is no config.json. Give it our handler and pin it to Python logging.
    upstream = logging.getLogger(UPSTREAM_LOGGER)
    upstream.handlers = [handler]
    upstream.propagate = False
    upstream.setLevel(level)
    _use_python_logging_for_upstream(upstream)
    return handler


def _use_python_logging_for_upstream(logger: logging.Logger) -> None:
    instance = object.__new__(mcp_logger.MCPLogger)
    instance._local_mode = True
    instance._logger = logger
    mcp_logger.MCPLogger._instance = instance
    mcp_logger._MCP_LOGGER = instance
