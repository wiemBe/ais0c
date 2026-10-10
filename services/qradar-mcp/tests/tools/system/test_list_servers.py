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
Tests for List Servers Tool
"""

import json
from unittest.mock import AsyncMock
import pytest
import httpx
from qradar_mcp.tools.system.list_servers import ListServersTool


@pytest.fixture
def tool():
    """Create a ListServersTool instance for testing."""
    return ListServersTool()


@pytest.fixture
def mock_servers():
    """Mock servers response."""
    return [
        {
            "server_id": 1,
            "hostname": "qradar-console",
            "private_ip": "192.168.1.10",
            "status": "ACTIVE",
            "managed_host_id": 1
        },
        {
            "server_id": 2,
            "hostname": "qradar-processor-1",
            "private_ip": "192.168.1.11",
            "status": "ACTIVE",
            "managed_host_id": 2
        }
    ]


class TestListServersMetadata:
    """Test tool metadata properties."""

    def test_tool_name(self, tool):
        """Test that tool name is correct."""
        assert tool.name == "list_servers"

    def test_tool_description(self, tool):
        """Test that tool has a description."""
        assert isinstance(tool.description, str)
        assert len(tool.description) > 0
        assert "server" in tool.description.lower()

    def test_input_schema(self, tool):
        """Test that input schema is properly defined."""
        schema = tool.input_schema
        assert isinstance(schema, dict)
        assert "type" in schema
        assert schema["type"] == "object"


class TestListServersExecution:
    """Test tool execution."""

    @pytest.mark.asyncio
    async def test_successful_execution(self, tool, mock_servers):
        """Test successful servers retrieval."""
        mock_request = httpx.Request("GET", "http://test")
        mock_response = httpx.Response(200, json=mock_servers, request=mock_request)

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({})

        assert "content" in result
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"
        content = json.loads(result["content"][0]["text"])
        assert len(content) == 2
        assert content[0]["hostname"] == "qradar-console"

    @pytest.mark.asyncio
    async def test_execution_with_filter(self, tool, mock_servers):
        """Test execution with filter."""
        mock_request = httpx.Request("GET", "http://test")
        mock_response = httpx.Response(200, json=[mock_servers[0]], request=mock_request)

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"filter": 'status="ACTIVE"'})

        assert "content" in result
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"
        tool.client.get.assert_called_once()
        call_args = tool.client.get.call_args
        assert call_args[1]["params"]["filter"] == 'status="ACTIVE"'

    @pytest.mark.asyncio
    async def test_execution_with_pagination(self, tool, mock_servers):
        """Test execution with pagination."""
        mock_request = httpx.Request("GET", "http://test")
        mock_response = httpx.Response(200, json=[mock_servers[0]], request=mock_request)

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
        mock_request = httpx.Request("GET", "http://test")
        mock_response = httpx.Response(200, json=[{"hostname": "qradar-console", "status": "ACTIVE"}], request=mock_request)

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"fields": "hostname,status"})

        assert "content" in result
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"
        tool.client.get.assert_called_once()
        call_args = tool.client.get.call_args
        assert call_args[1]["params"]["fields"] == "hostname,status"


class TestListServersErrorHandling:
    """Test error handling."""

    @pytest.mark.asyncio
    async def test_http_error_handling(self, tool):
        """Test handling of HTTP errors."""
        mock_request = httpx.Request("GET", "http://test")
        mock_response = httpx.Response(500, text="Internal Server Error", request=mock_request)

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(side_effect=httpx.HTTPStatusError(
            "Internal Server Error",
            request=mock_request,
            response=mock_response
        ))

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