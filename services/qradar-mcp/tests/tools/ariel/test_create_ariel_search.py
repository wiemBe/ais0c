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
Tests for Create Ariel Search Tool
"""

import json
import pytest
from unittest.mock import AsyncMock
import httpx
from qradar_mcp.tools.ariel.create_ariel_search import CreateArielSearchTool


@pytest.fixture
def tool():
    """Create a CreateArielSearchTool instance for testing."""
    return CreateArielSearchTool()


@pytest.fixture
def mock_search_response():
    """Mock successful search creation response."""
    return {
        "cursor_id": "s123",
        "search_id": "s123",
        "status": "WAIT",
        "query_string": "SELECT sourceip FROM events LAST 1 HOURS",
        "progress": 0,
        "record_count": 0,
        "save_results": False,
        "compressed_data_file_count": 0,
        "compressed_data_total_size": 0,
        "data_file_count": 0,
        "data_total_size": 0,
        "index_file_count": 0,
        "index_total_size": 0,
        "processed_record_count": 0,
        "error_messages": [],
        "desired_retention_time_msec": 86400000,
        "progress_details": [],
        "query_execution_time": 0,
        "subsearch_ids": []
    }


class TestCreateArielSearchToolProperties:
    """Test tool properties."""

    def test_name(self, tool):
        """Test tool name property."""
        assert tool.name == "create_ariel_search"

    def test_description(self, tool):
        """Test tool description property."""
        description = tool.description
        assert "asynchronous" in description.lower()
        assert "ariel" in description.lower()
        assert "search" in description.lower()

    def test_input_schema(self, tool):
        """Test input schema structure."""
        schema = tool.input_schema
        assert "properties" in schema
        assert "query_expression" in schema["properties"]
        assert "saved_search_id" in schema["properties"]


class TestCreateArielSearchWithQueryExpression:
    """Test creating searches with AQL query expressions."""

    @pytest.mark.asyncio
    async def test_create_search_with_simple_query(self, tool, mock_search_response):
        """Test creating a search with a simple AQL query."""
        mock_request = httpx.Request("POST", "http://test.com")
        mock_response = httpx.Response(201, json=mock_search_response, request=mock_request)

        tool.client = AsyncMock()
        tool.client.post = AsyncMock(return_value=mock_response)

        result = await tool.execute({
            "query_expression": "SELECT sourceip FROM events LAST 1 HOURS"
        })

        assert result.get("isError") is not True
        content = json.loads(result["content"][0]["text"])
        assert content["search_id"] == "s123"
        assert content["status"] == "WAIT"

        tool.client.post.assert_called_once_with(
            "ariel/searches",
            params={"query_expression": "SELECT sourceip FROM events LAST 1 HOURS"}
        )

    @pytest.mark.asyncio
    async def test_create_search_with_complex_query(self, tool, mock_search_response):
        """Test creating a search with a complex AQL query."""
        complex_query = (
            "SELECT sourceip, destinationip, qid FROM events "
            "WHERE sourceip IN ('10.0.0.1', '10.0.0.2') "
            "LAST 24 HOURS"
        )
        mock_search_response["query_string"] = complex_query

        mock_request = httpx.Request("POST", "http://test.com")
        mock_response = httpx.Response(201, json=mock_search_response, request=mock_request)

        tool.client = AsyncMock()
        tool.client.post = AsyncMock(return_value=mock_response)

        result = await tool.execute({"query_expression": complex_query})

        assert result.get("isError") is not True
        content = json.loads(result["content"][0]["text"])
        assert content["query_string"] == complex_query

    @pytest.mark.asyncio
    async def test_create_search_with_time_range(self, tool, mock_search_response):
        """Test creating a search with specific time range."""
        query = "SELECT * FROM events START '2024-01-01 00:00' STOP '2024-01-02 00:00'"

        mock_request = httpx.Request("POST", "http://test.com")
        mock_response = httpx.Response(201, json=mock_search_response, request=mock_request)

        tool.client = AsyncMock()
        tool.client.post = AsyncMock(return_value=mock_response)

        result = await tool.execute({"query_expression": query})

        assert result.get("isError") is not True
        tool.client.post.assert_called_once()


class TestCreateArielSearchWithSavedSearchId:
    """Test creating searches with saved search IDs."""

    @pytest.mark.asyncio
    async def test_create_search_with_saved_search_id(self, tool, mock_search_response):
        """Test creating a search using a saved search ID."""
        mock_request = httpx.Request("POST", "http://test.com")
        mock_response = httpx.Response(201, json=mock_search_response, request=mock_request)

        tool.client = AsyncMock()
        tool.client.post = AsyncMock(return_value=mock_response)

        result = await tool.execute({"saved_search_id": 42})

        assert result.get("isError") is not True
        content = json.loads(result["content"][0]["text"])
        assert content["search_id"] == "s123"

        tool.client.post.assert_called_once_with(
            "ariel/searches",
            params={"saved_search_id": 42}
        )

    @pytest.mark.asyncio
    async def test_create_search_with_zero_saved_search_id(self, tool, mock_search_response):
        """Test creating a search with saved_search_id of 0."""
        mock_request = httpx.Request("POST", "http://test.com")
        mock_response = httpx.Response(201, json=mock_search_response, request=mock_request)

        tool.client = AsyncMock()
        tool.client.post = AsyncMock(return_value=mock_response)

        result = await tool.execute({"saved_search_id": 0})

        assert result.get("isError") is not True
        tool.client.post.assert_called_once_with(
            "ariel/searches",
            params={"saved_search_id": 0}
        )


class TestCreateArielSearchValidation:
    """Test input validation."""

    @pytest.mark.asyncio
    async def test_missing_both_parameters(self, tool):
        """Test error when neither parameter is provided."""
        result = await tool.execute({})

        assert result["isError"] is True
        assert "either query_expression or saved_search_id" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_both_parameters_provided(self, tool):
        """Test error when both parameters are provided."""
        result = await tool.execute({
            "query_expression": "SELECT * FROM events",
            "saved_search_id": 42
        })

        assert result["isError"] is True
        assert "mutually exclusive" in result["content"][0]["text"].lower()


class TestCreateArielSearchErrorHandling:
    """Test error handling for various API responses."""

    @pytest.mark.asyncio
    async def test_invalid_aql_syntax(self, tool):
        """Test handling of invalid AQL syntax (422)."""
        mock_request = httpx.Request("POST", "http://test.com")
        mock_response = httpx.Response(422, text="Invalid AQL: Syntax error near 'FORM'", request=mock_request)

        tool.client = AsyncMock()
        tool.client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError("422 Client Error: Unprocessable Entity", request=mock_request, response=mock_response)
        )

        result = await tool.execute({"query_expression": "SELECT * FORM events"})

        assert result["isError"] is True
        assert "error" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_search_id_conflict(self, tool):
        """Test handling of search ID conflict (409)."""
        mock_request = httpx.Request("POST", "http://test.com")
        mock_response = httpx.Response(409, text="Search ID already in use", request=mock_request)

        tool.client = AsyncMock()
        tool.client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError("409 Client Error: Conflict", request=mock_request, response=mock_response)
        )

        result = await tool.execute({
            "query_expression": "SELECT * FROM events INTO s123"
        })

        assert result["isError"] is True
        assert "error" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_saved_search_not_found(self, tool):
        """Test handling of saved search not found (404)."""
        mock_request = httpx.Request("POST", "http://test.com")
        mock_response = httpx.Response(404, text="Saved search not found", request=mock_request)

        tool.client = AsyncMock()
        tool.client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError("404 Client Error: Not Found", request=mock_request, response=mock_response)
        )

        result = await tool.execute({"saved_search_id": 999})

        assert result["isError"] is True
        assert "error" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_ariel_server_unavailable(self, tool):
        """Test handling of Ariel server unavailable (503)."""
        mock_request = httpx.Request("POST", "http://test.com")
        mock_response = httpx.Response(503, text="Service temporarily unavailable", request=mock_request)

        tool.client = AsyncMock()
        tool.client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError("503 Server Error: Service Unavailable", request=mock_request, response=mock_response)
        )

        result = await tool.execute({"query_expression": "SELECT * FROM events"})

        assert result["isError"] is True
        assert "error" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_internal_server_error(self, tool):
        """Test handling of internal server error (500)."""
        mock_request = httpx.Request("POST", "http://test.com")
        mock_response = httpx.Response(500, text="Internal server error", request=mock_request)

        tool.client = AsyncMock()
        tool.client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError("500 Server Error: Internal Server Error", request=mock_request, response=mock_response)
        )

        result = await tool.execute({"query_expression": "SELECT * FROM events"})

        assert result["isError"] is True
        assert "error" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_unexpected_status_code(self, tool):
        """Test handling of unexpected status code."""
        mock_request = httpx.Request("POST", "http://test.com")
        mock_response = httpx.Response(418, text="I'm a teapot", request=mock_request)

        tool.client = AsyncMock()
        tool.client.post = AsyncMock(
            side_effect=httpx.HTTPStatusError("418 Client Error: I'm a teapot", request=mock_request, response=mock_response)
        )

        result = await tool.execute({"query_expression": "SELECT * FROM events"})

        assert result["isError"] is True
        assert "error" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_client_exception(self, tool):
        """Test handling of client exceptions."""
        tool.client = AsyncMock()
        tool.client.post = AsyncMock(side_effect=RuntimeError("Connection failed"))

        result = await tool.execute({"query_expression": "SELECT * FROM events"})

        assert result["isError"] is True
        assert "connection failed" in result["content"][0]["text"].lower()


class TestCreateArielSearchResponseFormat:
    """Test response format and content."""

    @pytest.mark.asyncio
    async def test_response_contains_search_id(self, tool, mock_search_response):
        """Test that response contains search_id."""
        mock_request = httpx.Request("POST", "http://test.com")
        mock_response = httpx.Response(201, json=mock_search_response, request=mock_request)

        tool.client = AsyncMock()
        tool.client.post = AsyncMock(return_value=mock_response)

        result = await tool.execute({"query_expression": "SELECT * FROM events"})

        content = json.loads(result["content"][0]["text"])
        assert "search_id" in content
        assert content["search_id"] == "s123"

    @pytest.mark.asyncio
    async def test_response_contains_status(self, tool, mock_search_response):
        """Test that response contains status."""
        mock_request = httpx.Request("POST", "http://test.com")
        mock_response = httpx.Response(201, json=mock_search_response, request=mock_request)

        tool.client = AsyncMock()
        tool.client.post = AsyncMock(return_value=mock_response)

        result = await tool.execute({"query_expression": "SELECT * FROM events"})

        content = json.loads(result["content"][0]["text"])
        assert "status" in content
        assert content["status"] in ["WAIT", "EXECUTE", "SORTING", "COMPLETED", "CANCELED", "ERROR"]

    @pytest.mark.asyncio
    async def test_response_json_formatted(self, tool, mock_search_response):
        """Test that response is properly formatted JSON."""
        mock_request = httpx.Request("POST", "http://test.com")
        mock_response = httpx.Response(201, json=mock_search_response, request=mock_request)

        tool.client = AsyncMock()
        tool.client.post = AsyncMock(return_value=mock_response)

        result = await tool.execute({"query_expression": "SELECT * FROM events"})

        # Should be valid JSON
        content_text = result["content"][0]["text"]
        parsed = json.loads(content_text)
        assert isinstance(parsed, dict)

        # Should be indented (formatted)
        assert "\n" in content_text
        assert "  " in content_text
