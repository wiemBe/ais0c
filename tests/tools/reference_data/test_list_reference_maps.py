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
Tests for ListReferenceMaps
"""

import pytest
from unittest.mock import AsyncMock
import httpx
from qradar_mcp.tools.reference_data.list_reference_maps import ListReferenceMaps


class TestListReferenceMapsMetadata:
    """Test ListReferenceMaps metadata properties."""

    def test_tool_name(self):
        """Test tool name is correct."""
        tool = ListReferenceMaps()
        assert tool.name == "list_reference_maps"

    def test_tool_description(self):
        """Test tool has a description."""
        tool = ListReferenceMaps()
        assert tool.description
        assert "list" in tool.description.lower()
        assert "map" in tool.description.lower()

    def test_input_schema_structure(self):
        """Test input schema has required structure."""
        tool = ListReferenceMaps()
        schema = tool.input_schema

        assert "type" in schema
        assert schema["type"] == "object"
        assert "properties" in schema

    def test_input_schema_optional_fields(self):
        """Test all fields are optional."""
        tool = ListReferenceMaps()
        schema = tool.input_schema

        optional_fields = [
            "filter", "sort", "limit", "offset",
            "fields", "format_output"
        ]

        for field in optional_fields:
            assert field in schema["properties"]


class TestListReferenceMapsExecution:
    """Test ListReferenceMaps execution."""

    @pytest.fixture
    def sample_maps_list(self):
        """Sample reference maps list data."""
        return [
            {
                "name": "ip_country_map",
                "element_type": "ALN",
                "number_of_elements": 100,
                "creation_time": 1640000000000
            },
            {
                "name": "threat_actor_map",
                "element_type": "ALN",
                "number_of_elements": 50,
                "creation_time": 1640100000000
            }
        ]

    @pytest.mark.asyncio
    async def test_execute_no_parameters(self, sample_maps_list):
        """Test execution with no parameters."""
        # Setup mock
        mock_response = httpx.Response(200, json=sample_maps_list, request=httpx.Request("GET", "http://test"))

        # Execute
        tool = ListReferenceMaps()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)
        result = await tool.execute({})

        # Verify
        assert "isError" not in result
        assert "content" in result
        tool.client.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_with_filter(self, sample_maps_list):
        """Test execution with filter parameter."""
        # Setup mock
        mock_response = httpx.Response(200, json=[sample_maps_list[0]], request=httpx.Request("GET", "http://test"))

        # Execute
        tool = ListReferenceMaps()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)
        result = await tool.execute({
            "filter": "element_type='ALN'"
        })

        # Verify
        assert "isError" not in result
        params = tool.client.get.call_args[1]["params"]
        assert "filter" in params

    @pytest.mark.asyncio
    async def test_execute_with_pagination(self, sample_maps_list):
        """Test execution with pagination."""
        # Setup mock
        mock_response = httpx.Response(200, json=sample_maps_list, request=httpx.Request("GET", "http://test"))

        # Execute
        tool = ListReferenceMaps()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)
        result = await tool.execute({
            "limit": 10,
            "offset": 0
        })

        # Verify
        assert "isError" not in result
        headers = tool.client.get.call_args[1]["headers"]
        assert "Range" in headers

    @pytest.mark.asyncio
    async def test_execute_with_sort(self, sample_maps_list):
        """Test execution with sort parameter."""
        # Setup mock
        mock_response = httpx.Response(200, json=sample_maps_list, request=httpx.Request("GET", "http://test"))

        # Execute
        tool = ListReferenceMaps()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)
        result = await tool.execute({
            "sort": "+name"
        })

        # Verify
        assert "isError" not in result
        params = tool.client.get.call_args[1]["params"]
        assert "sort" in params


class TestListReferenceMapsErrorHandling:
    """Test ListReferenceMaps error handling."""

    @pytest.mark.asyncio
    async def test_execute_api_error(self):
        """Test handling of API errors."""
        # Execute
        tool = ListReferenceMaps()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(side_effect=RuntimeError("API connection failed"))
        result = await tool.execute({})

        assert result["isError"] is True

    @pytest.mark.asyncio
    async def test_execute_http_error(self):
        """Test handling of HTTP errors."""
        # Setup mock to raise HTTPStatusError
        mock_request = httpx.Request("GET", "http://test")
        mock_response = httpx.Response(500, request=mock_request)
        http_error = httpx.HTTPStatusError("500 Server Error", request=mock_request, response=mock_response)

        # Execute
        tool = ListReferenceMaps()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(side_effect=http_error)
        result = await tool.execute({})

        # Verify error response
        assert result["isError"] is True
        assert "Error executing list_reference_maps: 500 Server Error" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_execute_value_error(self):
        """Test handling of ValueError."""
        # Execute
        tool = ListReferenceMaps()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(side_effect=ValueError("Invalid parameter"))
        result = await tool.execute({})

        # Verify error response
        assert result["isError"] is True
        assert "tool execution failed:" in result["content"][0]["text"].lower()
