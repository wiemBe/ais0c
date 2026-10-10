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
Tests for CreateReferenceTable
"""

import pytest
import httpx
from unittest.mock import AsyncMock
from qradar_mcp.tools.reference_data.create_reference_table import CreateReferenceTable


class TestCreateReferenceTableMetadata:
    """Test CreateReferenceTable metadata properties."""

    def test_tool_name(self):
        """Test tool name is correct."""
        tool = CreateReferenceTable()
        assert tool.name == "create_reference_table"

    def test_tool_description(self):
        """Test tool has a description."""
        tool = CreateReferenceTable()
        assert tool.description
        assert "create" in tool.description.lower()
        assert "table" in tool.description.lower()

    def test_input_schema_structure(self):
        """Test input schema has required structure."""
        tool = CreateReferenceTable()
        schema = tool.input_schema

        assert "type" in schema
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "required" in schema

    def test_input_schema_required_fields(self):
        """Test required fields in schema."""
        tool = CreateReferenceTable()
        schema = tool.input_schema

        assert "name" in schema["required"]
        assert "element_type" in schema["required"]
        # outer_key_label and key_name_types are optional
        assert len(schema["required"]) == 2


class TestCreateReferenceTableExecution:
    """Test CreateReferenceTable execution."""

    @pytest.fixture
    def sample_created_table(self):
        """Sample created reference table data."""
        return {
            "name": "ip_port_services",
            "element_type": "ALN",
            "outer_key_label": "IP Address",
            "key_name_types": [{"key_name": "port", "element_type": "PORT"}],
            "number_of_elements": 0,
            "creation_time": 1640000000000
        }

    @pytest.mark.asyncio
    async def test_execute_minimal_request(self, sample_created_table):
        """Test basic execution with only required fields."""
        # Setup mock
        mock_response = httpx.Response(
            200,
            json=sample_created_table,
            request=httpx.Request("POST", "http://test")
        )

        # Execute
        tool = CreateReferenceTable()
        tool.client = AsyncMock()
        tool.client.post = AsyncMock(return_value=mock_response)
        result = await tool.execute({
            "name": "ip_port_services",
            "element_type": "ALN",
            "outer_key_label": "IP Address",
            "key_name_types": '[{"key_name": "port", "element_type": "PORT"}]'
        })

        # Verify
        assert "isError" not in result
        assert "content" in result
        tool.client.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_with_optional_fields(self, sample_created_table):
        """Test execution with optional fields."""
        # Setup mock
        mock_response = httpx.Response(
            200,
            json=sample_created_table,
            request=httpx.Request("POST", "http://test")
        )

        # Execute
        tool = CreateReferenceTable()
        tool.client = AsyncMock()
        tool.client.post = AsyncMock(return_value=mock_response)
        result = await tool.execute({
            "name": "ip_port_services",
            "element_type": "ALN",
            "outer_key_label": "IP Address",
            "key_name_types": '[{"key_name": "port", "element_type": "PORT"}]',
            "description": "IP and port to service mappings",
            "timeout_type": "FIRST_SEEN",
            "time_to_live": 3600
        })

        # Verify
        assert "isError" not in result

    @pytest.mark.asyncio
    async def test_execute_missing_required_fields(self):
        """Test execution fails when required fields are missing."""
        tool = CreateReferenceTable()

        # Missing name
        result = await tool.execute({
            "element_type": "ALN",
            "outer_key_label": "IP",
            "key_name_types": "[]"
        })
        assert result["isError"] is True


class TestCreateReferenceTableErrorHandling:
    """Test CreateReferenceTable error handling."""

    @pytest.mark.asyncio
    async def test_execute_api_error(self):
        """Test handling of API errors."""
        # Execute
        tool = CreateReferenceTable()
        tool.client = AsyncMock()
        tool.client.post = AsyncMock(side_effect=RuntimeError("API connection failed"))
        result = await tool.execute({
            "name": "test_table",
            "element_type": "ALN",
            "outer_key_label": "IP",
            "key_name_types": '[{"key_name": "port", "element_type": "PORT"}]'
        })

        # Verify error response
        assert result["isError"] is True

    @pytest.mark.asyncio
    async def test_execute_http_error(self):
        """Test handling of HTTP errors."""
        # Setup mock to raise HTTPError
        mock_response = httpx.Response(
            500,
            request=httpx.Request("POST", "http://test")
        )

        # Execute
        tool = CreateReferenceTable()
        tool.client = AsyncMock()
        tool.client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError("500 Server Error", request=mock_response.request, response=mock_response)
        )
        result = await tool.execute({
            "name": "test_table",
            "element_type": "ALN",
            "outer_key_label": "IP",
            "key_name_types": '[{"key_name": "port", "element_type": "PORT"}]'
        })

        # Verify error response
        assert result["isError"] is True
        assert "Error executing create_reference_table: 500 Server Error" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_execute_value_error(self):
        """Test handling of ValueError."""
        # Execute
        tool = CreateReferenceTable()
        tool.client = AsyncMock()
        tool.client.post = AsyncMock(side_effect=ValueError("Invalid JSON format"))
        result = await tool.execute({
            "name": "test_table",
            "element_type": "ALN",
            "outer_key_label": "IP",
            "key_name_types": '[{"key_name": "port", "element_type": "PORT"}]'
        })

        # Verify error response
        assert result["isError"] is True
        assert "tool execution failed:" in result["content"][0]["text"].lower()


class TestCreateReferenceTableIntegration:
    """Integration tests for CreateReferenceTable."""

    @pytest.mark.asyncio
    async def test_create_different_element_types(self):
        """Test creating tables with different element types."""
        element_types = ["IP", "ALN", "NUM"]

        for element_type in element_types:
            # Setup mock
            mock_response = httpx.Response(
                200,
                json={
                    "name": f"test_{element_type.lower()}",
                    "element_type": element_type,
                    "number_of_elements": 0
                },
                request=httpx.Request("POST", "http://test")
            )

            # Execute
            tool = CreateReferenceTable()
            tool.client = AsyncMock()
            tool.client.post = AsyncMock(return_value=mock_response)
            result = await tool.execute({
                "name": f"test_{element_type.lower()}",
                "element_type": element_type,
                "outer_key_label": "Key",
                "key_name_types": '[{"key_name": "inner", "element_type": "ALN"}]'
            })

            # Verify
            assert "isError" not in result
