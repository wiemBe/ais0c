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


"""Tests for get_whois_result tool."""

from unittest.mock import AsyncMock
import pytest
import httpx
from qradar_mcp.tools.services.get_whois_result import GetWhoisResultTool


class TestGetWhoisResultMetadata:
    """Test tool metadata."""

    def test_tool_name(self):
        """Test tool name is correct."""
        tool = GetWhoisResultTool()
        assert tool.name == "get_whois_result"

    def test_tool_description(self):
        """Test tool has description."""
        tool = GetWhoisResultTool()
        assert len(tool.description) > 0
        assert "WHOIS" in tool.description or "whois" in tool.description.lower()

    def test_input_schema(self):
        """Test input schema is valid."""
        tool = GetWhoisResultTool()
        schema = tool.input_schema
        assert "properties" in schema
        assert "task_id" in schema["properties"]
        assert schema["properties"]["task_id"]["type"] == "integer"
        assert "task_id" in schema["required"]


class TestGetWhoisResultExecution:
    """Test tool execution."""

    @pytest.mark.asyncio
    async def test_completed_whois_lookup(self):
        """Test retrieving completed WHOIS lookup."""
        mock_request = httpx.Request("GET", "http://test.com")
        mock_response = httpx.Response(
            200,
            json={
                "id": 43,
                "ip": "8.8.8.8",
                "status": "COMPLETED",
                "message": "NetRange: 8.8.8.0 - 8.8.8.255\nCIDR: 8.8.8.0/24\nNetName: LVLT-GOGL-8-8-8\nOrganization: Google LLC (GOGL)"
            },
            request=mock_request
        )

        tool = GetWhoisResultTool()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"task_id": 43})

        assert result["content"][0]["type"] == "text"
        text = result["content"][0]["text"]
        assert "WHOIS Lookup Results" in text
        assert "43" in text
        assert "8.8.8.8" in text
        assert "COMPLETED" in text
        assert "Google LLC" in text

    @pytest.mark.asyncio
    async def test_queued_whois_lookup(self):
        """Test retrieving queued WHOIS lookup."""
        mock_request = httpx.Request("GET", "http://test.com")
        mock_response = httpx.Response(
            200,
            json={
                "id": 43,
                "ip": "8.8.8.8",
                "status": "QUEUED",
                "message": None
            },
            request=mock_request
        )

        tool = GetWhoisResultTool()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"task_id": 43})

        assert result["content"][0]["type"] == "text"
        text = result["content"][0]["text"]
        assert "WHOIS Lookup Status" in text
        assert "QUEUED" in text
        assert "in progress" in text

    @pytest.mark.asyncio
    async def test_processing_whois_lookup(self):
        """Test retrieving processing WHOIS lookup."""
        mock_request = httpx.Request("GET", "http://test.com")
        mock_response = httpx.Response(
            200,
            json={
                "id": 43,
                "ip": "8.8.8.8",
                "status": "PROCESSING",
                "message": None
            },
            request=mock_request
        )

        tool = GetWhoisResultTool()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"task_id": 43})

        assert result["content"][0]["type"] == "text"
        text = result["content"][0]["text"]
        assert "WHOIS Lookup Status" in text
        assert "PROCESSING" in text
        assert "in progress" in text

    @pytest.mark.asyncio
    async def test_failed_whois_lookup(self):
        """Test retrieving failed WHOIS lookup."""
        mock_request = httpx.Request("GET", "http://test.com")
        mock_response = httpx.Response(
            200,
            json={
                "id": 43,
                "ip": "8.8.8.8",
                "status": "EXCEPTION",
                "message": "WHOIS server timeout"
            },
            request=mock_request
        )

        tool = GetWhoisResultTool()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"task_id": 43})

        assert result["content"][0]["type"] == "text"
        text = result["content"][0]["text"]
        assert "WHOIS Lookup Failed" in text
        assert "EXCEPTION" in text
        assert "WHOIS server timeout" in text

    @pytest.mark.asyncio
    async def test_with_fields_parameter(self):
        """Test with fields parameter."""
        mock_request = httpx.Request("GET", "http://test.com")
        mock_response = httpx.Response(
            200,
            json={
                "id": 43,
                "status": "COMPLETED"
            },
            request=mock_request
        )

        tool = GetWhoisResultTool()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({
            "task_id": 43,
            "fields": "id,status"
        })

        tool.client.get.assert_called_once()
        call_args = tool.client.get.call_args
        assert call_args[1]["params"]["fields"] == "id,status"


class TestGetWhoisResultValidation:
    """Test parameter validation."""

    @pytest.mark.asyncio
    async def test_missing_task_id(self):
        """Test error when task_id is missing."""
        tool = GetWhoisResultTool()
        result = await tool.execute({})

        assert result.get("isError") is True
        assert "task_id is required" in result["content"][0]["text"]


class TestGetWhoisResultErrorHandling:
    """Test error handling."""

    @pytest.mark.asyncio
    async def test_http_404_error(self):
        """Test handling of 404 error (task not found)."""
        mock_request = httpx.Request("GET", "http://test.com")
        mock_response = httpx.Response(
            404,
            text="WHOIS lookup task not found",
            request=mock_request
        )

        tool = GetWhoisResultTool()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "WHOIS lookup task not found",
                request=mock_request,
                response=mock_response
            )
        )

        result = await tool.execute({"task_id": 999})

        assert result.get("isError") is True

    @pytest.mark.asyncio
    async def test_http_500_error(self):
        """Test handling of 500 error."""
        mock_request = httpx.Request("GET", "http://test.com")
        mock_response = httpx.Response(
            500,
            text="Internal server error",
            request=mock_request
        )

        tool = GetWhoisResultTool()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Internal server error",
                request=mock_request,
                response=mock_response
            )
        )

        result = await tool.execute({"task_id": 43})

        assert result.get("isError") is True

    @pytest.mark.asyncio
    async def test_value_error(self):
        """Test handling of ValueError."""
        tool = GetWhoisResultTool()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(side_effect=ValueError("Invalid value"))

        result = await tool.execute({"task_id": 43})

        assert result.get("isError") is True
        assert "Tool execution failed:" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_runtime_error(self):
        """Test handling of RuntimeError."""
        tool = GetWhoisResultTool()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(side_effect=RuntimeError("Runtime error"))

        result = await tool.execute({"task_id": 43})

        assert result.get("isError") is True
        assert "Tool execution failed:" in result["content"][0]["text"]
