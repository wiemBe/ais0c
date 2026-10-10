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
Request Context Management

Provides context variables and middleware for capturing HTTP request information
and making it accessible across async boundaries in the FastMCP server.
"""

from contextvars import ContextVar
from typing import Optional, Dict, Any
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request


# Context variables to store request information for the current request
_request_method: ContextVar[Optional[str]] = ContextVar('request_method', default=None)
_request_path: ContextVar[Optional[str]] = ContextVar('request_path', default=None)
_request_url: ContextVar[Optional[str]] = ContextVar('request_url', default=None)
_request_remote_addr: ContextVar[Optional[str]] = ContextVar('request_remote_addr', default=None)
_request_user_agent: ContextVar[Optional[str]] = ContextVar('request_user_agent', default=None)
_request_referer: ContextVar[Optional[str]] = ContextVar('request_referer', default=None)
_request_content_type: ContextVar[Optional[str]] = ContextVar('request_content_type', default=None)
_request_query_params: ContextVar[Optional[Dict[str, str]]] = ContextVar('request_query_params', default=None)
_request_headers: ContextVar[Optional[Dict[str, str]]] = ContextVar('request_headers', default=None)


def get_request_method() -> Optional[str]:
    """
    Get the HTTP method from the current request context.

    Returns:
        HTTP method (GET, POST, etc.) or None
    """
    return _request_method.get()


def get_request_path() -> Optional[str]:
    """
    Get the request path from the current request context.

    Returns:
        Request path (e.g., '/api/v1/offenses') or None
    """
    return _request_path.get()


def get_request_url() -> Optional[str]:
    """
    Get the full request URL from the current request context.

    Returns:
        Full URL including scheme, host, and path or None
    """
    return _request_url.get()


def get_request_remote_addr() -> Optional[str]:
    """
    Get the remote address from the current request context.

    Returns:
        Remote IP address or None
    """
    return _request_remote_addr.get()


def get_request_user_agent() -> Optional[str]:
    """
    Get the User-Agent header from the current request context.

    Returns:
        User-Agent string or None
    """
    return _request_user_agent.get()


def get_request_referer() -> Optional[str]:
    """
    Get the Referer header from the current request context.

    Returns:
        Referer URL or None
    """
    return _request_referer.get()


def get_request_content_type() -> Optional[str]:
    """
    Get the Content-Type header from the current request context.

    Returns:
        Content-Type string or None
    """
    return _request_content_type.get()


def get_request_query_params() -> Optional[Dict[str, str]]:
    """
    Get the query parameters from the current request context.

    Returns:
        Dictionary of query parameters or None
    """
    return _request_query_params.get()


def get_request_headers() -> Optional[Dict[str, str]]:
    """
    Get all request headers from the current request context.

    Returns:
        Dictionary of request headers or None
    """
    return _request_headers.get()


def get_request_context() -> Dict[str, Any]:
    """
    Get all request context information.

    Returns:
        Dictionary containing all request context variables
    """
    return {
        'method': get_request_method(),
        'path': get_request_path(),
        'url': get_request_url(),
        'remote_addr': get_request_remote_addr(),
        'user_agent': get_request_user_agent(),
        'referer': get_request_referer(),
        'content_type': get_request_content_type(),
        'query_params': get_request_query_params(),
        'headers': get_request_headers()
    }


def set_request_context(  # pylint: disable=too-many-positional-arguments
    method: Optional[str] = None,
    path: Optional[str] = None,
    url: Optional[str] = None,
    remote_addr: Optional[str] = None,
    user_agent: Optional[str] = None,
    referer: Optional[str] = None,
    content_type: Optional[str] = None,
    query_params: Optional[Dict[str, str]] = None,
    headers: Optional[Dict[str, str]] = None
) -> None:
    """
    Set request context information.

    Args:
        method: HTTP method
        path: Request path
        url: Full request URL
        remote_addr: Remote IP address
        user_agent: User-Agent header
        referer: Referer header
        content_type: Content-Type header
        query_params: Query parameters dictionary
        headers: Request headers dictionary
    """
    _request_method.set(method)
    _request_path.set(path)
    _request_url.set(url)
    _request_remote_addr.set(remote_addr)
    _request_user_agent.set(user_agent)
    _request_referer.set(referer)
    _request_content_type.set(content_type)
    _request_query_params.set(query_params)
    _request_headers.set(headers)


def clear_request_context() -> None:
    """Clear all request context variables."""
    _request_method.set(None)
    _request_path.set(None)
    _request_url.set(None)
    _request_remote_addr.set(None)
    _request_user_agent.set(None)
    _request_referer.set(None)
    _request_content_type.set(None)
    _request_query_params.set(None)
    _request_headers.set(None)


class RequestContextMiddleware(BaseHTTPMiddleware):
    """
    Middleware to capture HTTP request information and store in context variables.

    This middleware:
    - Extracts HTTP request metadata (method, path, headers, etc.)
    - Sets context variables that work across async boundaries
    - Makes request information accessible throughout the request lifecycle

    Captured information:
    - HTTP method (GET, POST, etc.)
    - Request path and full URL
    - Remote address (client IP)
    - User-Agent header
    - Referer header
    - Content-Type header
    - Query parameters
    - All request headers
    """

    async def dispatch(self, request: Request, call_next):
        """
        Extract request information and set context before processing.

        Args:
            request: The incoming request
            call_next: The next middleware/handler in the chain

        Returns:
            Response from the handler
        """
        # Extract remote address (client IP)
        remote_addr = None
        if request.client:
            remote_addr = request.client.host

        # Extract query parameters
        query_params = dict(request.query_params) if request.query_params else {}

        # Extract all headers as a dictionary
        headers = dict(request.headers) if request.headers else {}

        # Set all request context variables
        set_request_context(
            method=request.method,
            path=request.url.path,
            url=str(request.url),
            remote_addr=remote_addr,
            user_agent=request.headers.get('user-agent'),
            referer=request.headers.get('referer'),
            content_type=request.headers.get('content-type'),
            query_params=query_params,
            headers=headers
        )

        try:
            response = await call_next(request)
            return response
        finally:
            # Clean up context after request
            clear_request_context()
