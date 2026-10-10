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
Tests for MCP Logger
"""

import json
import logging
from unittest.mock import patch, MagicMock, Mock
from qradar_mcp.utils.mcp_logger import MCPLogger, get_mcp_logger, log_mcp


class TestMCPLoggerInitialization:
    """Test MCPLogger initialization and singleton behavior."""

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    def test_singleton_pattern(self, mock_load_config):
        """Test that MCPLogger follows singleton pattern."""
        mock_load_config.return_value = None

        logger1 = MCPLogger()
        logger2 = MCPLogger()

        assert logger1 is logger2

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    def test_initialization_local_mode(self, mock_load_config):
        """Test initialization in local mode with config."""
        mock_load_config.return_value = {
            'qradar': {
                'host': 'https://test.qradar.com'
            }
        }

        # Reset singleton
        MCPLogger._instance = None

        logger = MCPLogger()

        assert logger._local_mode is True
        assert isinstance(logger._logger, logging.Logger)

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    def test_initialization_qradar_app_mode(self, mock_load_config):
        """Test initialization in QRadar app mode without config."""
        mock_load_config.return_value = None

        # Reset singleton
        MCPLogger._instance = None

        with patch('qradar_mcp.utils.mcp_logger.MCPLogger._init_qpylib_logger'):
            logger = MCPLogger()

            assert logger._local_mode is False


class TestPythonLoggerInitialization:
    """Test Python logger initialization."""

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    @patch.dict('os.environ', {'LOG_LEVEL': 'DEBUG'})
    def test_init_python_logger_with_debug_level(self, mock_load_config):
        """Test Python logger initialization with DEBUG level."""
        mock_load_config.return_value = {'qradar': {'host': 'test'}}

        # Reset singleton
        MCPLogger._instance = None

        logger = MCPLogger()

        assert logger._logger.level == logging.DEBUG

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    @patch.dict('os.environ', {'LOG_LEVEL': 'WARNING'})
    def test_init_python_logger_with_warning_level(self, mock_load_config):
        """Test Python logger initialization with WARNING level."""
        mock_load_config.return_value = {'qradar': {'host': 'test'}}

        # Reset singleton
        MCPLogger._instance = None

        logger = MCPLogger()

        assert logger._logger.level == logging.WARNING

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    @patch.dict('os.environ', {}, clear=True)
    def test_init_python_logger_default_level(self, mock_load_config):
        """Test Python logger initialization with default INFO level."""
        mock_load_config.return_value = {'qradar': {'host': 'test'}}

        # Reset singleton
        MCPLogger._instance = None

        logger = MCPLogger()

        assert logger._logger.level == logging.INFO

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    def test_init_python_logger_no_duplicate_handlers(self, mock_load_config):
        """Test that Python logger doesn't add duplicate handlers."""
        mock_load_config.return_value = {'qradar': {'host': 'test'}}

        # Reset singleton
        MCPLogger._instance = None

        logger = MCPLogger()
        initial_handler_count = len(logger._logger.handlers)

        # Initialize again (shouldn't add more handlers)
        logger._init_python_logger()

        assert len(logger._logger.handlers) == initial_handler_count


class TestQpylibLoggerInitialization:
    """Test qpylib logger initialization."""

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    def test_init_qpylib_logger_success(self, mock_load_config):
        """Test successful qpylib logger initialization."""
        mock_load_config.return_value = None

        # Reset singleton
        MCPLogger._instance = None

        # Create a mock qpylib module with create_log method
        mock_qpylib_module = MagicMock()
        mock_qpylib_module.create_log = MagicMock()

        # Create a mock qpylib package that contains the qpylib module
        mock_qpylib_package = MagicMock()
        mock_qpylib_package.qpylib = mock_qpylib_module

        # Mock the qpylib package and module at import time
        with patch.dict('sys.modules', {'qpylib': mock_qpylib_package, 'qpylib.qpylib': mock_qpylib_module}):
            logger = MCPLogger()

            assert logger._local_mode is False
            mock_qpylib_module.create_log.assert_called_once_with(False)
            assert logger._logger is mock_qpylib_module

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    def test_init_qpylib_logger_fallback_on_error(self, mock_load_config):
        """Test fallback to Python logging when qpylib fails."""
        mock_load_config.return_value = None

        # Reset singleton
        MCPLogger._instance = None

        # Simulate qpylib import failure
        with patch.dict('sys.modules', {'qpylib': None, 'qpylib.qpylib': None}):
            # This will trigger the exception in _init_qpylib_logger and fall back to Python logging
            logger = MCPLogger()

            # Should have fallen back to local mode with Python logging
            assert logger._local_mode is True
            assert isinstance(logger._logger, logging.Logger)


class TestFormatMessageWithContext:
    """Test _format_message_with_context method."""

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    def test_format_message_without_context(self, mock_load_config):
        """Test formatting message without context."""
        mock_load_config.return_value = {'qradar': {'host': 'test'}}

        # Reset singleton
        MCPLogger._instance = None

        logger = MCPLogger()
        result = logger._format_message_with_context("Test message", {})

        assert result == "Test message"

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    def test_format_message_with_context(self, mock_load_config):
        """Test formatting message with context."""
        mock_load_config.return_value = {'qradar': {'host': 'test'}}

        # Reset singleton
        MCPLogger._instance = None

        logger = MCPLogger()
        context = {'key1': 'value1', 'key2': 'value2'}
        result = logger._format_message_with_context("Test message", context)

        assert "Test message" in result
        assert "|" in result
        # Verify JSON context is included
        assert "key1" in result
        assert "value1" in result


class TestLogPython:
    """Test _log_python method."""

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    def test_log_python_debug(self, mock_load_config):
        """Test Python logging at DEBUG level."""
        mock_load_config.return_value = {'qradar': {'host': 'test'}}

        # Reset singleton
        MCPLogger._instance = None

        logger = MCPLogger()
        logger._logger = Mock(spec=logging.Logger)

        logger._log_python("Debug message", "DEBUG")

        logger._logger.debug.assert_called_once()

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    def test_log_python_info(self, mock_load_config):
        """Test Python logging at INFO level."""
        mock_load_config.return_value = {'qradar': {'host': 'test'}}

        # Reset singleton
        MCPLogger._instance = None

        logger = MCPLogger()
        logger._logger = Mock(spec=logging.Logger)

        logger._log_python("Info message", "INFO")

        logger._logger.info.assert_called_once()

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    def test_log_python_warning(self, mock_load_config):
        """Test Python logging at WARNING level."""
        mock_load_config.return_value = {'qradar': {'host': 'test'}}

        # Reset singleton
        MCPLogger._instance = None

        logger = MCPLogger()
        logger._logger = Mock(spec=logging.Logger)

        logger._log_python("Warning message", "WARNING")

        logger._logger.warning.assert_called_once()

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    def test_log_python_warn_alias(self, mock_load_config):
        """Test Python logging with WARN alias."""
        mock_load_config.return_value = {'qradar': {'host': 'test'}}

        # Reset singleton
        MCPLogger._instance = None

        logger = MCPLogger()
        logger._logger = Mock(spec=logging.Logger)

        logger._log_python("Warn message", "WARN")

        logger._logger.warning.assert_called_once()

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    def test_log_python_error(self, mock_load_config):
        """Test Python logging at ERROR level."""
        mock_load_config.return_value = {'qradar': {'host': 'test'}}

        # Reset singleton
        MCPLogger._instance = None

        logger = MCPLogger()
        logger._logger = Mock(spec=logging.Logger)

        logger._log_python("Error message", "ERROR")

        logger._logger.error.assert_called_once()

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    def test_log_python_critical(self, mock_load_config):
        """Test Python logging at CRITICAL level."""
        mock_load_config.return_value = {'qradar': {'host': 'test'}}

        # Reset singleton
        MCPLogger._instance = None

        logger = MCPLogger()
        logger._logger = Mock(spec=logging.Logger)

        logger._log_python("Critical message", "CRITICAL")

        logger._logger.critical.assert_called_once()

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    def test_log_python_unknown_level_defaults_to_info(self, mock_load_config):
        """Test Python logging with unknown level defaults to INFO."""
        mock_load_config.return_value = {'qradar': {'host': 'test'}}

        # Reset singleton
        MCPLogger._instance = None

        logger = MCPLogger()
        logger._logger = Mock(spec=logging.Logger)

        logger._log_python("Unknown level message", "UNKNOWN")

        logger._logger.info.assert_called_once()

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    def test_log_python_with_context(self, mock_load_config):
        """Test Python logging with context kwargs."""
        mock_load_config.return_value = {'qradar': {'host': 'test'}}

        # Reset singleton
        MCPLogger._instance = None

        logger = MCPLogger()
        logger._logger = Mock(spec=logging.Logger)

        logger._log_python("Message", "INFO", key1="value1", key2="value2")

        # Verify the message includes context
        call_args = logger._logger.info.call_args[0][0]
        assert "Message" in call_args
        assert "key1" in call_args
        assert "value1" in call_args


class TestLogQpylib:
    """Test _log_qpylib method."""

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    def test_log_qpylib_without_context(self, mock_load_config):
        """Test qpylib logging without context."""
        mock_load_config.return_value = None

        # Reset singleton
        MCPLogger._instance = None

        logger = MCPLogger()
        logger._logger = Mock()
        logger._local_mode = False

        logger._log_qpylib("Test message", "INFO")

        logger._logger.log.assert_called_once_with("Test message", level="INFO")

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    def test_log_qpylib_with_context(self, mock_load_config):
        """Test qpylib logging with context."""
        mock_load_config.return_value = None

        # Reset singleton
        MCPLogger._instance = None

        logger = MCPLogger()
        logger._logger = Mock()
        logger._local_mode = False

        logger._log_qpylib("Test message", "WARNING", key1="value1")

        # Verify JSON structure was passed
        call_args = logger._logger.log.call_args[0][0]
        log_entry = json.loads(call_args)
        assert log_entry['message'] == "Test message"
        assert log_entry['context']['key1'] == "value1"


class TestLogMethod:
    """Test main log method."""

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    def test_log_routes_to_python_in_local_mode(self, mock_load_config):
        """Test that log routes to Python logging in local mode."""
        mock_load_config.return_value = {'qradar': {'host': 'test'}}

        # Reset singleton
        MCPLogger._instance = None

        logger = MCPLogger()
        logger._logger = Mock(spec=logging.Logger)

        with patch.object(logger, '_log_python') as mock_log_python:
            logger.log("Test message", "INFO")

            mock_log_python.assert_called_once_with("Test message", "INFO")

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    def test_log_routes_to_qpylib_in_app_mode(self, mock_load_config):
        """Test that log routes to qpylib in app mode."""
        mock_load_config.return_value = None

        # Reset singleton
        MCPLogger._instance = None

        logger = MCPLogger()
        logger._logger = Mock()
        logger._local_mode = False

        with patch.object(logger, '_log_qpylib') as mock_log_qpylib:
            logger.log("Test message", "ERROR")

            mock_log_qpylib.assert_called_once_with("Test message", "ERROR")

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    def test_log_converts_level_to_uppercase(self, mock_load_config):
        """Test that log converts level to uppercase."""
        mock_load_config.return_value = {'qradar': {'host': 'test'}}

        # Reset singleton
        MCPLogger._instance = None

        logger = MCPLogger()
        logger._logger = Mock(spec=logging.Logger)

        with patch.object(logger, '_log_python') as mock_log_python:
            logger.log("Test message", "info")

            # Verify uppercase was passed
            mock_log_python.assert_called_once_with("Test message", "INFO")

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    def test_log_with_kwargs(self, mock_load_config):
        """Test log with additional kwargs."""
        mock_load_config.return_value = {'qradar': {'host': 'test'}}

        # Reset singleton
        MCPLogger._instance = None

        logger = MCPLogger()
        logger._logger = Mock(spec=logging.Logger)

        with patch.object(logger, '_log_python') as mock_log_python:
            logger.log("Test message", "DEBUG", key1="value1", key2="value2")

            mock_log_python.assert_called_once_with(
                "Test message", "DEBUG", key1="value1", key2="value2"
            )


class TestGlobalFunctions:
    """Test global convenience functions."""

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    def test_get_mcp_logger_returns_singleton(self, mock_load_config):
        """Test that get_mcp_logger returns the singleton instance."""
        mock_load_config.return_value = {'qradar': {'host': 'test'}}

        # Reset singleton
        MCPLogger._instance = None

        logger1 = get_mcp_logger()
        logger2 = get_mcp_logger()

        assert logger1 is logger2
        assert isinstance(logger1, MCPLogger)

    @patch('qradar_mcp.client.qradar_rest_client.load_config')
    def test_log_mcp_convenience_function(self, mock_load_config):
        """Test log_mcp convenience function."""
        mock_load_config.return_value = {'qradar': {'host': 'test'}}

        # Reset singleton
        MCPLogger._instance = None

        with patch('qradar_mcp.utils.mcp_logger.get_mcp_logger') as mock_get_logger:
            mock_logger = Mock()
            mock_get_logger.return_value = mock_logger

            log_mcp("Test message", level="WARNING", key="value")

            mock_logger.log.assert_called_once_with(
                "Test message", "WARNING", key="value"
            )
