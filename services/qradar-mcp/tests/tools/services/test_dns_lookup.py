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


"""Tests for dns_lookup tool."""

from unittest.mock import AsyncMock
import pytest
import httpx
from qradar_mcp.tools.services.dns_lookup import DnsLookupTool


class TestDnsLookupMetadata:
    """Test tool metadata."""

    def test_tool_name(self):
        """Test tool name is correct."""
        tool = DnsLookupTool()
        assert tool.name == "dns_lookup"

    def test_tool_description(self):
        """Test tool has description."""
        tool = DnsLookupTool()
        assert len(tool.description) > 0
        assert "DNS" in tool.description or "dns" in tool.description.lower()

    def test_input_schema(self):
        """Test input schema is valid."""
        tool = DnsLookupTool()
        schema = tool.input_schema
        assert "properties" in schema
        assert "ip_address" in schema["properties"]
        assert schema["properties"]["ip_address"]["type"] == "string"
        assert "ip_address" in schema["required"]


class TestDnsLookupExecution:
    """Test tool execution."""

    @pytest.mark.asyncio
    async def test_successful_dns_lookup_initiation(self):
        """Test successful DNS lookup initiation."""
        mock_request = httpx.Request("POST", "http://test.com")
        mock_response = httpx.Response(
            200,
            json={
                "id": 42,
                "ip": "8.8.8.8",
                "status": "QUEUED",
                "message": None
            },
            request=mock_request
        )

        tool = DnsLookupTool()
        tool.client = AsyncMock()
        tool.client.post = AsyncMock(return_value=mock_response)

        result = await tool.execute({"ip_address": "8.8.8.8"})

        assert result["content"][0]["type"] == "text"
        text = result["content"][0]["text"]
        assert "DNS Lookup Initiated" in text
        assert "42" in text
        assert "8.8.8.8" in text
        assert "QUEUED" in text
        assert "get_dns_result" in text

    @pytest.mark.asyncio
    async def test_dns_lookup_with_fields_parameter(self):
        """Test DNS lookup with fields parameter."""
        mock_request = httpx.Request("POST", "http://test.com")
        mock_response = httpx.Response(
            200,
            json={
                "id": 42,
                "ip": "8.8.8.8",
                "status": "QUEUED"
            },
            request=mock_request
        )

        tool = DnsLookupTool()
        tool.client = AsyncMock()
        tool.client.post = AsyncMock(return_value=mock_response)

        result = await tool.execute({
            "ip_address": "8.8.8.8",
            "fields": "id,status"
        })

        tool.client.post.assert_called_once()
        call_args = tool.client.post.call_args
        assert call_args[1]["params"]["IP"] == "8.8.8.8"
        assert call_args[1]["params"]["fields"] == "id,status"

        assert result["content"][0]["type"] == "text"

    @pytest.mark.asyncio
    async def test_dns_lookup_initializing_status(self):
        """Test DNS lookup with INITIALIZING status."""
        mock_request = httpx.Request("POST", "http://test.com")
        mock_response = httpx.Response(
            200,
            json={
                "id": 43,
                "ip": "1.1.1.1",
                "status": "INITIALIZING",
                "message": None
            },
            request=mock_request
        )

        tool = DnsLookupTool()
        tool.client = AsyncMock()
        tool.client.post = AsyncMock(return_value=mock_response)

        result = await tool.execute({"ip_address": "1.1.1.1"})

        assert result["content"][0]["type"] == "text"
        text = result["content"][0]["text"]
        assert "43" in text
        assert "INITIALIZING" in text


class TestDnsLookupValidation:
    """Test parameter validation."""

    @pytest.mark.asyncio
    async def test_missing_ip_address(self):
        """Test error when ip_address is missing."""
        tool = DnsLookupTool()
        result = await tool.execute({})

        assert result.get("isError") is True
        assert "ip_address is required" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_empty_ip_address(self):
        """Test error when ip_address is empty."""
        tool = DnsLookupTool()
        result = await tool.execute({"ip_address": ""})

        assert result.get("isError") is True
        assert "ip_address is required" in result["content"][0]["text"]


class TestDnsLookupErrorHandling:
    """Test error handling."""

    @pytest.mark.asyncio
    async def test_http_500_error(self):
        """Test handling of 500 error."""
        mock_request = httpx.Request("POST", "http://test.com")
        mock_response = httpx.Response(
            500,
            text="Internal server error during DNS lookup creation",
            request=mock_request
        )

        tool = DnsLookupTool()
        tool.client = AsyncMock()
        tool.client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Internal server error",
                request=mock_request,
                response=mock_response
            )
        )

        result = await tool.execute({"ip_address": "8.8.8.8"})

        assert result.get("isError") is True

    @pytest.mark.asyncio
    async def test_value_error(self):
        """Test handling of ValueError."""
        tool = DnsLookupTool()
        tool.client = AsyncMock()
        tool.client.post = AsyncMock(side_effect=ValueError("Invalid value"))

        result = await tool.execute({"ip_address": "8.8.8.8"})

        assert result.get("isError") is True
        assert "Tool execution failed:" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_runtime_error(self):
        """Test handling of RuntimeError."""
        tool = DnsLookupTool()
        tool.client = AsyncMock()
        tool.client.post = AsyncMock(side_effect=RuntimeError("Runtime error"))

        result = await tool.execute({"ip_address": "8.8.8.8"})

        assert result.get("isError") is True
        assert "Tool execution failed:" in result["content"][0]["text"]
