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
Tests for RemoveFromReferenceMap
"""

import pytest
from unittest.mock import AsyncMock
import httpx
from qradar_mcp.tools.reference_data.remove_from_reference_map import RemoveFromReferenceMap


class TestRemoveFromReferenceMapMetadata:
    """Test RemoveFromReferenceMap metadata properties."""

    def test_tool_name(self):
        """Test tool name is correct."""
        tool = RemoveFromReferenceMap()
        assert tool.name == "remove_from_reference_map"

    def test_tool_description(self):
        """Test tool has a description."""
        tool = RemoveFromReferenceMap()
        assert tool.description
        assert "remove" in tool.description.lower()
        assert "map" in tool.description.lower()

    def test_input_schema_structure(self):
        """Test input schema has required structure."""
        tool = RemoveFromReferenceMap()
        schema = tool.input_schema

        assert "type" in schema
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "required" in schema

    def test_input_schema_required_fields(self):
        """Test required fields in schema."""
        tool = RemoveFromReferenceMap()
        schema = tool.input_schema

        assert "name" in schema["required"]
        assert "key" in schema["required"]
        assert "value" in schema["required"]


class TestRemoveFromReferenceMapExecution:
    """Test RemoveFromReferenceMap execution."""

    @pytest.fixture
    def sample_updated_map(self):
        """Sample updated reference map data."""
        return {
            "name": "ip_country_map",
            "element_type": "ALN",
            "number_of_elements": 99
        }

    @pytest.mark.asyncio
    async def test_execute_basic_request(self, sample_updated_map):
        """Test basic execution."""
        # Setup mock
        mock_response = httpx.Response(200, json=sample_updated_map, request=httpx.Request("DELETE", "http://test"))

        # Execute
        tool = RemoveFromReferenceMap()
        tool.client = AsyncMock()
        tool.client.delete = AsyncMock(return_value=mock_response)
        result = await tool.execute({
            "name": "ip_country_map",
            "key": "192.168.1.1",
            "value": "USA"
        })

        # Verify
        assert "isError" not in result
        assert "content" in result
        tool.client.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_missing_required_fields(self):
        """Test execution fails when required fields are missing."""
        tool = RemoveFromReferenceMap()

        # Missing name
        result = await tool.execute({"key": "test", "value": "test"})
        assert result["isError"] is True

        # Missing key
        result = await tool.execute({"name": "test", "value": "test"})
        assert result["isError"] is True

        # Missing value
        result = await tool.execute({"name": "test", "key": "test"})
        assert result["isError"] is True


class TestRemoveFromReferenceMapErrorHandling:
    """Test RemoveFromReferenceMap error handling."""

    @pytest.mark.asyncio
    async def test_execute_api_error(self):
        """Test handling of API errors."""
        # Execute
        tool = RemoveFromReferenceMap()
        tool.client = AsyncMock()
        tool.client.delete = AsyncMock(side_effect=RuntimeError("API connection failed"))
        result = await tool.execute({
            "name": "test_map",
            "key": "test_key",
            "value": "test_value"
        })

        # Verify error response
        assert result["isError"] is True

    @pytest.mark.asyncio
    async def test_execute_not_found_error(self):
        """Test handling of not found errors."""
        # Execute
        tool = RemoveFromReferenceMap()
        tool.client = AsyncMock()
        tool.client.delete = AsyncMock(side_effect=RuntimeError("404: Not found"))
        result = await tool.execute({
            "name": "test_map",
            "key": "nonexistent_key",
            "value": "test_value"
        })

        # Verify error response
        assert result["isError"] is True
