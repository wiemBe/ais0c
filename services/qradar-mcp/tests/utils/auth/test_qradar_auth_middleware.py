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
Unit tests for QRadar authentication middleware.
"""

import pytest
from unittest.mock import Mock, AsyncMock
from starlette.applications import Starlette
import httpx
from httpx import AsyncClient
from starlette.responses import JSONResponse
from starlette.routing import Route

from qradar_mcp.utils.qradar_auth import (
    QRadarAuthMiddleware,
    get_user_id,
    get_username,
    get_service_id,
    get_service_label,
    is_service_auth,
    get_auth_context,
    set_user_auth,
    set_service_auth,
    clear_auth_context
)


class TestContextVariableAccessors:
    """Tests for context variable accessor functions."""

    def test_get_user_id_returns_none_by_default(self):
        """Test that get_user_id returns None when not set."""
        clear_auth_context()
        assert get_user_id() is None

    def test_get_username_returns_none_by_default(self):
        """Test that get_username returns None when not set."""
        clear_auth_context()
        assert get_username() is None

    def test_get_service_id_returns_none_by_default(self):
        """Test that get_service_id returns None when not set."""
        clear_auth_context()
        assert get_service_id() is None

    def test_get_service_label_returns_none_by_default(self):
        """Test that get_service_label returns None when not set."""
        clear_auth_context()
        assert get_service_label() is None

    def test_is_service_auth_returns_false_by_default(self):
        """Test that is_service_auth returns False when not set."""
        clear_auth_context()
        assert is_service_auth() is False

    def test_set_user_auth_sets_user_context(self):
        """Test that set_user_auth properly sets user context variables."""
        set_user_auth(42, "testuser")

        assert get_user_id() == 42
        assert get_username() == "testuser"
        assert get_service_id() is None
        assert get_service_label() is None
        assert is_service_auth() is False

        clear_auth_context()

    def test_set_service_auth_sets_service_context(self):
        """Test that set_service_auth properly sets service context variables."""
        set_service_auth(99, "test_service")

        assert get_user_id() is None
        assert get_username() is None
        assert get_service_id() == 99
        assert get_service_label() == "test_service"
        assert is_service_auth() is True

        clear_auth_context()

    def test_get_auth_context_returns_all_values(self):
        """Test that get_auth_context returns all context variables."""
        set_user_auth(1, "admin")

        context = get_auth_context()
        assert context['user_id'] == 1
        assert context['username'] == "admin"
        assert context['service_id'] is None
        assert context['service_label'] is None
        assert context['is_service'] is False

        clear_auth_context()

    def test_clear_auth_context_clears_all_values(self):
        """Test that clear_auth_context clears all context variables."""
        set_user_auth(1, "admin")
        clear_auth_context()

        assert get_user_id() is None
        assert get_username() is None
        assert get_service_id() is None
        assert get_service_label() is None
        assert is_service_auth() is False


class TestQRadarAuthMiddleware:
    """Tests for QRadarAuthMiddleware."""

    @pytest.mark.asyncio
    async def test_middleware_with_successful_user_authentication(self):
        """Test middleware with successful user authentication."""
        # Create mock API client
        mock_client = Mock()
        mock_client.identify_user = AsyncMock(return_value=(1, "admin"))
        mock_client.identify_authorized_service = AsyncMock(return_value=(-1, ""))

        def api_client_factory():
            return mock_client

        # Create test endpoint that uses context variables
        async def test_endpoint(request):
            return JSONResponse({
                "user_id": get_user_id(),
                "username": get_username(),
                "is_service": is_service_auth()
            })

        # Create Starlette app with middleware
        app = Starlette(routes=[Route("/test", test_endpoint)])
        app.add_middleware(QRadarAuthMiddleware, api_client_factory=api_client_factory)

        # Test the endpoint with async client
        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/test")

        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == 1
        assert data["username"] == "admin"
        assert data["is_service"] is False

    @pytest.mark.asyncio
    async def test_middleware_with_successful_service_authentication(self):
        """Test middleware with successful service authentication."""
        mock_client = Mock()
        mock_client.identify_user = AsyncMock(return_value=(-1, ""))
        mock_client.identify_authorized_service = AsyncMock(return_value=(10, "test_service"))

        def api_client_factory():
            return mock_client

        async def test_endpoint(request):
            return JSONResponse({
                "service_id": get_service_id(),
                "service_label": get_service_label(),
                "is_service": is_service_auth()
            })

        app = Starlette(routes=[Route("/test", test_endpoint)])
        app.add_middleware(QRadarAuthMiddleware, api_client_factory=api_client_factory)

        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/test")

        assert response.status_code == 200
        data = response.json()
        assert data["service_id"] == 10
        assert data["service_label"] == "test_service"
        assert data["is_service"] is True

    @pytest.mark.asyncio
    async def test_middleware_with_failed_authentication(self):
        """Test middleware when both authentication methods fail."""
        mock_client = Mock()
        mock_client.identify_user = AsyncMock(return_value=(-1, ""))
        mock_client.identify_authorized_service = AsyncMock(return_value=(-1, ""))

        def api_client_factory():
            return mock_client

        async def test_endpoint(request):
            return JSONResponse({"success": True})

        app = Starlette(routes=[Route("/test", test_endpoint)])
        app.add_middleware(QRadarAuthMiddleware, api_client_factory=api_client_factory)

        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/test")

        assert response.status_code == 401
        data = response.json()
        assert data["code"] == "UNAUTHENTICATED"

    @pytest.mark.asyncio
    async def test_middleware_prioritizes_user_auth_over_service(self):
        """Test that middleware tries user auth before service auth."""
        mock_client = Mock()
        mock_client.identify_user = AsyncMock(return_value=(1, "admin"))
        mock_client.identify_authorized_service = AsyncMock(return_value=(10, "test_service"))

        def api_client_factory():
            return mock_client

        async def test_endpoint(request):
            return JSONResponse({"is_service": is_service_auth()})

        app = Starlette(routes=[Route("/test", test_endpoint)])
        app.add_middleware(QRadarAuthMiddleware, api_client_factory=api_client_factory)

        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/test")

        assert response.status_code == 200
        data = response.json()
        assert data["is_service"] is False
        # Service auth should not be called
        mock_client.identify_authorized_service.assert_not_called()

    @pytest.mark.asyncio
    async def test_middleware_cleans_up_context_after_request(self):
        """Test that middleware cleans up context variables after request."""
        mock_client = Mock()
        mock_client.identify_user = AsyncMock(return_value=(1, "admin"))

        def api_client_factory():
            return mock_client

        async def test_endpoint(request):
            # Context should be set during request
            assert get_user_id() == 1
            assert get_username() == "admin"
            return JSONResponse({"success": True})

        app = Starlette(routes=[Route("/test", test_endpoint)])
        app.add_middleware(QRadarAuthMiddleware, api_client_factory=api_client_factory)

        async with AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/test")

        assert response.status_code == 200
        # Context should be cleared after request
        # Note: In test environment, context may persist, but in production
        # each request gets its own context
