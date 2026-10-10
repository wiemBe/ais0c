"""
Tests for Audit Logger
"""

import json
from unittest.mock import patch
from qradar_mcp.utils.audit_logger import AuditLogger


class TestAuditLogger:
    """Test AuditLogger class."""

    def test_event_type_constants(self):
        """Test that event type constants are defined."""
        assert AuditLogger.EVENT_TOOL_EXECUTION == 'tool_execution'
        assert AuditLogger.EVENT_AUTHENTICATION == 'authentication'
        assert AuditLogger.EVENT_AUTHORIZATION == 'authorization'
        assert AuditLogger.EVENT_DATA_ACCESS == 'data_access'
        assert AuditLogger.EVENT_DATA_MODIFICATION == 'data_modification'

    @patch('qradar_mcp.utils.audit_logger.time.time')
    @patch('qradar_mcp.utils.audit_logger.datetime')
    @patch('qradar_mcp.utils.audit_logger.get_user_id')
    @patch('qradar_mcp.utils.audit_logger.get_username')
    @patch('qradar_mcp.utils.audit_logger.get_service_id')
    @patch('qradar_mcp.utils.audit_logger.get_service_label')
    @patch('qradar_mcp.utils.audit_logger.is_service_auth')
    @patch('qradar_mcp.utils.audit_logger.get_request_method')
    @patch('qradar_mcp.utils.audit_logger.get_request_path')
    @patch('qradar_mcp.utils.audit_logger.get_request_remote_addr')
    @patch('qradar_mcp.utils.audit_logger.get_request_user_agent')
    def test_get_audit_context_no_request(
        self, mock_user_agent, mock_remote_addr, mock_path, mock_method,
        mock_is_service, mock_service_label, mock_service_id,
        mock_username, mock_user_id, mock_datetime, mock_time
    ):
        """Test _get_audit_context when not in request context."""
        mock_datetime.now.return_value.__str__.return_value = '2024-01-01T00:00:00Z'
        mock_time.return_value = 1704067200.0
        mock_is_service.return_value = False
        mock_user_id.return_value = None
        mock_username.return_value = None
        mock_method.return_value = None
        mock_path.return_value = None

        context = AuditLogger._get_audit_context()

        assert context['timestamp'] == '2024-01-01T00:00:00Z'
        assert context['timestamp_unix'] == 1704067200.0
        assert 'actor' in context  # Actor is always present but may have None values
        assert 'request' not in context

    @patch('qradar_mcp.utils.audit_logger.time.time')
    @patch('qradar_mcp.utils.audit_logger.datetime')
    @patch('qradar_mcp.utils.audit_logger.get_user_id')
    @patch('qradar_mcp.utils.audit_logger.get_username')
    @patch('qradar_mcp.utils.audit_logger.is_service_auth')
    @patch('qradar_mcp.utils.audit_logger.get_request_method')
    @patch('qradar_mcp.utils.audit_logger.get_request_path')
    @patch('qradar_mcp.utils.audit_logger.get_request_remote_addr')
    @patch('qradar_mcp.utils.audit_logger.get_request_user_agent')
    def test_get_audit_context_with_user(
        self, mock_user_agent, mock_remote_addr, mock_path, mock_method,
        mock_is_service, mock_username, mock_user_id, mock_datetime, mock_time
    ):
        """Test _get_audit_context with user information."""
        mock_datetime.now.return_value.__str__.return_value = '2024-01-01T00:00:00Z'
        mock_time.return_value = 1704067200.0
        mock_is_service.return_value = False
        mock_user_id.return_value = 123
        mock_username.return_value = 'testuser'
        mock_method.return_value = 'POST'
        mock_path.return_value = '/test'
        mock_remote_addr.return_value = '127.0.0.1'
        mock_user_agent.return_value = 'TestAgent/1.0'

        context = AuditLogger._get_audit_context()

        assert context['actor']['type'] == 'user'
        assert context['actor']['id'] == 123
        assert context['actor']['username'] == 'testuser'
        assert context['request']['method'] == 'POST'
        assert context['request']['path'] == '/test'
        assert context['request']['remote_addr'] == '127.0.0.1'
        assert context['request']['user_agent'] == 'TestAgent/1.0'

    @patch('qradar_mcp.utils.audit_logger.time.time')
    @patch('qradar_mcp.utils.audit_logger.datetime')
    @patch('qradar_mcp.utils.audit_logger.get_service_id')
    @patch('qradar_mcp.utils.audit_logger.get_service_label')
    @patch('qradar_mcp.utils.audit_logger.is_service_auth')
    @patch('qradar_mcp.utils.audit_logger.get_request_method')
    @patch('qradar_mcp.utils.audit_logger.get_request_path')
    def test_get_audit_context_with_service(
        self, mock_path, mock_method, mock_is_service,
        mock_service_label, mock_service_id, mock_datetime, mock_time
    ):
        """Test _get_audit_context with service information."""
        mock_datetime.now.return_value.__str__.return_value = '2024-01-01T00:00:00Z'
        mock_time.return_value = 1704067200.0
        mock_is_service.return_value = True
        mock_service_id.return_value = 'svc-456'
        mock_service_label.return_value = 'Test Service'
        mock_method.return_value = 'POST'
        mock_path.return_value = '/test'

        context = AuditLogger._get_audit_context()

        assert context['actor']['type'] == 'service'
        assert context['actor']['id'] == 'svc-456'
        assert context['actor']['label'] == 'Test Service'

    @patch('qradar_mcp.utils.audit_logger.log_mcp')
    @patch('qradar_mcp.utils.audit_logger.AuditLogger._get_audit_context')
    def test_log_tool_execution_success(self, mock_context, mock_log_mcp):
        """Test logging successful tool execution."""
        mock_context.return_value = {
            'timestamp': '2024-01-01T00:00:00Z',
            'timestamp_unix': 1704067200.0
        }

        arguments = {'param1': 'value1', 'param2': 'value2'}
        result = {'content': [{'text': 'Success'}]}

        AuditLogger.log_tool_execution(
            tool_name='test_tool',
            arguments=arguments,
            result=result,
            duration_seconds=1.5
        )

        # Verify log_mcp was called
        assert mock_log_mcp.called
        call_args = mock_log_mcp.call_args
        log_message = call_args[0][0]

        assert 'AUDIT:' in log_message
        assert call_args[1]['level'] == 'CRITICAL'

        # Parse the JSON log entry
        audit_json = log_message.replace('AUDIT: ', '')
        audit_entry = json.loads(audit_json)

        assert audit_entry['event_type'] == 'tool_execution'
        assert audit_entry['tool_name'] == 'test_tool'
        assert audit_entry['success'] is True
        assert audit_entry['duration_seconds'] == 1.5

    @patch('qradar_mcp.utils.audit_logger.log_mcp')
    @patch('qradar_mcp.utils.audit_logger.AuditLogger._get_audit_context')
    def test_log_tool_execution_failure(self, mock_context, mock_log_mcp):
        """Test logging failed tool execution."""
        mock_context.return_value = {
            'timestamp': '2024-01-01T00:00:00Z',
            'timestamp_unix': 1704067200.0
        }

        arguments = {'param1': 'value1'}
        result = {
            'isError': True,
            'content': [{'text': 'Tool execution failed'}]
        }

        AuditLogger.log_tool_execution(
            tool_name='test_tool',
            arguments=arguments,
            result=result,
            duration_seconds=0.5
        )

        call_args = mock_log_mcp.call_args
        log_message = call_args[0][0]
        audit_json = log_message.replace('AUDIT: ', '')
        audit_entry = json.loads(audit_json)

        assert audit_entry['success'] is False
        assert 'error' in audit_entry
        assert audit_entry['error']['message'] == 'Tool execution failed'

    @patch('qradar_mcp.utils.audit_logger.log_mcp')
    @patch('qradar_mcp.utils.audit_logger.AuditLogger._get_audit_context')
    def test_log_authentication_success(self, mock_context, mock_log_mcp):
        """Test logging successful authentication."""
        mock_context.return_value = {
            'timestamp': '2024-01-01T00:00:00Z',
            'timestamp_unix': 1704067200.0
        }

        AuditLogger.log_authentication(
            success=True,
            auth_type='user',
            user_id=123
        )

        call_args = mock_log_mcp.call_args
        log_message = call_args[0][0]
        audit_json = log_message.replace('AUDIT: ', '')
        audit_entry = json.loads(audit_json)

        assert audit_entry['event_type'] == 'authentication'
        assert audit_entry['auth_type'] == 'user'
        assert audit_entry['success'] is True
        assert audit_entry['user_id'] == 123

    @patch('qradar_mcp.utils.audit_logger.log_mcp')
    @patch('qradar_mcp.utils.audit_logger.AuditLogger._get_audit_context')
    def test_log_authentication_failure(self, mock_context, mock_log_mcp):
        """Test logging failed authentication."""
        mock_context.return_value = {
            'timestamp': '2024-01-01T00:00:00Z',
            'timestamp_unix': 1704067200.0
        }

        AuditLogger.log_authentication(
            success=False,
            auth_type='service',
            user_id=None
        )

        call_args = mock_log_mcp.call_args
        log_message = call_args[0][0]
        audit_json = log_message.replace('AUDIT: ', '')
        audit_entry = json.loads(audit_json)

        assert audit_entry['success'] is False
        assert audit_entry['user_id'] is None

    @patch('qradar_mcp.utils.audit_logger.log_mcp')
    @patch('qradar_mcp.utils.audit_logger.AuditLogger._get_audit_context')
    def test_log_data_access(self, mock_context, mock_log_mcp):
        """Test logging data access."""
        mock_context.return_value = {
            'timestamp': '2024-01-01T00:00:00Z',
            'timestamp_unix': 1704067200.0
        }

        AuditLogger.log_data_access(
            resource_type='offense',
            resource_id=12345,
            action='read'
        )

        call_args = mock_log_mcp.call_args
        log_message = call_args[0][0]
        audit_json = log_message.replace('AUDIT: ', '')
        audit_entry = json.loads(audit_json)

        assert audit_entry['event_type'] == 'data_access'
        assert audit_entry['resource_type'] == 'offense'
        assert audit_entry['resource_id'] == '12345'
        assert audit_entry['action'] == 'read'

    @patch('qradar_mcp.utils.audit_logger.log_mcp')
    @patch('qradar_mcp.utils.audit_logger.AuditLogger._get_audit_context')
    def test_log_data_modification_without_changes(self, mock_context, mock_log_mcp):
        """Test logging data modification without changes."""
        mock_context.return_value = {
            'timestamp': '2024-01-01T00:00:00Z',
            'timestamp_unix': 1704067200.0
        }

        AuditLogger.log_data_modification(
            resource_type='reference_set',
            resource_id='test_set',
            action='create'
        )

        call_args = mock_log_mcp.call_args
        log_message = call_args[0][0]
        audit_json = log_message.replace('AUDIT: ', '')
        audit_entry = json.loads(audit_json)

        assert audit_entry['event_type'] == 'data_modification'
        assert audit_entry['resource_type'] == 'reference_set'
        assert audit_entry['resource_id'] == 'test_set'
        assert audit_entry['action'] == 'create'
        assert 'changes' not in audit_entry

    @patch('qradar_mcp.utils.audit_logger.log_mcp')
    @patch('qradar_mcp.utils.audit_logger.AuditLogger._get_audit_context')
    def test_log_data_modification_with_changes(self, mock_context, mock_log_mcp):
        """Test logging data modification with changes."""
        mock_context.return_value = {
            'timestamp': '2024-01-01T00:00:00Z',
            'timestamp_unix': 1704067200.0
        }

        changes = {
            'field1': 'new_value',
            'field2': 'another_value'
        }

        AuditLogger.log_data_modification(
            resource_type='offense',
            resource_id=123,
            action='update',
            changes=changes
        )

        call_args = mock_log_mcp.call_args
        log_message = call_args[0][0]
        audit_json = log_message.replace('AUDIT: ', '')
        audit_entry = json.loads(audit_json)

        assert 'changes' in audit_entry
        assert audit_entry['changes']['field1'] == 'new_value'

    def test_sanitize_for_audit_redacts_sensitive_keys(self):
        """Test that sensitive keys are redacted."""
        data = {
            'username': 'testuser',
            'password': 'secret123',
            'api_key': 'key123',
            'token': 'token123',
            'normal_field': 'normal_value'
        }

        sanitized = AuditLogger._sanitize_for_audit(data)

        assert sanitized['username'] == 'testuser'
        assert sanitized['password'] == '***REDACTED***'
        assert sanitized['api_key'] == '***REDACTED***'
        assert sanitized['token'] == '***REDACTED***'
        assert sanitized['normal_field'] == 'normal_value'

    def test_sanitize_for_audit_handles_nested_dicts(self):
        """Test that nested dictionaries are sanitized."""
        data = {
            'user': {
                'name': 'testuser',
                'password': 'secret123'
            },
            'config': {
                'api_key': 'key123',
                'timeout': 30
            }
        }

        sanitized = AuditLogger._sanitize_for_audit(data)

        assert sanitized['user']['name'] == 'testuser'
        assert sanitized['user']['password'] == '***REDACTED***'
        assert sanitized['config']['api_key'] == '***REDACTED***'
        assert sanitized['config']['timeout'] == 30

    def test_sanitize_for_audit_truncates_long_strings(self):
        """Test that long strings are truncated."""
        long_string = 'x' * 1500
        data = {
            'long_field': long_string,
            'short_field': 'short'
        }

        sanitized = AuditLogger._sanitize_for_audit(data)

        assert len(sanitized['long_field']) == 1014  # 1000 + len('...[truncated]')
        assert sanitized['long_field'].endswith('...[truncated]')
        assert sanitized['short_field'] == 'short'

    def test_sanitize_for_audit_case_insensitive(self):
        """Test that sensitive key matching is case-insensitive."""
        data = {
            'PASSWORD': 'secret',
            'Api_Key': 'key',
            'AUTH_TOKEN': 'token'
        }

        sanitized = AuditLogger._sanitize_for_audit(data)

        assert sanitized['PASSWORD'] == '***REDACTED***'
        assert sanitized['Api_Key'] == '***REDACTED***'
        assert sanitized['AUTH_TOKEN'] == '***REDACTED***'

    def test_sanitize_for_audit_all_sensitive_keys(self):
        """Test all sensitive key patterns."""
        data = {
            'password': 'secret',
            'token': 'token',
            'secret': 'secret',
            'api_key': 'key',
            'auth': 'auth',
            'sec_token': 'sec',
            'csrf_token': 'csrf',
            'authorized_service_token': 'service'
        }

        sanitized = AuditLogger._sanitize_for_audit(data)

        for key in data.keys():
            assert sanitized[key] == '***REDACTED***'

    @patch('qradar_mcp.utils.audit_logger.log_mcp')
    def test_write_audit_log(self, mock_log_mcp):
        """Test _write_audit_log writes to log_mcp."""
        audit_entry = {
            'event_type': 'test',
            'timestamp': '2024-01-01T00:00:00Z'
        }

        AuditLogger._write_audit_log(audit_entry)

        mock_log_mcp.assert_called_once()
        call_args = mock_log_mcp.call_args
        assert 'AUDIT:' in call_args[0][0]
        assert call_args[1]['level'] == 'CRITICAL'
