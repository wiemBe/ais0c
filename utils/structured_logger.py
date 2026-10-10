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
Structured Logging Utilities

Provides structured logging with contextual information for better observability.
"""

import time
from typing import Dict, Any
from .mcp_logger import log_mcp

# Import context variable getters from middleware
from .request_context import (
    get_request_method,
    get_request_path,
    get_request_url,
    get_request_remote_addr,
    get_request_user_agent,
    get_request_referer,
    get_request_content_type,
    get_request_query_params,
)
from .qradar_auth import (
    get_user_id,
    get_username,
    get_service_id,
    get_service_label,
    is_service_auth
)


class StructuredLogger:
    """
    Structured logger that adds contextual information to log entries.

    Automatically includes:
    - Timestamp
    - Request ID
    - User/Service ID
    - Tool name (if applicable)
    - Additional context
    """

    @staticmethod
    def _get_context() -> Dict[str, Any]:
        """Extract context from request and auth context variables."""
        context = {
            'timestamp': time.time(),
            'timestamp_iso': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        }

        # Add user/service information from auth context
        if is_service_auth():
            service_id = get_service_id()
            service_label = get_service_label()
            context['service_id'] = service_id
            context['service_label'] = service_label
            context['auth_type'] = 'service'
        else:
            user_id = get_user_id()
            username = get_username()
            if user_id:  # Only add if we have user context
                context['user_id'] = user_id
                context['username'] = username
                context['auth_type'] = 'user'

        # Add request information from request context
        method = get_request_method()
        path = get_request_path()
        url = get_request_url()
        remote_addr = get_request_remote_addr()
        user_agent = get_request_user_agent()
        referer = get_request_referer()
        content_type = get_request_content_type()
        query_params = get_request_query_params()

        if method:
            context['method'] = method
        if path:
            context['path'] = path
        if url:
            context['url'] = url
        if remote_addr:
            context['remote_addr'] = remote_addr
        if user_agent:
            context['user_agent'] = user_agent
        if referer:
            context['referer'] = referer
        if content_type:
            context['content_type'] = content_type
        if query_params:
            context['query_params'] = query_params

        return context

    @staticmethod
    def log(message: str, level: str = 'INFO', **extra_context):
        """
        Log a structured message with context.

        Args:
            message: Human-readable log message
            level: Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
            **extra_context: Additional context to include
        """
        context = StructuredLogger._get_context()
        context.update(extra_context)

        # Use MCP logger which handles both standalone and QRadar app mode
        log_mcp(message, level=level, **context)

    @staticmethod
    def log_tool_execution(tool_name: str, arguments: Dict[str, Any],
                          stage: str, **extra_context):
        """
        Log tool execution with standardized format.

        Args:
            tool_name: Name of the tool being executed
            arguments: Tool arguments (sanitized)
            stage: Execution stage (started, completed, failed)
            **extra_context: Additional context
        """
        context = {
            'tool_name': tool_name,
            'stage': stage,
            'arguments': StructuredLogger._sanitize_arguments(arguments)
        }
        context.update(extra_context)

        message = f"Tool {tool_name} {stage}"
        level = 'ERROR' if stage == 'failed' else 'INFO'

        StructuredLogger.log(message, level=level, **context)

    @staticmethod
    def _sanitize_arguments(arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Sanitize arguments for logging (remove sensitive data).

        Args:
            arguments: Original arguments

        Returns:
            Sanitized arguments safe for logging
        """
        sensitive_keys = ['password', 'token', 'secret', 'api_key', 'auth']
        sanitized = {}

        for key, value in arguments.items():
            if any(sensitive in key.lower() for sensitive in sensitive_keys):
                sanitized[key] = '***REDACTED***'
            elif isinstance(value, str) and len(value) > 1000:
                sanitized[key] = value[:1000] + '...[truncated]'
            else:
                sanitized[key] = value

        return sanitized


# Convenience function
def log_structured(message: str, level: str = 'INFO', **context):
    """Convenience function for structured logging."""
    StructuredLogger.log(message, level, **context)
