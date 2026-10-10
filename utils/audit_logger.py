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
Audit Logging

Provides comprehensive audit logging for compliance and security.
"""

import json
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional
from .mcp_logger import log_mcp

# Import context variable getters from middleware
from .request_context import (
    get_request_method,
    get_request_path,
    get_request_remote_addr,
    get_request_user_agent
)
from .qradar_auth import (
    get_user_id,
    get_username,
    get_service_id,
    get_service_label,
    is_service_auth
)


class AuditLogger:
    """
    Audit logger for tracking user actions and tool executions.

    Logs are written in a structured format suitable for SIEM ingestion.
    """

    # Audit event types
    EVENT_TOOL_EXECUTION = 'tool_execution'
    EVENT_AUTHENTICATION = 'authentication'
    EVENT_AUTHORIZATION = 'authorization'
    EVENT_DATA_ACCESS = 'data_access'
    EVENT_DATA_MODIFICATION = 'data_modification'

    @staticmethod
    def _get_audit_context() -> Dict[str, Any]:
        """Extract audit context from request."""
        context = {
            'timestamp': str(datetime.now(tz=timezone.utc)),
            'timestamp_unix': time.time()
        }

        # User/Service information from auth context
        if is_service_auth():
            service_id = get_service_id()
            service_label = get_service_label()
            context['actor'] = {
                'type': 'service',
                'id': service_id,
                'label': service_label
            }
        else:
            user_id = get_user_id()
            username = get_username()
            context['actor'] = {
                'type': 'user',
                'id': user_id,
                'username': username
            }

        # Request information from request context
        method = get_request_method()
        path = get_request_path()

        if method or path:  # If we have any request context
            remote_addr = get_request_remote_addr()
            user_agent = get_request_user_agent()
            context['request'] = {
                'method': method,
                'path': path,
                'remote_addr': remote_addr,
                'user_agent': user_agent
            }

        return context

    @staticmethod
    def log_tool_execution(
        tool_name: str,
        arguments: Dict[str, Any],
        result: Dict[str, Any],
        duration_seconds: float
    ):
        """
        Log a tool execution for audit purposes.

        Args:
            tool_name: Name of the tool executed
            arguments: Tool arguments (will be sanitized)
            result: Tool execution result
            duration_seconds: Execution duration
        """
        context = AuditLogger._get_audit_context()

        audit_entry = {
            'event_type': AuditLogger.EVENT_TOOL_EXECUTION,
            'tool_name': tool_name,
            'arguments': AuditLogger._sanitize_for_audit(arguments),
            'success': not result.get('isError', False),
            'duration_seconds': duration_seconds,
            **context
        }

        # Add error information if failed
        if result.get('isError'):
            content = result.get('content', [{}])
            if content:
                audit_entry['error'] = {
                    'message': content[0].get('text', 'Unknown error')
                }

        # Write to audit log
        AuditLogger._write_audit_log(audit_entry)

    @staticmethod
    def log_authentication(
        success: bool,
        auth_type: str,
        user_id: Optional[int] = None
    ):
        """
        Log an authentication attempt.

        Args:
            success: Whether authentication succeeded
            auth_type: Type of authentication (user, service)
            user_id: User or service ID if successful
        """
        context = AuditLogger._get_audit_context()

        audit_entry = {
            'event_type': AuditLogger.EVENT_AUTHENTICATION,
            'auth_type': auth_type,
            'success': success,
            'user_id': user_id,
            **context
        }

        AuditLogger._write_audit_log(audit_entry)

    @staticmethod
    def log_data_access(resource_type: str, resource_id: Any, action: str):
        """
        Log data access for compliance.

        Args:
            resource_type: Type of resource (offense, search, reference_set, etc.)
            resource_id: ID of the resource accessed
            action: Action performed (read, list, query)
        """
        context = AuditLogger._get_audit_context()

        audit_entry = {
            'event_type': AuditLogger.EVENT_DATA_ACCESS,
            'resource_type': resource_type,
            'resource_id': str(resource_id),
            'action': action,
            **context
        }

        AuditLogger._write_audit_log(audit_entry)

    @staticmethod
    def log_data_modification(
        resource_type: str,
        resource_id: Any,
        action: str,
        changes: Optional[Dict[str, Any]] = None
    ):
        """
        Log data modification for compliance.

        Args:
            resource_type: Type of resource modified
            resource_id: ID of the resource
            action: Action performed (create, update, delete)
            changes: Dictionary of changes made
        """
        context = AuditLogger._get_audit_context()

        audit_entry = {
            'event_type': AuditLogger.EVENT_DATA_MODIFICATION,
            'resource_type': resource_type,
            'resource_id': str(resource_id),
            'action': action,
            **context
        }

        if changes:
            audit_entry['changes'] = AuditLogger._sanitize_for_audit(changes)

        AuditLogger._write_audit_log(audit_entry)

    @staticmethod
    def _sanitize_for_audit(data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sanitize data for audit logging (remove sensitive information).

        Args:
            data: Data to sanitize

        Returns:
            Sanitized data safe for audit logs
        """
        sensitive_keys = [
            'password', 'token', 'secret', 'api_key', 'auth',
            'sec_token', 'csrf_token', 'authorized_service_token'
        ]

        sanitized = {}
        for key, value in data.items():
            if any(sensitive in key.lower() for sensitive in sensitive_keys):
                sanitized[key] = '***REDACTED***'
            elif isinstance(value, dict):
                sanitized[key] = AuditLogger._sanitize_for_audit(value)
            elif isinstance(value, str) and len(value) > 1000:
                sanitized[key] = value[:1000] + '...[truncated]'
            else:
                sanitized[key] = value

        return sanitized

    @staticmethod
    def _write_audit_log(audit_entry: Dict[str, Any]):
        """
        Write audit entry to log.

        Args:
            audit_entry: Audit entry to write
        """
        # Write as JSON for structured logging
        audit_json = json.dumps(audit_entry)

        # Use CRITICAL level for audit logs to ensure they're always captured
        log_mcp(f'AUDIT: {audit_json}', level='CRITICAL')

        # TODO: Consider writing to separate audit log file
        # TODO: Consider sending to external SIEM
