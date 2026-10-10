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
Tests for DeleteReferenceMap
"""

import pytest
import httpx
from unittest.mock import AsyncMock
from qradar_mcp.tools.reference_data.delete_reference_map import DeleteReferenceMap


class TestDeleteReferenceMapMetadata:
    """Test DeleteReferenceMap metadata properties."""

    def test_tool_name(self):
        """Test tool name is correct."""
        tool = DeleteReferenceMap()
        assert tool.name == "delete_reference_map"

    def test_tool_description(self):
        """Test tool has a description."""
        tool = DeleteReferenceMap()
        assert tool.description
        assert "delete" in tool.description.lower()
        assert "map" in tool.description.lower()

    def test_input_schema_structure(self):
        """Test input schema has required structure."""
        tool = DeleteReferenceMap()
        schema = tool.input_schema

        assert "type" in schema
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "required" in schema

    def test_input_schema_required_fields(self):
        """Test name is required in schema."""
        tool = DeleteReferenceMap()
        schema = tool.input_schema

        assert "name" in schema["required"]
        assert "name" in schema["properties"]


class TestDeleteReferenceMapExecution:
    """Test DeleteReferenceMap execution."""

    @pytest.fixture
    def sample_delete_response(self):
        """Sample delete response data."""
        return {
            "status": "COMPLETED",
            "message": "Map deleted successfully"
        }

    @pytest.mark.asyncio
    async def test_execute_basic_request(self, sample_delete_response):
        """Test basic execution."""
        # Setup mock
        mock_response = httpx.Response(
            200,
            json=sample_delete_response,
            request=httpx.Request("DELETE", "http://test")
        )

        # Execute
        tool = DeleteReferenceMap()
        tool.client = AsyncMock()
        tool.client.delete = AsyncMock(return_value=mock_response)
        result = await tool.execute({"name": "test_map"})

        # Verify
        assert "isError" not in result
        assert "content" in result
        tool.client.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_with_purge_only(self, sample_delete_response):
        """Test execution with purge_only parameter."""
        # Setup mock
        mock_response = httpx.Response(
            200,
            json=sample_delete_response,
            request=httpx.Request("DELETE", "http://test")
        )

        # Execute
        tool = DeleteReferenceMap()
        tool.client = AsyncMock()
        tool.client.delete = AsyncMock(return_value=mock_response)
        result = await tool.execute({
            "name": "test_map",
            "purge_only": True
        })

        # Verify
        assert "isError" not in result
        params = tool.client.delete.call_args[1]["params"]
        assert "purge_only" in params

    @pytest.mark.asyncio
    async def test_execute_missing_name(self):
        """Test execution fails when name is missing."""
        tool = DeleteReferenceMap()
        result = await tool.execute({})

        assert result["isError"] is True


class TestDeleteReferenceMapErrorHandling:
    """Test DeleteReferenceMap error handling."""

    @pytest.mark.asyncio
    async def test_execute_api_error(self):
        """Test handling of API errors."""
        # Execute
        tool = DeleteReferenceMap()
        tool.client = AsyncMock()
        tool.client.delete = AsyncMock(side_effect=RuntimeError("API connection failed"))
        result = await tool.execute({"name": "test_map"})

        # Verify error response
        assert result["isError"] is True

    @pytest.mark.asyncio
    async def test_execute_not_found_error(self):
        """Test handling of not found errors."""
        # Execute
        tool = DeleteReferenceMap()
        tool.client = AsyncMock()
        tool.client.delete = AsyncMock(side_effect=RuntimeError("404: Not found"))
        result = await tool.execute({"name": "nonexistent_map"})

        # Verify error response
        assert result["isError"] is True
