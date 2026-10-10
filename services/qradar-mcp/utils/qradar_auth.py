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
QRadar Authentication Middleware

Provides BaseHTTPMiddleware implementation for QRadar authentication that stores
authentication context in ContextVars, enabling access across async boundaries.
"""

from contextvars import ContextVar
from typing import Optional, Dict, Any, Callable
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from .mcp_logger import log_mcp


# Context variables to store authentication information for the current request
_user_id: ContextVar[Optional[int]] = ContextVar('user_id', default=None)
_username: ContextVar[Optional[str]] = ContextVar('username', default=None)
_service_id: ContextVar[Optional[int]] = ContextVar('service_id', default=None)
_service_label: ContextVar[Optional[str]] = ContextVar('service_label', default=None)
_is_service: ContextVar[bool] = ContextVar('is_service', default=False)


def get_user_id() -> Optional[int]:
    """
    Get the authenticated user ID from the current request context.

    Returns:
        User ID or None if not authenticated as a user
    """
    return _user_id.get()


def get_username() -> Optional[str]:
    """
    Get the authenticated username from the current request context.

    Returns:
        Username or None if not authenticated as a user
    """
    return _username.get()


def get_service_id() -> Optional[int]:
    """
    Get the authenticated service ID from the current request context.

    Returns:
        Service ID or None if not authenticated as a service
    """
    return _service_id.get()


def get_service_label() -> Optional[str]:
    """
    Get the authenticated service label from the current request context.

    Returns:
        Service label or None if not authenticated as a service
    """
    return _service_label.get()


def is_service_auth() -> bool:
    """
    Check if the current request is authenticated as a service.

    Returns:
        True if authenticated as a service, False otherwise
    """
    return _is_service.get()


def get_auth_context() -> Dict[str, Any]:
    """
    Get all authentication context information.

    Returns:
        Dictionary containing all authentication context variables
    """
    return {
        'user_id': get_user_id(),
        'username': get_username(),
        'service_id': get_service_id(),
        'service_label': get_service_label(),
        'is_service': is_service_auth()
    }


def set_user_auth(user_id: int, username: str) -> None:
    """
    Set user authentication context.

    Args:
        user_id: The authenticated user's ID
        username: The authenticated user's username
    """
    _user_id.set(user_id)
    _username.set(username)
    _service_id.set(None)
    _service_label.set(None)
    _is_service.set(False)


def set_service_auth(service_id: int, service_label: str) -> None:
    """
    Set service authentication context.

    Args:
        service_id: The authenticated service's ID
        service_label: The authenticated service's label
    """
    _user_id.set(None)
    _username.set(None)
    _service_id.set(service_id)
    _service_label.set(service_label)
    _is_service.set(True)


def clear_auth_context() -> None:
    """Clear all authentication context variables."""
    _user_id.set(None)
    _username.set(None)
    _service_id.set(None)
    _service_label.set(None)
    _is_service.set(False)


class QRadarAuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware to authenticate requests with QRadar and store auth context.

    This middleware:
    - Authenticates incoming requests as either a QRadar user or authorized service
    - Sets authentication context variables that work across async boundaries
    - Returns 401 if authentication fails

    For users:
    - Sets user_id, username, and is_service=False

    For authorized services:
    - Sets service_id, service_label, and is_service=True

    Authentication priority:
    1. User authentication (via SEC + CSRF tokens)
    2. Service authentication (via SEC token only)
    """

    def __init__(self, app, api_client_factory: Callable):
        """
        Initialize the authentication middleware.

        Args:
            app: The ASGI application
            api_client_factory: Callable that returns a QRadarRestClient instance
        """
        super().__init__(app)
        self.api_client_factory = api_client_factory

    async def dispatch(self, request: Request, call_next):
        """
        Authenticate the request and set auth context before processing.

        Args:
            request: The incoming request
            call_next: The next middleware/handler in the chain

        Returns:
            Response from the handler or 401 error if authentication fails
        """
        # Get API client instance
        api_client = self.api_client_factory()

        # First, try to authenticate as a user
        user_id, username = await api_client.identify_user()
        if username and user_id:
            set_user_auth(user_id, username)
            log_mcp(f"Authenticated as user: {username} (ID: {user_id})", level='DEBUG')

            try:
                response = await call_next(request)
                return response
            finally:
                # Clean up context after request
                clear_auth_context()

        # If user authentication fails, try authorized service authentication
        service_id, service_label = await api_client.identify_authorized_service()
        if service_label and service_id:
            set_service_auth(service_id, service_label)
            log_mcp(f"Authenticated as service: {service_label} (ID: {service_id})", level='DEBUG')

            try:
                response = await call_next(request)
                return response
            finally:
                # Clean up context after request
                clear_auth_context()

        # If both authentication methods fail, return 401
        log_mcp("Authentication failed for both user and service", level='ERROR')
        return JSONResponse(
            status_code=401,
            content={
                'code': 'UNAUTHENTICATED',
                'message': 'User is not authenticated. Failing request.'
            }
        )
