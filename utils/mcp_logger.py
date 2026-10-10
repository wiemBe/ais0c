# Copyright 2026 IBM Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


"""
MCP Logger - Environment-aware logging for QRadar MCP Server

Provides logging that works in both standalone container mode and
integrated QRadar app mode. Uses load_config() to detect the environment,
following the same pattern as QRadarRestClient.
"""

import os
import sys
import logging
import json


class MCPLogger:
    """
    Environment-aware logger for QRadar MCP Server.

    Detects whether running in:
    - Standalone mode (config.json exists): Uses Python logging to stdout
    - QRadar app mode (no config.json): Uses qpylib logging
    """

    _instance = None
    _logger = None
    _local_mode = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MCPLogger, cls).__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        """Initialize the logger based on environment detection."""
        # Import here to avoid circular dependency - pylint: disable=import-outside-toplevel
        from qradar_mcp.client.qradar_rest_client import load_config

        config = load_config()

        if config:
            # Local development mode - use Python logging
            self._local_mode = True
            self._init_python_logger()
        else:
            # QRadar App mode - use qpylib
            self._local_mode = False
            self._init_qpylib_logger()

    def _init_qpylib_logger(self):
        """Initialize qpylib logger for QRadar app mode."""
        try:
            # pylint: disable=import-outside-toplevel
            from qpylib import qpylib
            qpylib.create_log(False)
            self._logger = qpylib
        except Exception as e:  # pylint: disable=broad-exception-caught
            # Fallback to Python logging if qpylib fails - need broad catch for any qpylib error
            print(f"Warning: Failed to initialize qpylib logger: {e}", file=sys.stderr)
            print("Falling back to Python logging", file=sys.stderr)
            self._local_mode = True
            self._init_python_logger()

    def _init_python_logger(self):
        """Initialize Python logging for standalone mode."""
        logger = logging.getLogger('qradar-mcp')
        logger.setLevel(self._get_log_level())

        # Only add handler if not already present
        if not logger.handlers:
            handler = logging.StreamHandler(sys.stdout)
            handler.setLevel(self._get_log_level())

            # Format similar to worker_logger
            formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
            handler.setFormatter(formatter)

            logger.addHandler(handler)

        logger.propagate = False
        self._logger = logger

    def _get_log_level(self) -> int:
        """Get log level from environment variable."""
        level_str = os.getenv('LOG_LEVEL', 'INFO').upper()
        return getattr(logging, level_str, logging.INFO)

    def _format_message_with_context(self, message: str, kwargs: dict) -> str:
        """
        Format message with context for structured logging.

        Args:
            message: Log message
            kwargs: Additional context

        Returns:
            Formatted message string
        """
        if not kwargs:
            return message
        context_str = json.dumps(kwargs)
        return f"{message} | {context_str}"

    def _log_python(self, message: str, level: str, **kwargs):
        """
        Log using Python logging.

        Args:
            message: Log message
            level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            **kwargs: Additional context
        """
        full_message = self._format_message_with_context(message, kwargs)

        # Map level to logging method
        level_methods = {
            'DEBUG': self._logger.debug,
            'INFO': self._logger.info,
            'WARNING': self._logger.warning,
            'WARN': self._logger.warning,
            'ERROR': self._logger.error,
            'CRITICAL': self._logger.critical
        }

        log_method = level_methods.get(level, self._logger.info)
        log_method(full_message)

    def _log_qpylib(self, message: str, level: str, **kwargs):
        """
        Log using qpylib.

        Args:
            message: Log message
            level: Log level
            **kwargs: Additional context
        """
        if kwargs:
            log_entry = {
                'message': message,
                'context': kwargs
            }
            self._logger.log(json.dumps(log_entry), level=level)
        else:
            self._logger.log(message, level=level)

    def log(self, message: str, level: str = 'INFO', **kwargs):
        """
        Log a message with the appropriate logger.

        Args:
            message: Log message
            level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            **kwargs: Additional context to include in structured logs
        """
        level_upper = level.upper()

        if self._local_mode and isinstance(self._logger, logging.Logger):
            self._log_python(message, level_upper, **kwargs)
        else:
            self._log_qpylib(message, level_upper, **kwargs)


# Global logger instance
_MCP_LOGGER = None


def get_mcp_logger() -> MCPLogger:
    """Get the global MCP logger instance."""
    global _MCP_LOGGER  # pylint: disable=global-statement
    if _MCP_LOGGER is None:
        _MCP_LOGGER = MCPLogger()
    return _MCP_LOGGER


def log_mcp(message: str, level: str = 'INFO', **context):
    """
    Convenience function for MCP logging.

    Args:
        message: Log message
        level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        **context: Additional context for structured logging
    """
    logger = get_mcp_logger()
    logger.log(message, level, **context)
