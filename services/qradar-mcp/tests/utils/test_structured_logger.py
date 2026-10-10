"""
Tests for Structured Logger
"""

from unittest.mock import patch
from qradar_mcp.utils.structured_logger import StructuredLogger, log_structured


class TestStructuredLogger:
    """Test StructuredLogger class."""

    @patch('qradar_mcp.utils.structured_logger.time')
    @patch('qradar_mcp.utils.structured_logger.get_user_id')
    @patch('qradar_mcp.utils.structured_logger.get_username')
    @patch('qradar_mcp.utils.structured_logger.is_service_auth')
    @patch('qradar_mcp.utils.structured_logger.get_request_method')
    @patch('qradar_mcp.utils.structured_logger.get_request_path')
    @patch('qradar_mcp.utils.structured_logger.get_request_remote_addr')
    def test_get_context_no_request(
        self, mock_remote_addr, mock_path, mock_method, mock_is_service,
        mock_username, mock_user_id, mock_time
    ):
        """Test _get_context when not in request context."""
        mock_time.time.return_value = 1704067200.0
        mock_time.strftime.return_value = '2024-01-01T00:00:00Z'
        mock_time.gmtime.return_value = None
        mock_is_service.return_value = False
        mock_user_id.return_value = None
        mock_username.return_value = None
        mock_method.return_value = None
        mock_path.return_value = None
        mock_remote_addr.return_value = None

        context = StructuredLogger._get_context()

        assert context['timestamp'] == 1704067200.0
        assert context['timestamp_iso'] == '2024-01-01T00:00:00Z'
        assert 'user_id' not in context
        assert 'service_id' not in context
        assert 'method' not in context

    @patch('qradar_mcp.utils.structured_logger.time')
    @patch('qradar_mcp.utils.structured_logger.get_user_id')
    @patch('qradar_mcp.utils.structured_logger.get_username')
    @patch('qradar_mcp.utils.structured_logger.is_service_auth')
    @patch('qradar_mcp.utils.structured_logger.get_request_method')
    @patch('qradar_mcp.utils.structured_logger.get_request_path')
    @patch('qradar_mcp.utils.structured_logger.get_request_remote_addr')
    def test_get_context_with_user(
        self, mock_remote_addr, mock_path, mock_method, mock_is_service,
        mock_username, mock_user_id, mock_time
    ):
        """Test _get_context with user information."""
        mock_time.time.return_value = 1704067200.0
        mock_time.strftime.return_value = '2024-01-01T00:00:00Z'
        mock_time.gmtime.return_value = None
        mock_is_service.return_value = False
        mock_user_id.return_value = 456
        mock_username.return_value = 'testuser'
        mock_method.return_value = 'POST'
        mock_path.return_value = '/test'
        mock_remote_addr.return_value = '127.0.0.1'

        context = StructuredLogger._get_context()

        assert context['user_id'] == 456
        assert context['username'] == 'testuser'
        assert context['auth_type'] == 'user'
        assert context['method'] == 'POST'
        assert context['path'] == '/test'
        assert context['remote_addr'] == '127.0.0.1'

    @patch('qradar_mcp.utils.structured_logger.time')
    @patch('qradar_mcp.utils.structured_logger.get_service_id')
    @patch('qradar_mcp.utils.structured_logger.get_service_label')
    @patch('qradar_mcp.utils.structured_logger.is_service_auth')
    @patch('qradar_mcp.utils.structured_logger.get_request_method')
    def test_get_context_with_service(
        self, mock_method, mock_is_service, mock_service_label,
        mock_service_id, mock_time
    ):
        """Test _get_context with service information."""
        mock_time.time.return_value = 1704067200.0
        mock_time.strftime.return_value = '2024-01-01T00:00:00Z'
        mock_time.gmtime.return_value = None
        mock_is_service.return_value = True
        mock_service_id.return_value = 'svc-789'
        mock_service_label.return_value = 'Test Service'
        mock_method.return_value = 'POST'

        context = StructuredLogger._get_context()

        assert context['service_id'] == 'svc-789'
        assert context['service_label'] == 'Test Service'
        assert context['auth_type'] == 'service'

    @patch('qradar_mcp.utils.structured_logger.log_mcp')
    @patch('qradar_mcp.utils.structured_logger.StructuredLogger._get_context')
    def test_log_basic(self, mock_context, mock_log_mcp):
        """Test basic logging."""
        mock_context.return_value = {
            'timestamp': 1704067200.0,
            'timestamp_iso': '2024-01-01T00:00:00Z'
        }

        StructuredLogger.log('Test message', level='INFO')

        mock_log_mcp.assert_called_once()
        call_args = mock_log_mcp.call_args
        # log_mcp is called with message and kwargs, not JSON
        assert call_args[0][0] == 'Test message'
        assert call_args[1]['level'] == 'INFO'
        assert call_args[1]['timestamp'] == 1704067200.0

    @patch('qradar_mcp.utils.structured_logger.log_mcp')
    @patch('qradar_mcp.utils.structured_logger.StructuredLogger._get_context')
    def test_log_with_extra_context(self, mock_context, mock_log_mcp):
        """Test logging with extra context."""
        mock_context.return_value = {
            'timestamp': 1704067200.0,
            'timestamp_iso': '2024-01-01T00:00:00Z'
        }

        StructuredLogger.log(
            'Test message',
            level='WARNING',
            custom_field='custom_value',
            another_field=123
        )

        call_args = mock_log_mcp.call_args
        # log_mcp is called with message and kwargs, not JSON
        assert call_args[1]['custom_field'] == 'custom_value'
        assert call_args[1]['another_field'] == 123
        assert call_args[1]['level'] == 'WARNING'

    @patch('qradar_mcp.utils.structured_logger.log_mcp')
    @patch('qradar_mcp.utils.structured_logger.StructuredLogger._get_context')
    def test_log_tool_execution_started(self, mock_context, mock_log_mcp):
        """Test logging tool execution started."""
        mock_context.return_value = {
            'timestamp': 1704067200.0,
            'timestamp_iso': '2024-01-01T00:00:00Z'
        }

        arguments = {'param1': 'value1', 'param2': 'value2'}

        StructuredLogger.log_tool_execution(
            tool_name='test_tool',
            arguments=arguments,
            stage='started'
        )

        call_args = mock_log_mcp.call_args
        # log_mcp is called with message and kwargs, not JSON
        assert call_args[0][0] == 'Tool test_tool started'
        assert call_args[1]['level'] == 'INFO'
        assert call_args[1]['tool_name'] == 'test_tool'
        assert call_args[1]['stage'] == 'started'
        assert call_args[1]['arguments']['param1'] == 'value1'

    @patch('qradar_mcp.utils.structured_logger.log_mcp')
    @patch('qradar_mcp.utils.structured_logger.StructuredLogger._get_context')
    def test_log_tool_execution_failed(self, mock_context, mock_log_mcp):
        """Test logging tool execution failed."""
        mock_context.return_value = {
            'timestamp': 1704067200.0,
            'timestamp_iso': '2024-01-01T00:00:00Z'
        }

        arguments = {'param1': 'value1'}

        StructuredLogger.log_tool_execution(
            tool_name='test_tool',
            arguments=arguments,
            stage='failed',
            error='Test error'
        )

        call_args = mock_log_mcp.call_args
        # log_mcp is called with message and kwargs, not JSON
        assert call_args[0][0] == 'Tool test_tool failed'
        assert call_args[1]['level'] == 'ERROR'
        assert call_args[1]['error'] == 'Test error'

    @patch('qradar_mcp.utils.structured_logger.log_mcp')
    @patch('qradar_mcp.utils.structured_logger.StructuredLogger._get_context')
    def test_log_tool_execution_completed(self, mock_context, mock_log_mcp):
        """Test logging tool execution completed."""
        mock_context.return_value = {
            'timestamp': 1704067200.0,
            'timestamp_iso': '2024-01-01T00:00:00Z'
        }

        arguments = {'param1': 'value1'}

        StructuredLogger.log_tool_execution(
            tool_name='test_tool',
            arguments=arguments,
            stage='completed',
            duration=1.5
        )

        call_args = mock_log_mcp.call_args
        # log_mcp is called with message and kwargs, not JSON
        assert call_args[0][0] == 'Tool test_tool completed'
        assert call_args[1]['level'] == 'INFO'
        assert call_args[1]['duration'] == 1.5

    def test_sanitize_arguments_redacts_sensitive_keys(self):
        """Test that sensitive keys are redacted."""
        arguments = {
            'username': 'testuser',
            'password': 'secret123',
            'api_key': 'key123',
            'normal_param': 'normal_value'
        }

        sanitized = StructuredLogger._sanitize_arguments(arguments)

        assert sanitized['username'] == 'testuser'
        assert sanitized['password'] == '***REDACTED***'
        assert sanitized['api_key'] == '***REDACTED***'
        assert sanitized['normal_param'] == 'normal_value'

    def test_sanitize_arguments_truncates_long_strings(self):
        """Test that long strings are truncated."""
        long_string = 'x' * 1500
        arguments = {
            'long_param': long_string,
            'short_param': 'short'
        }

        sanitized = StructuredLogger._sanitize_arguments(arguments)

        assert len(sanitized['long_param']) == 1014  # 1000 + len('...[truncated]')
        assert sanitized['long_param'].endswith('...[truncated]')
        assert sanitized['short_param'] == 'short'

    def test_sanitize_arguments_all_sensitive_keys(self):
        """Test all sensitive key patterns."""
        arguments = {
            'password': 'secret',
            'token': 'token',
            'secret': 'secret',
            'api_key': 'key',
            'auth': 'auth'
        }

        sanitized = StructuredLogger._sanitize_arguments(arguments)

        for key in arguments.keys():
            assert sanitized[key] == '***REDACTED***'

    def test_sanitize_arguments_case_insensitive(self):
        """Test that sensitive key matching is case-insensitive."""
        arguments = {
            'PASSWORD': 'secret',
            'Api_Key': 'key',
            'AUTH_TOKEN': 'token'
        }

        sanitized = StructuredLogger._sanitize_arguments(arguments)

        assert sanitized['PASSWORD'] == '***REDACTED***'
        assert sanitized['Api_Key'] == '***REDACTED***'
        assert sanitized['AUTH_TOKEN'] == '***REDACTED***'

    @patch('qradar_mcp.utils.structured_logger.StructuredLogger.log')
    def test_log_structured_convenience_function(self, mock_log):
        """Test the convenience function."""
        log_structured('Test message', level='DEBUG', custom='value')

        mock_log.assert_called_once_with('Test message', 'DEBUG', custom='value')
