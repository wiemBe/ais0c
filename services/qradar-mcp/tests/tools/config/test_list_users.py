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
Tests for List Users Tool
"""

import json
from unittest.mock import AsyncMock
import pytest
import httpx
from qradar_mcp.tools.config.list_users import ListUsersTool


@pytest.fixture
def tool():
    """Create a ListUsersTool instance for testing."""
    return ListUsersTool()


@pytest.fixture
def mock_users():
    """Mock users response."""
    return [
        {
            "id": 1,
            "username": "admin",
            "email": "admin@company.com",
            "user_role_id": 1,
            "security_profile_id": 1,
            "tenant_id": 1,
            "locale_id": "en_US",
            "enable_popup_notifications": True
        },
        {
            "id": 2,
            "username": "analyst1",
            "email": "analyst1@company.com",
            "user_role_id": 2,
            "security_profile_id": 2,
            "tenant_id": 1,
            "locale_id": "en_US",
            "enable_popup_notifications": False
        }
    ]


class TestListUsersMetadata:
    """Test tool metadata properties."""

    def test_tool_name(self, tool):
        """Test that tool name is correct."""
        assert tool.name == "list_users"

    def test_tool_description(self, tool):
        """Test that tool has a description."""
        assert isinstance(tool.description, str)
        assert len(tool.description) > 0
        assert "user" in tool.description.lower()

    def test_input_schema(self, tool):
        """Test that input schema is properly defined."""
        schema = tool.input_schema
        assert isinstance(schema, dict)
        assert "type" in schema
        assert schema["type"] == "object"


class TestListUsersExecution:
    """Test tool execution."""

    @pytest.mark.asyncio
    async def test_successful_execution(self, tool, mock_users):
        """Test successful users retrieval."""
        mock_response = httpx.Response(
            200,
            json=mock_users,
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({})

        assert "content" in result
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"
        content = json.loads(result["content"][0]["text"])
        assert len(content) == 2
        assert content[0]["username"] == "admin"

    @pytest.mark.asyncio
    async def test_execution_with_current_user(self, tool, mock_users):
        """Test execution with current_user flag."""
        mock_response = httpx.Response(
            200,
            json=[mock_users[0]],
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"current_user": True})

        assert "content" in result
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"
        tool.client.get.assert_called_once()
        call_args = tool.client.get.call_args
        assert call_args[1]["params"]["current_user"] == "true"

    @pytest.mark.asyncio
    async def test_execution_with_filter(self, tool, mock_users):
        """Test execution with filter."""
        mock_response = httpx.Response(
            200,
            json=[mock_users[0]],
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"filter": "tenant_id=1"})

        assert "content" in result
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"
        tool.client.get.assert_called_once()
        call_args = tool.client.get.call_args
        assert call_args[1]["params"]["filter"] == "tenant_id=1"

    @pytest.mark.asyncio
    async def test_execution_with_sort(self, tool, mock_users):
        """Test execution with sort."""
        mock_response = httpx.Response(
            200,
            json=mock_users,
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"sort": "+username"})

        assert "content" in result
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"
        tool.client.get.assert_called_once()
        call_args = tool.client.get.call_args
        assert call_args[1]["params"]["sort"] == "+username"

    @pytest.mark.asyncio
    async def test_execution_with_pagination(self, tool, mock_users):
        """Test execution with pagination."""
        mock_response = httpx.Response(
            200,
            json=[mock_users[0]],
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"limit": 10, "offset": 0})

        assert "content" in result
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"
        tool.client.get.assert_called_once()
        call_args = tool.client.get.call_args
        assert "Range" in call_args[1]["headers"]

    @pytest.mark.asyncio
    async def test_execution_with_fields(self, tool):
        """Test execution with field selection."""
        mock_response = httpx.Response(
            200,
            json=[{"username": "admin", "email": "admin@company.com"}],
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"fields": "username,email"})

        assert "content" in result
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"
        tool.client.get.assert_called_once()
        call_args = tool.client.get.call_args
        assert call_args[1]["params"]["fields"] == "username,email"


class TestListUsersErrorHandling:
    """Test error handling."""

    @pytest.mark.asyncio
    async def test_http_error_handling(self, tool):
        """Test handling of HTTP errors."""
        mock_response = httpx.Response(
            422,
            text="Invalid sort field",
            request=httpx.Request("GET", "http://test")
        )
        http_error = httpx.HTTPStatusError("Invalid sort field", request=mock_response.request, response=mock_response)

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(side_effect=http_error)

        result = await tool.execute({})

        assert result["isError"] is True
        assert "error" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_value_error_handling(self, tool):
        """Test handling of ValueError."""
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(side_effect=ValueError("Invalid value"))

        result = await tool.execute({})

        assert result["isError"] is True
        assert "tool execution failed: invalid value" == result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_runtime_error_handling(self, tool):
        """Test handling of RuntimeError."""
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(side_effect=RuntimeError("Runtime error"))

        result = await tool.execute({})

        assert result["isError"] is True
        assert "error" in result["content"][0]["text"].lower()
