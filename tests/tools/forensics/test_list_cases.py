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
Unit tests for the ListCasesTool.
"""

import pytest
from unittest.mock import AsyncMock
import httpx
from qradar_mcp.tools.forensics.list_cases import ListCasesTool


class TestListCasesToolMetadata:
    """Tests for ListCasesTool metadata properties."""

    def test_tool_name(self):
        """Test that tool has correct name."""
        tool = ListCasesTool()
        assert tool.name == "list_cases"

    def test_tool_description(self):
        """Test that tool has correct description."""
        tool = ListCasesTool()
        assert "List forensic investigation cases" in tool.description
        assert "Forensics" in tool.description

    def test_input_schema_structure(self):
        """Test that input schema has correct structure."""
        tool = ListCasesTool()
        schema = tool.input_schema

        assert schema["type"] == "object"
        assert "properties" in schema

    def test_input_schema_optional_fields(self):
        """Test all fields are optional."""
        tool = ListCasesTool()
        schema = tool.input_schema

        # All parameters should be optional
        assert "required" not in schema
        assert len(schema.get("required", [])) == 0

        # Check expected properties exist
        expected_props = ["filter", "fields", "limit", "offset"]
        for prop in expected_props:
            assert prop in schema["properties"]

    def test_to_mcp_tool_definition(self):
        """Test converting tool to MCP definition."""
        tool = ListCasesTool()
        definition = tool.to_mcp_tool_definition()

        assert definition["name"] == "list_cases"
        assert "List forensic investigation cases" in definition["description"]
        assert "inputSchema" in definition


class TestListCasesToolExecution:
    """Tests for ListCasesTool execute method."""

    @pytest.fixture
    def sample_cases(self):
        """Sample forensic case data."""
        return [
            {
                "id": 1,
                "name": "Malware Investigation",
                "status": "OPEN",
                "assignee": "analyst1",
                "created_time": 1234567890000,
                "modified_time": 1234567900000
            },
            {
                "id": 2,
                "name": "Data Exfiltration",
                "status": "CLOSED",
                "assignee": "analyst2",
                "created_time": 1234567800000,
                "modified_time": 1234567950000
            }
        ]

    @pytest.mark.asyncio
    async def test_execute_with_pagination(self, sample_cases):
        """Test executing tool with limit and offset."""
        # Setup mock
        mock_response = httpx.Response(
            200,
            json=sample_cases,
            request=httpx.Request("GET", "http://test")
        )

        tool = ListCasesTool()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"limit": 20, "offset": 0})

        # Verify Range header was set
        call_args = tool.client.get.call_args
        assert 'headers' in call_args[1]
        assert 'Range' in call_args[1]['headers']
        assert call_args[1]['headers']['Range'] == "items=0-19"

    @pytest.mark.asyncio
    async def test_execute_with_empty_result(self):
        """Test executing tool with empty result."""
        # Setup mock
        mock_response = httpx.Response(
            200,
            json=[],
            request=httpx.Request("GET", "http://test")
        )

        tool = ListCasesTool()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({})

        # Verify MCP result structure
        assert "content" in result
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"
        assert "[]" in result["content"][0]["text"]

class TestListCasesToolErrorHandling:
    """Tests for ListCasesTool error handling."""

    @pytest.mark.asyncio
    async def test_forensics_module_not_available(self):
        """Test handling when Forensics module is not available (404)."""
        # Setup mock to raise 404 HTTPStatusError
        mock_response = httpx.Response(
            404,
            request=httpx.Request("GET", "http://test")
        )
        http_error = httpx.HTTPStatusError("404 Not Found", request=mock_response.request, response=mock_response)

        # Execute
        tool = ListCasesTool()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(side_effect=http_error)

        result = await tool.execute({})

        # Verify error response
        assert result["isError"] is True
        assert "Error executing list_cases: 404 Not Found" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_permission_denied_error(self):
        """Test handling when user lacks permissions (403)."""
        # Setup mock to raise 403 HTTPStatusError
        mock_response = httpx.Response(
            403,
            request=httpx.Request("GET", "http://test")
        )
        http_error = httpx.HTTPStatusError("403 Forbidden", request=mock_response.request, response=mock_response)

        # Execute
        tool = ListCasesTool()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(side_effect=http_error)

        result = await tool.execute({})

        # Verify error response
        assert result["isError"] is True
        assert "Error executing list_cases: 403 Forbidden" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_api_error_handling(self):
        """Test handling of general API errors."""
        # Setup mock to raise error
        tool = ListCasesTool()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(side_effect=RuntimeError("API connection failed"))

        # Execute
        result = await tool.execute({})

        # Verify error response
        assert result["isError"] is True
        assert "tool execution failed: api connection failed" == result["content"][0]["text"].lower()
        assert "API connection failed" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_value_error_handling(self):
        """Test handling of ValueError."""
        # Setup mock to raise ValueError
        tool = ListCasesTool()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(side_effect=ValueError("Invalid parameter"))

        # Execute
        result = await tool.execute({})

        # Verify error response
        assert result["isError"] is True
        assert "tool execution failed: invalid parameter" == result["content"][0]["text"].lower()