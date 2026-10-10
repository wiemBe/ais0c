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
Tests for Delete Ariel Search Tool
"""

import json
import pytest
from unittest.mock import AsyncMock
import httpx
from qradar_mcp.tools.ariel.delete_ariel_search import DeleteArielSearchTool


@pytest.fixture
def tool():
    """Create a DeleteArielSearchTool instance for testing."""
    return DeleteArielSearchTool()


@pytest.fixture
def mock_delete_response():
    """Mock successful delete response."""
    return {
        "cursor_id": "s123",
        "search_id": "s123",
        "status": "COMPLETED",
        "query_string": "SELECT sourceip FROM events LAST 1 HOURS",
        "progress": 100,
        "record_count": 1500,
        "save_results": False
    }


class TestDeleteArielSearchToolProperties:
    """Test tool properties."""

    def test_name(self, tool):
        """Test tool name property."""
        assert tool.name == "delete_ariel_search"

    def test_description(self, tool):
        """Test tool description property."""
        description = tool.description
        assert "delete" in description.lower()
        assert "ariel" in description.lower()
        assert "search" in description.lower()

    def test_input_schema(self, tool):
        """Test input schema structure."""
        schema = tool.input_schema
        assert "properties" in schema
        assert "search_id" in schema["properties"]
        assert "search_id" in schema["required"]


class TestDeleteArielSearchBasic:
    """Test basic search deletion."""

    @pytest.mark.asyncio
    async def test_delete_search_success(self, tool, mock_delete_response):
        """Test successfully deleting a search."""
        mock_response = httpx.Response(
            status_code=202,
            json=mock_delete_response,
            request=httpx.Request("DELETE", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.delete = AsyncMock(return_value=mock_response)

        result = await tool.execute({"search_id": "s123"})

        assert result.get("isError") is not True
        content = json.loads(result["content"][0]["text"])
        assert content["search_id"] == "s123"
        assert content["status"] == "COMPLETED"

        tool.client.delete.assert_called_once_with(
            api_path="ariel/searches/s123"
        )

    @pytest.mark.asyncio
    async def test_delete_in_progress_search(self, tool):
        """Test deleting a search that is still in progress."""
        mock_response_data = {
            "search_id": "s456",
            "status": "EXECUTE",
            "progress": 45,
            "record_count": 500
        }

        mock_response = httpx.Response(
            status_code=202,
            json=mock_response_data,
            request=httpx.Request("DELETE", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.delete = AsyncMock(return_value=mock_response)

        result = await tool.execute({"search_id": "s456"})

        assert result.get("isError") is not True
        content = json.loads(result["content"][0]["text"])
        assert content["search_id"] == "s456"
        assert content["status"] == "EXECUTE"

    @pytest.mark.asyncio
    async def test_delete_saved_search(self, tool):
        """Test deleting a search with saved results."""
        mock_response_data = {
            "search_id": "s789",
            "status": "COMPLETED",
            "save_results": True,
            "record_count": 2000
        }

        mock_response = httpx.Response(
            status_code=202,
            json=mock_response_data,
            request=httpx.Request("DELETE", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.delete = AsyncMock(return_value=mock_response)

        result = await tool.execute({"search_id": "s789"})

        assert result.get("isError") is not True
        content = json.loads(result["content"][0]["text"])
        assert content["save_results"] is True


class TestDeleteArielSearchValidation:
    """Test input validation."""

    @pytest.mark.asyncio
    async def test_missing_search_id(self, tool):
        """Test error when search_id is not provided."""
        result = await tool.execute({})

        assert result["isError"] is True
        assert "search_id is required" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_empty_search_id(self, tool):
        """Test error when search_id is empty string."""
        result = await tool.execute({"search_id": ""})

        assert result["isError"] is True
        assert "search_id is required" in result["content"][0]["text"].lower()


class TestDeleteArielSearchErrorHandling:
    """Test error handling for various API responses."""

    @pytest.mark.asyncio
    async def test_search_not_found(self, tool):
        """Test handling of search not found (404)."""
        mock_response = httpx.Response(
            status_code=404,
            text="Search not found",
            request=httpx.Request("DELETE", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.delete = AsyncMock(return_value=mock_response)

        result = await tool.execute({"search_id": "s999"})

        assert result["isError"] is True
        assert "not found" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_invalid_parameters(self, tool):
        """Test handling of invalid parameters (422)."""
        mock_response = httpx.Response(
            status_code=422,
            text="Invalid search_id format",
            request=httpx.Request("DELETE", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.delete = AsyncMock(return_value=mock_response)

        result = await tool.execute({"search_id": "invalid"})

        assert result["isError"] is True
        assert "invalid request parameters" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_ariel_server_unavailable(self, tool):
        """Test handling of Ariel server unavailable (503)."""
        mock_response = httpx.Response(
            status_code=503,
            text="Service temporarily unavailable",
            request=httpx.Request("DELETE", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.delete = AsyncMock(return_value=mock_response)

        result = await tool.execute({"search_id": "s123"})

        assert result["isError"] is True
        assert "unavailable" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_internal_server_error(self, tool):
        """Test handling of internal server error (500)."""
        mock_response = httpx.Response(
            status_code=500,
            text="Internal server error",
            request=httpx.Request("DELETE", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.delete = AsyncMock(return_value=mock_response)

        result = await tool.execute({"search_id": "s123"})

        assert result["isError"] is True
        assert "500" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_unexpected_status_code(self, tool):
        """Test handling of unexpected status code."""
        mock_response = httpx.Response(
            status_code=418,
            text="I'm a teapot",
            request=httpx.Request("DELETE", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.delete = AsyncMock(return_value=mock_response)

        result = await tool.execute({"search_id": "s123"})

        assert result["isError"] is True
        assert "418" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_client_exception(self, tool):
        """Test handling of client exceptions."""
        tool.client = AsyncMock()
        tool.client.delete = AsyncMock(side_effect=RuntimeError("Connection failed"))

        result = await tool.execute({"search_id": "s123"})

        assert result["isError"] is True
        assert "connection failed" in result["content"][0]["text"].lower()


class TestDeleteArielSearchResponseFormat:
    """Test response format and content."""

    @pytest.mark.asyncio
    async def test_response_contains_search_id(self, tool, mock_delete_response):
        """Test that response contains search_id."""
        mock_response = httpx.Response(
            status_code=202,
            json=mock_delete_response,
            request=httpx.Request("DELETE", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.delete = AsyncMock(return_value=mock_response)

        result = await tool.execute({"search_id": "s123"})

        content = json.loads(result["content"][0]["text"])
        assert "search_id" in content
        assert content["search_id"] == "s123"

    @pytest.mark.asyncio
    async def test_response_json_formatted(self, tool, mock_delete_response):
        """Test that response is properly formatted JSON."""
        mock_response = httpx.Response(
            status_code=202,
            json=mock_delete_response,
            request=httpx.Request("DELETE", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.delete = AsyncMock(return_value=mock_response)

        result = await tool.execute({"search_id": "s123"})

        # Should be valid JSON
        content_text = result["content"][0]["text"]
        parsed = json.loads(content_text)
        assert isinstance(parsed, dict)

        # Should be indented (formatted)
        assert "\n" in content_text
        assert "  " in content_text
