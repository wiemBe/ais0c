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
Tests for Get Ariel Search Results Tool
"""

import json
import pytest
from unittest.mock import AsyncMock
import httpx
from qradar_mcp.tools.ariel.get_ariel_search_results import GetArielSearchResultsTool


@pytest.fixture
def tool():
    """Create a GetArielSearchResultsTool instance for testing."""
    return GetArielSearchResultsTool()


@pytest.fixture
def mock_search_results_events():
    """Mock search results for events."""
    return {
        "events": [
            {
                "sourceip": "10.100.65.20",
                "destinationip": "192.168.1.1",
                "starttime": 1467049610018,
                "qid": 10034,
                "sourceport": 13675
            },
            {
                "sourceip": "10.100.100.121",
                "destinationip": "192.168.1.2",
                "starttime": 1467049610019,
                "qid": 20034,
                "sourceport": 80
            },
            {
                "sourceip": "10.100.100.122",
                "destinationip": "192.168.1.3",
                "starttime": 1467049610020,
                "qid": 30034,
                "sourceport": 443
            }
        ]
    }


@pytest.fixture
def mock_search_results_flows():
    """Mock search results for flows."""
    return {
        "flows": [
            {
                "sourceip": "10.0.0.1",
                "destinationip": "10.0.0.2",
                "sourcebytes": 1024,
                "destinationbytes": 2048
            },
            {
                "sourceip": "10.0.0.3",
                "destinationip": "10.0.0.4",
                "sourcebytes": 512,
                "destinationbytes": 1024
            }
        ]
    }


class TestGetArielSearchResultsToolProperties:
    """Test tool properties."""

    def test_name(self, tool):
        """Test tool name property."""
        assert tool.name == "get_ariel_search_results"

    def test_description(self, tool):
        """Test tool description property."""
        description = tool.description
        assert "results" in description.lower()
        assert "ariel" in description.lower()
        assert "search" in description.lower()

    def test_input_schema(self, tool):
        """Test input schema structure."""
        schema = tool.input_schema
        assert "properties" in schema
        assert "search_id" in schema["properties"]
        assert "start" in schema["properties"]
        assert "limit" in schema["properties"]
        assert "search_id" in schema["required"]


class TestGetArielSearchResultsBasic:
    """Test basic search results retrieval."""

    @pytest.mark.asyncio
    async def test_get_results_events(self, tool, mock_search_results_events):
        """Test retrieving event search results."""
        mock_response = httpx.Response(
            status_code=200,
            json=mock_search_results_events,
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"search_id": "s123"})

        assert result.get("isError") is not True
        content = json.loads(result["content"][0]["text"])
        assert "events" in content
        assert len(content["events"]) == 3
        assert content["events"][0]["sourceip"] == "10.100.65.20"

        tool.client.get.assert_called_once_with(
            api_path="ariel/searches/s123/results",
            headers={}
        )

    @pytest.mark.asyncio
    async def test_get_results_flows(self, tool, mock_search_results_flows):
        """Test retrieving flow search results."""
        mock_response = httpx.Response(
            status_code=200,
            json=mock_search_results_flows,
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"search_id": "s456"})

        assert result.get("isError") is not True
        content = json.loads(result["content"][0]["text"])
        assert "flows" in content
        assert len(content["flows"]) == 2

    @pytest.mark.asyncio
    async def test_get_results_empty(self, tool):
        """Test retrieving empty search results."""
        mock_response = httpx.Response(
            status_code=200,
            json={"events": []},
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"search_id": "s789"})

        assert result.get("isError") is not True
        content = json.loads(result["content"][0]["text"])
        assert "events" in content
        assert len(content["events"]) == 0


class TestGetArielSearchResultsPagination:
    """Test search results pagination."""

    @pytest.mark.asyncio
    async def test_get_results_with_limit(self, tool, mock_search_results_events):
        """Test retrieving results with limit parameter."""
        mock_response = httpx.Response(
            status_code=200,
            json=mock_search_results_events,
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({
            "search_id": "s123",
            "limit": 10
        })

        assert result.get("isError") is not True
        tool.client.get.assert_called_once_with(
            api_path="ariel/searches/s123/results",
            headers={"Range": "items=0-9"}
        )

    @pytest.mark.asyncio
    async def test_get_results_with_start_and_limit(self, tool, mock_search_results_events):
        """Test retrieving results with start and limit parameters."""
        mock_response = httpx.Response(
            status_code=200,
            json=mock_search_results_events,
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({
            "search_id": "s123",
            "start": 10,
            "limit": 20
        })

        assert result.get("isError") is not True
        tool.client.get.assert_called_once_with(
            api_path="ariel/searches/s123/results",
            headers={"Range": "items=10-29"}
        )

    @pytest.mark.asyncio
    async def test_get_results_with_start_only(self, tool, mock_search_results_events):
        """Test retrieving results with only start parameter."""
        mock_response = httpx.Response(
            status_code=200,
            json=mock_search_results_events,
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({
            "search_id": "s123",
            "start": 50
        })

        assert result.get("isError") is not True
        tool.client.get.assert_called_once_with(
            api_path="ariel/searches/s123/results",
            headers={"Range": "items=50-"}
        )

    @pytest.mark.asyncio
    async def test_get_results_with_zero_start(self, tool, mock_search_results_events):
        """Test retrieving results with start=0."""
        mock_response = httpx.Response(
            status_code=200,
            json=mock_search_results_events,
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({
            "search_id": "s123",
            "start": 0,
            "limit": 5
        })

        assert result.get("isError") is not True
        tool.client.get.assert_called_once_with(
            api_path="ariel/searches/s123/results",
            headers={"Range": "items=0-4"}
        )


class TestGetArielSearchResultsValidation:
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


class TestGetArielSearchResultsErrorHandling:
    """Test error handling for various API responses."""

    @pytest.mark.asyncio
    async def test_search_not_found(self, tool):
        """Test handling of search not found (404)."""
        mock_response = httpx.Response(
            status_code=404,
            text="Search not found",
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"search_id": "s999"})

        assert result["isError"] is True
        assert "error" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_results_not_ready(self, tool):
        """Test handling of results not ready (404 with different message)."""
        mock_response = httpx.Response(
            status_code=404,
            text="Results not available",
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"search_id": "s123"})

        assert result["isError"] is True
        assert "error" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_invalid_parameters(self, tool):
        """Test handling of invalid parameters (422)."""
        mock_response = httpx.Response(
            status_code=422,
            text="Invalid range",
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"search_id": "s123", "start": -1})

        assert result["isError"] is True
        assert "error" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_ariel_server_unavailable(self, tool):
        """Test handling of Ariel server unavailable (503)."""
        mock_response = httpx.Response(
            status_code=503,
            text="Service temporarily unavailable",
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"search_id": "s123"})

        assert result["isError"] is True
        assert "error" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_internal_server_error(self, tool):
        """Test handling of internal server error (500)."""
        mock_response = httpx.Response(
            status_code=500,
            text="Internal server error",
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"search_id": "s123"})

        assert result["isError"] is True
        assert "error" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_client_exception(self, tool):
        """Test handling of client exceptions."""
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(side_effect=RuntimeError("Connection failed"))

        result = await tool.execute({"search_id": "s123"})

        assert result["isError"] is True
        assert "connection failed" in result["content"][0]["text"].lower()


class TestGetArielSearchResultsResponseFormat:
    """Test response format and content."""

    @pytest.mark.asyncio
    async def test_response_contains_results(self, tool, mock_search_results_events):
        """Test that response contains results."""
        mock_response = httpx.Response(
            status_code=200,
            json=mock_search_results_events,
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"search_id": "s123"})

        content = json.loads(result["content"][0]["text"])
        assert "events" in content
        assert isinstance(content["events"], list)

    @pytest.mark.asyncio
    async def test_response_json_formatted(self, tool, mock_search_results_events):
        """Test that response is properly formatted JSON."""
        mock_response = httpx.Response(
            status_code=200,
            json=mock_search_results_events,
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"search_id": "s123"})

        # Should be valid JSON
        content_text = result["content"][0]["text"]
        parsed = json.loads(content_text)
        assert isinstance(parsed, dict)

        # Should be indented (formatted)
        assert "\n" in content_text
        assert "  " in content_text

    @pytest.mark.asyncio
    async def test_response_with_list_results(self, tool):
        """Test response when results are a list instead of dict."""
        mock_results = [
            {"field1": "value1"},
            {"field1": "value2"}
        ]

        mock_response = httpx.Response(
            status_code=200,
            json=mock_results,
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"search_id": "s123"})

        assert result.get("isError") is not True
        content = json.loads(result["content"][0]["text"])
        assert isinstance(content, list)
        assert len(content) == 2


class TestGetArielSearchResultsCountResults:
    """Test the _count_results helper method."""

    def test_count_results_with_dict(self, tool):
        """Test counting results when data is a dict with lists."""
        count = tool._count_results({"events": [1, 2, 3], "flows": [4, 5]})
        assert count == 5

    def test_count_results_with_list(self, tool):
        """Test counting results when data is a list."""
        count = tool._count_results([1, 2, 3, 4])
        assert count == 4

    def test_count_results_with_other_type(self, tool):
        """Test counting results when data is neither dict nor list."""
        count = tool._count_results("string")
        assert count == 0

    def test_count_results_with_empty_dict(self, tool):
        """Test counting results with empty dict."""
        count = tool._count_results({})
        assert count == 0

    def test_count_results_with_empty_list(self, tool):
        """Test counting results with empty list."""
        count = tool._count_results([])
        assert count == 0


class TestGetArielSearchResultsHandleErrorResponse:
    """Test the _handle_error_response helper method."""

    def test_handle_404_not_found(self, tool):
        """Test handling 404 with 'not found' in text."""
        mock_response = httpx.Response(
            status_code=404,
            text="Search not found",
            request=httpx.Request("GET", "http://test")
        )

        with pytest.raises(RuntimeError, match="Search s123 not found"):
            tool._handle_error_response(mock_response, "s123")

    def test_handle_404_results_not_ready(self, tool):
        """Test handling 404 without 'not found' in text."""
        mock_response = httpx.Response(
            status_code=404,
            text="Results unavailable",
            request=httpx.Request("GET", "http://test")
        )

        with pytest.raises(RuntimeError, match="Search results not found"):
            tool._handle_error_response(mock_response, "s456")

    def test_handle_422_invalid_parameters(self, tool):
        """Test handling 422 invalid request."""
        mock_response = httpx.Response(
            status_code=422,
            text="Invalid range specified",
            request=httpx.Request("GET", "http://test")
        )

        with pytest.raises(RuntimeError, match="Invalid request parameters"):
            tool._handle_error_response(mock_response, "s789")

    def test_handle_503_service_unavailable(self, tool):
        """Test handling 503 service unavailable."""
        mock_response = httpx.Response(
            status_code=503,
            text="Service temporarily unavailable",
            request=httpx.Request("GET", "http://test")
        )

        with pytest.raises(RuntimeError, match="Ariel server temporarily unavailable"):
            tool._handle_error_response(mock_response, "s999")

    def test_handle_other_error_codes(self, tool):
        """Test handling other error codes."""
        mock_response = httpx.Response(
            status_code=500,
            text="Internal server error",
            request=httpx.Request("GET", "http://test")
        )

        with pytest.raises(RuntimeError, match="Failed to retrieve search results"):
            tool._handle_error_response(mock_response, "s111")
