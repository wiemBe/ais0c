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


"""Tests for get_dns_result tool."""

import json
from unittest.mock import Mock, AsyncMock, patch
import pytest
import httpx
from qradar_mcp.tools.services.get_dns_result import GetDnsResultTool


class TestGetDnsResultMetadata:
    """Test tool metadata."""

    def test_tool_name(self):
        """Test tool name is correct."""
        tool = GetDnsResultTool()
        assert tool.name == "get_dns_result"

    def test_tool_description(self):
        """Test tool has description."""
        tool = GetDnsResultTool()
        assert len(tool.description) > 0
        assert "DNS" in tool.description or "dns" in tool.description.lower()

    def test_input_schema(self):
        """Test input schema is valid."""
        tool = GetDnsResultTool()
        schema = tool.input_schema
        assert "properties" in schema
        assert "task_id" in schema["properties"]
        assert schema["properties"]["task_id"]["type"] == "integer"
        assert "task_id" in schema["required"]


class TestGetDnsResultExecution:
    """Test tool execution."""

    @pytest.mark.asyncio
    async def test_completed_dns_lookup(self):
        """Test retrieving completed DNS lookup."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "id": 42,
            "ip": "8.8.8.8",
            "status": "COMPLETED",
            "message": "google-public-dns-a.google.com"
        }
        mock_response.raise_for_status = Mock()

        tool = GetDnsResultTool()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"task_id": 42})

        assert result["content"][0]["type"] == "text"
        text = result["content"][0]["text"]
        assert "DNS Lookup Results" in text
        assert "42" in text
        assert "8.8.8.8" in text
        assert "COMPLETED" in text
        assert "google-public-dns-a.google.com" in text

    @pytest.mark.asyncio
    async def test_queued_dns_lookup(self):
        """Test retrieving queued DNS lookup."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "id": 42,
            "ip": "8.8.8.8",
            "status": "QUEUED",
            "message": None
        }
        mock_response.raise_for_status = Mock()

        tool = GetDnsResultTool()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"task_id": 42})

        assert result["content"][0]["type"] == "text"
        text = result["content"][0]["text"]
        assert "DNS Lookup Status" in text
        assert "QUEUED" in text
        assert "in progress" in text

    @pytest.mark.asyncio
    async def test_processing_dns_lookup(self):
        """Test retrieving processing DNS lookup."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "id": 42,
            "ip": "8.8.8.8",
            "status": "PROCESSING",
            "message": None
        }
        mock_response.raise_for_status = Mock()

        tool = GetDnsResultTool()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"task_id": 42})

        assert result["content"][0]["type"] == "text"
        text = result["content"][0]["text"]
        assert "DNS Lookup Status" in text
        assert "PROCESSING" in text
        assert "in progress" in text

    @pytest.mark.asyncio
    async def test_failed_dns_lookup(self):
        """Test retrieving failed DNS lookup."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "id": 42,
            "ip": "8.8.8.8",
            "status": "EXCEPTION",
            "message": "DNS server timeout"
        }
        mock_response.raise_for_status = Mock()

        tool = GetDnsResultTool()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"task_id": 42})

        assert result["content"][0]["type"] == "text"
        text = result["content"][0]["text"]
        assert "DNS Lookup Failed" in text
        assert "EXCEPTION" in text
        assert "DNS server timeout" in text

    @pytest.mark.asyncio
    async def test_with_fields_parameter(self):
        """Test with fields parameter."""
        mock_response = Mock()
        mock_response.json.return_value = {
            "id": 42,
            "status": "COMPLETED"
        }
        mock_response.raise_for_status = Mock()

        tool = GetDnsResultTool()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({
            "task_id": 42,
            "fields": "id,status"
        })

        tool.client.get.assert_called_once()
        call_args = tool.client.get.call_args
        assert call_args[1]["params"]["fields"] == "id,status"


class TestGetDnsResultValidation:
    """Test parameter validation."""

    @pytest.mark.asyncio
    async def test_missing_task_id(self):
        """Test error when task_id is missing."""
        tool = GetDnsResultTool()
        result = await tool.execute({})

        assert result.get("isError") is True
        assert "task_id is required" in result["content"][0]["text"]


class TestGetDnsResultErrorHandling:
    """Test error handling."""

    @pytest.mark.asyncio
    async def test_http_404_error(self):
        """Test handling of 404 error (task not found)."""
        mock_request = httpx.Request("GET", "http://test.com")
        mock_response = httpx.Response(404, text="DNS lookup task not found", request=mock_request)

        tool = GetDnsResultTool()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(side_effect=httpx.HTTPStatusError(
            "404 error", request=mock_request, response=mock_response
        ))

        result = await tool.execute({"task_id": 999})

        assert result.get("isError") is True

    @pytest.mark.asyncio
    async def test_http_500_error(self):
        """Test handling of 500 error."""
        mock_request = httpx.Request("GET", "http://test.com")
        mock_response = httpx.Response(500, text="Internal server error", request=mock_request)

        tool = GetDnsResultTool()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(side_effect=httpx.HTTPStatusError(
            "500 error", request=mock_request, response=mock_response
        ))

        result = await tool.execute({"task_id": 42})

        assert result.get("isError") is True

    @pytest.mark.asyncio
    async def test_value_error(self):
        """Test handling of ValueError."""
        tool = GetDnsResultTool()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(side_effect=ValueError("Invalid value"))

        result = await tool.execute({"task_id": 42})

        assert result.get("isError") is True
        assert "Tool execution failed:" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_runtime_error(self):
        """Test handling of RuntimeError."""
        tool = GetDnsResultTool()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(side_effect=RuntimeError("Runtime error"))

        result = await tool.execute({"task_id": 42})

        assert result.get("isError") is True
        assert "Tool execution failed:" in result["content"][0]["text"]
