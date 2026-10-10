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
#
# Modified in the ais0c platform fork (T-040): the tool takes no filter argument.


"""
Tests for GetReferenceTable
"""

import pytest
from unittest.mock import AsyncMock
import httpx
from qradar_mcp.tools.reference_data.get_reference_table import GetReferenceTable


class TestGetReferenceTableMetadata:
    """Test GetReferenceTable metadata properties."""

    def test_tool_name(self):
        """Test tool name is correct."""
        tool = GetReferenceTable()
        assert tool.name == "get_reference_table"

    def test_tool_description(self):
        """Test tool has a description."""
        tool = GetReferenceTable()
        assert tool.description
        assert "get" in tool.description.lower() or "retrieve" in tool.description.lower()
        assert "table" in tool.description.lower()

    def test_input_schema_structure(self):
        """Test input schema has required structure."""
        tool = GetReferenceTable()
        schema = tool.input_schema

        assert "type" in schema
        assert schema["type"] == "object"
        assert "properties" in schema
        assert "required" in schema

    def test_input_schema_required_fields(self):
        """Test name is required in schema."""
        tool = GetReferenceTable()
        schema = tool.input_schema

        assert "name" in schema["required"]
        assert "name" in schema["properties"]


class TestGetReferenceTableExecution:
    """Test GetReferenceTable execution."""

    @pytest.fixture
    def sample_table_data(self):
        """Sample reference table data."""
        return {
            "name": "ip_port_services",
            "element_type": "ALN",
            "outer_key_label": "IP Address",
            "number_of_elements": 100,
            "data": {
                "192.168.1.1": {
                    "80": {"value": "HTTP", "source": "admin"},
                    "443": {"value": "HTTPS", "source": "admin"}
                }
            }
        }

    @pytest.mark.asyncio
    async def test_execute_basic_request(self, sample_table_data):
        """Test basic execution."""
        # Setup mock
        mock_response = httpx.Response(200, json=sample_table_data, request=httpx.Request("GET", "http://test"))

        # Execute
        tool = GetReferenceTable()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)
        result = await tool.execute({"name": "ip_port_services"})

        # Verify
        assert "isError" not in result
        assert "content" in result
        tool.client.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_execute_does_not_send_a_filter(self, sample_table_data):
        """The tool has no filter argument (fork, T-040); a stray one never reaches QRadar."""
        # Setup mock
        mock_response = httpx.Response(200, json=sample_table_data, request=httpx.Request("GET", "http://test"))

        # Execute
        tool = GetReferenceTable()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)
        result = await tool.execute({
            "name": "ip_port_services",
            "filter": "value='HTTP'"
        })

        # Verify
        assert "filter" not in tool.input_schema["properties"]
        assert "isError" not in result
        params = tool.client.get.call_args[1]["params"]
        assert "filter" not in params

    @pytest.mark.asyncio
    async def test_execute_missing_name(self):
        """Test execution fails when name is missing."""
        tool = GetReferenceTable()
        result = await tool.execute({})

        assert result["isError"] is True


class TestGetReferenceTableErrorHandling:
    """Test GetReferenceTable error handling."""

    @pytest.mark.asyncio
    async def test_execute_api_error(self):
        """Test handling of API errors."""
        # Execute
        tool = GetReferenceTable()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(side_effect=RuntimeError("API connection failed"))
        result = await tool.execute({"name": "test_table"})

        # Verify error response
        assert result["isError"] is True

    @pytest.mark.asyncio
    async def test_execute_not_found_error(self):
        """Test handling of not found errors."""
        # Execute
        tool = GetReferenceTable()
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(side_effect=RuntimeError("404: Not found"))
        result = await tool.execute({"name": "nonexistent_table"})

        # Verify error response
        assert result["isError"] is True
