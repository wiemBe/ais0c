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
Tests for Get System Info Tool
"""

import json
from unittest.mock import AsyncMock
import pytest
import httpx
from qradar_mcp.tools.system.get_system_info import GetSystemInfoTool


@pytest.fixture
def tool():
    """Create a GetSystemInfoTool instance for testing."""
    return GetSystemInfoTool()


@pytest.fixture
def mock_system_info():
    """Mock system information response."""
    return {
        "build_version": "7.5.0.20230615123456",
        "external_version": "7.5.0 UP3",
        "fips_enabled": True,
        "release_name": "QRadar 7.5.0 Update 3"
    }


class TestGetSystemInfoMetadata:
    """Test tool metadata properties."""

    def test_tool_name(self, tool):
        """Test that tool name is correct."""
        assert tool.name == "get_system_info"

    def test_tool_description(self, tool):
        """Test that tool has a description."""
        assert isinstance(tool.description, str)
        assert len(tool.description) > 0
        assert "system" in tool.description.lower()

    def test_input_schema(self, tool):
        """Test that input schema is properly defined."""
        schema = tool.input_schema
        assert isinstance(schema, dict)
        assert "type" in schema
        assert schema["type"] == "object"


class TestGetSystemInfoExecution:
    """Test tool execution."""

    @pytest.mark.asyncio
    async def test_successful_execution(self, tool, mock_system_info):
        """Test successful system info retrieval."""
        mock_request = httpx.Request("GET", "http://test")
        mock_response = httpx.Response(200, json=mock_system_info, request=mock_request)

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({})

        assert "content" in result
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"
        content = json.loads(result["content"][0]["text"])
        assert content["build_version"] == "7.5.0.20230615123456"
        assert content["external_version"] == "7.5.0 UP3"
        assert content["fips_enabled"] is True

    @pytest.mark.asyncio
    async def test_execution_with_fields(self, tool, mock_system_info):
        """Test execution with field selection."""
        mock_request = httpx.Request("GET", "http://test")
        mock_response = httpx.Response(200, json={
            "build_version": mock_system_info["build_version"],
            "external_version": mock_system_info["external_version"]
        }, request=mock_request)

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"fields": "build_version,external_version"})

        assert "content" in result
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"
        tool.client.get.assert_called_once()
        call_args = tool.client.get.call_args
        assert call_args[1]["params"]["fields"] == "build_version,external_version"


class TestGetSystemInfoErrorHandling:
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