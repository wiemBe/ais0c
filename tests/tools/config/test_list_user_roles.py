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
Tests for List User Roles Tool
"""

import json
from unittest.mock import AsyncMock
import pytest
import httpx
from qradar_mcp.tools.config.list_user_roles import ListUserRolesTool


@pytest.fixture
def tool():
    """Create a ListUserRolesTool instance for testing."""
    return ListUserRolesTool()


@pytest.fixture
def mock_roles():
    """Mock user roles response."""
    return [
        {
            "id": 1,
            "name": "Admin",
            "capabilities": [
                {"application_id": 1, "name": "ADMIN", "description": "Administrator"},
                {"application_id": 2, "name": "SEM", "description": "Security Event Manager"}
            ]
        },
        {
            "id": 2,
            "name": "Analyst",
            "capabilities": [
                {"application_id": 2, "name": "SEM", "description": "Security Event Manager"}
            ]
        }
    ]


class TestListUserRolesMetadata:
    """Test tool metadata properties."""

    def test_tool_name(self, tool):
        """Test that tool name is correct."""
        assert tool.name == "list_user_roles"

    def test_tool_description(self, tool):
        """Test that tool has a description."""
        assert isinstance(tool.description, str)
        assert len(tool.description) > 0
        assert "role" in tool.description.lower()

    def test_input_schema(self, tool):
        """Test that input schema is properly defined."""
        schema = tool.input_schema
        assert isinstance(schema, dict)
        assert "type" in schema
        assert schema["type"] == "object"


class TestListUserRolesExecution:
    """Test tool execution."""

    @pytest.mark.asyncio
    async def test_successful_execution(self, tool, mock_roles):
        """Test successful user roles retrieval."""
        mock_response = httpx.Response(
            200,
            json=mock_roles,
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
        assert content[0]["name"] == "Admin"

    @pytest.mark.asyncio
    async def test_execution_with_current_user_role(self, tool, mock_roles):
        """Test execution with current_user_role flag."""
        mock_response = httpx.Response(
            200,
            json=[mock_roles[0]],
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"current_user_role": True})

        assert "content" in result
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"
        tool.client.get.assert_called_once()
        call_args = tool.client.get.call_args
        assert call_args[1]["params"]["current_user_role"] == "true"

    @pytest.mark.asyncio
    async def test_execution_with_contains(self, tool, mock_roles):
        """Test execution with contains parameter."""
        mock_response = httpx.Response(
            200,
            json=[mock_roles[0]],
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"contains": "ADMIN"})

        assert "content" in result
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"
        tool.client.get.assert_called_once()
        call_args = tool.client.get.call_args
        assert call_args[1]["params"]["contains"] == "ADMIN"

    @pytest.mark.asyncio
    async def test_execution_with_filter(self, tool, mock_roles):
        """Test execution with filter."""
        mock_response = httpx.Response(
            200,
            json=[mock_roles[0]],
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"filter": "id=1"})

        assert "content" in result
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"
        tool.client.get.assert_called_once()
        call_args = tool.client.get.call_args
        assert call_args[1]["params"]["filter"] == "id=1"

    @pytest.mark.asyncio
    async def test_execution_with_pagination(self, tool, mock_roles):
        """Test execution with pagination."""
        mock_response = httpx.Response(
            200,
            json=[mock_roles[0]],
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
            json=[{"id": 1, "name": "Admin"}],
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"fields": "id,name"})

        assert "content" in result
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"
        tool.client.get.assert_called_once()
        call_args = tool.client.get.call_args
        assert call_args[1]["params"]["fields"] == "id,name"


class TestListUserRolesErrorHandling:
    """Test error handling."""

    @pytest.mark.asyncio
    async def test_http_error_handling(self, tool):
        """Test handling of HTTP errors."""
        mock_response = httpx.Response(
            500,
            text="Internal Server Error",
            request=httpx.Request("GET", "http://test")
        )
        http_error = httpx.HTTPStatusError("Internal Server Error", request=mock_response.request, response=mock_response)

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