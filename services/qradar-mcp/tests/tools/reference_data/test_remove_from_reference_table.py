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
Tests for RemoveFromReferenceTable
"""

import pytest
from unittest.mock import AsyncMock
import httpx
from qradar_mcp.tools.reference_data.remove_from_reference_table import RemoveFromReferenceTable


class TestRemoveFromReferenceTableMetadata:
    """Test RemoveFromReferenceTable metadata properties."""

    def test_tool_name(self):
        """Test tool name is correct."""
        tool = RemoveFromReferenceTable()
        assert tool.name == "remove_from_reference_table"

    def test_tool_description(self):
        """Test tool has a description."""
        tool = RemoveFromReferenceTable()
        assert tool.description
        assert "remove" in tool.description.lower()
        assert "table" in tool.description.lower()

    def test_input_schema_structure(self):
        """Test input schema has required structure."""
        tool = RemoveFromReferenceTable()
        schema = tool.input_schema

        assert "type" in schema
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "required" in schema

    def test_input_schema_required_fields(self):
        """Test required fields in schema."""
        tool = RemoveFromReferenceTable()
        schema = tool.input_schema

        assert "name" in schema["required"]
        assert "outer_key" in schema["required"]
        assert "inner_key" in schema["required"]
        assert "value" in schema["required"]


class TestRemoveFromReferenceTableExecution:
    """Test RemoveFromReferenceTable execution."""

    @pytest.fixture
    def sample_updated_table(self):
        """Sample updated reference table data."""
        return {
            "name": "ip_port_services",
            "element_type": "ALN",
            "number_of_elements": 99
        }

    @pytest.mark.asyncio
    async def test_execute_basic_request(self, sample_updated_table):
        """Test basic execution."""
        # Setup mock
        mock_response = httpx.Response(200, json=sample_updated_table, request=httpx.Request("DELETE", "http://test"))

        # Execute
        tool = RemoveFromReferenceTable()
        tool.client = AsyncMock()
        tool.client.delete = AsyncMock(return_value=mock_response)
        result = await tool.execute({
            "name": "ip_port_services",
            "outer_key": "192.168.1.1",
            "inner_key": "80",
            "value": "HTTP"
        })

        # Verify
        assert "isError" not in result
        assert "content" in result
        tool.client.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_missing_required_fields(self):
        """Test execution fails when required fields are missing."""
        tool = RemoveFromReferenceTable()

        # Missing name
        result = await tool.execute({"outer_key": "test", "inner_key": "test", "value": "test"})
        assert result["isError"] is True

        # Missing outer_key
        result = await tool.execute({"name": "test", "inner_key": "test", "value": "test"})
        assert result["isError"] is True

        # Missing inner_key
        result = await tool.execute({"name": "test", "outer_key": "test", "value": "test"})
        assert result["isError"] is True

        # Missing value
        result = await tool.execute({"name": "test", "outer_key": "test", "inner_key": "test"})
        assert result["isError"] is True


class TestRemoveFromReferenceTableErrorHandling:
    """Test RemoveFromReferenceTable error handling."""

    @pytest.mark.asyncio
    async def test_execute_api_error(self):
        """Test handling of API errors."""
        # Execute
        tool = RemoveFromReferenceTable()
        tool.client = AsyncMock()
        tool.client.delete = AsyncMock(side_effect=RuntimeError("API connection failed"))
        result = await tool.execute({
            "name": "test_table",
            "outer_key": "test_outer",
            "inner_key": "test_inner",
            "value": "test_value"
        })

        # Verify error response
        assert result["isError"] is True

    @pytest.mark.asyncio
    async def test_execute_not_found_error(self):
        """Test handling of not found errors."""
        # Execute
        tool = RemoveFromReferenceTable()
        tool.client = AsyncMock()
        tool.client.delete = AsyncMock(side_effect=RuntimeError("404: Not found"))
        result = await tool.execute({
            "name": "test_table",
            "outer_key": "nonexistent_outer",
            "inner_key": "nonexistent_inner",
            "value": "test_value"
        })

        # Verify error response
        assert result["isError"] is True
