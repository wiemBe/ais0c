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


"""Tests for geolocate_ip tool."""

from unittest.mock import AsyncMock
import pytest
import httpx
from qradar_mcp.tools.services.geolocate_ip import GeolocateIpTool


class TestGeolocateIpMetadata:
    """Test tool metadata."""

    def test_tool_name(self):
        """Test tool name is correct."""
        tool = GeolocateIpTool()
        assert tool.name == "geolocate_ip"

    def test_tool_description(self):
        """Test tool has description."""
        tool = GeolocateIpTool()
        assert len(tool.description) > 0
        assert "geographic location" in tool.description.lower() or "geoip" in tool.description.lower()

    def test_input_schema(self):
        """Test input schema is valid."""
        tool = GeolocateIpTool()
        schema = tool.input_schema
        assert "properties" in schema
        assert "ip_address" in schema["properties"]
        assert schema["properties"]["ip_address"]["type"] == "string"
        assert "ip_address" in schema["required"]


class TestGeolocateIpExecution:
    """Test tool execution."""

    @pytest.mark.asyncio
    async def test_successful_geolocation_external_ip(self):
        """Test successful geolocation of external IP."""
        mock_request = httpx.Request("GET", "http://test.com")
        mock_response = httpx.Response(
            200,
            json=[{
                "ip_address": "8.8.8.8",
                "city": {"name": "Mountain View", "geo_id": 5375480},
                "physical_country": {"name": "United States", "iso_code": "US"},
                "continent": {"name": "North America"},
                "location": {"latitude": 37.386, "longitude": -122.0838, "timezone": "America/Los_Angeles"},
                "traits": {
                    "internet_service_provider": "Google LLC",
                    "autonomous_system_number": 15169,
                    "organization": "Google LLC"
                },
                "is_local": False
            }],
            request=mock_request
        )

        tool = GeolocateIpTool()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"ip_address": "8.8.8.8"})

        assert result["content"][0]["type"] == "text"
        text = result["content"][0]["text"]
        assert "8.8.8.8" in text
        assert "Mountain View" in text
        assert "United States" in text
        assert "Google LLC" in text
        assert "External IP" in text

    @pytest.mark.asyncio
    async def test_successful_geolocation_local_ip(self):
        """Test successful geolocation of local IP."""
        mock_request = httpx.Request("GET", "http://test.com")
        mock_response = httpx.Response(
            200,
            json=[{
                "ip_address": "192.168.1.100",
                "is_local": True,
                "network": "Corporate LAN",
                "domain_id": 0
            }],
            request=mock_request
        )

        tool = GeolocateIpTool()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"ip_address": "192.168.1.100"})

        assert result["content"][0]["type"] == "text"
        text = result["content"][0]["text"]
        assert "192.168.1.100" in text
        assert "Local IP" in text
        assert "Corporate LAN" in text

    @pytest.mark.asyncio
    async def test_geolocation_with_fields_parameter(self):
        """Test geolocation with fields parameter."""
        mock_request = httpx.Request("GET", "http://test.com")
        mock_response = httpx.Response(
            200,
            json=[{
                "ip_address": "8.8.8.8",
                "physical_country": {"name": "United States", "iso_code": "US"}
            }],
            request=mock_request
        )

        tool = GeolocateIpTool()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({
            "ip_address": "8.8.8.8",
            "fields": "ip_address,physical_country"
        })

        tool.client.get.assert_called_once()
        call_args = tool.client.get.call_args
        assert call_args[1]["params"]["fields"] == "ip_address,physical_country"

        assert result["content"][0]["type"] == "text"


class TestGeolocateIpValidation:
    """Test parameter validation."""

    @pytest.mark.asyncio
    async def test_missing_ip_address(self):
        """Test error when ip_address is missing."""
        tool = GeolocateIpTool()
        result = await tool.execute({})

        assert result.get("isError") is True
        assert "ip_address is required" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_empty_ip_address(self):
        """Test error when ip_address is empty."""
        tool = GeolocateIpTool()
        result = await tool.execute({"ip_address": ""})

        assert result.get("isError") is True
        assert "ip_address is required" in result["content"][0]["text"]


class TestGeolocateIpErrorHandling:
    """Test error handling."""

    @pytest.mark.asyncio
    async def test_http_404_error(self):
        """Test handling of 404 error (IP not found)."""
        mock_request = httpx.Request("GET", "http://test.com")
        mock_response = httpx.Response(
            404,
            text="IP address not found in database",
            request=mock_request
        )

        tool = GeolocateIpTool()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "IP address not found",
                request=mock_request,
                response=mock_response
            )
        )

        result = await tool.execute({"ip_address": "1.2.3.4"})

        assert result.get("isError") is True
        assert "Tool execution failed:" in result["content"][0]["text"] or "Error executing geolocate_ip:" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_http_422_error(self):
        """Test handling of 422 error (invalid IP format)."""
        mock_request = httpx.Request("GET", "http://test.com")
        mock_response = httpx.Response(
            422,
            text="Invalid IP address format",
            request=mock_request
        )

        tool = GeolocateIpTool()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Invalid IP address format",
                request=mock_request,
                response=mock_response
            )
        )

        result = await tool.execute({"ip_address": "invalid.ip"})

        assert result.get("isError") is True
        assert "422" in result["content"][0]["text"] or "invalid" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_http_500_error(self):
        """Test handling of 500 error."""
        mock_request = httpx.Request("GET", "http://test.com")
        mock_response = httpx.Response(
            500,
            text="Internal server error",
            request=mock_request
        )

        tool = GeolocateIpTool()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "Internal server error",
                request=mock_request,
                response=mock_response
            )
        )

        result = await tool.execute({"ip_address": "8.8.8.8"})

        assert result.get("isError") is True

    @pytest.mark.asyncio
    async def test_empty_response(self):
        """Test handling of empty response (no data found)."""
        mock_request = httpx.Request("GET", "http://test.com")
        mock_response = httpx.Response(
            200,
            json=[],
            request=mock_request
        )

        tool = GeolocateIpTool()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"ip_address": "1.2.3.4"})

        assert result.get("isError") is True
        assert "No geolocation data found" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_value_error(self):
        """Test handling of ValueError."""
        tool = GeolocateIpTool()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(side_effect=ValueError("Invalid value"))

        result = await tool.execute({"ip_address": "8.8.8.8"})

        assert result.get("isError") is True
        assert "Tool execution failed:" in result["content"][0]["text"] or "Error executing geolocate_ip:" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_runtime_error(self):
        """Test handling of RuntimeError."""
        tool = GeolocateIpTool()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(side_effect=RuntimeError("Runtime error"))

        result = await tool.execute({"ip_address": "8.8.8.8"})

        assert result.get("isError") is True
        assert "Tool execution failed:" in result["content"][0]["text"] or "Error executing geolocate_ip:" in result["content"][0]["text"]
