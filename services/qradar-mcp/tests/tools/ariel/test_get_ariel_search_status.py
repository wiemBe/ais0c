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
Tests for Get Ariel Search Status Tool
"""

import json
import pytest
from unittest.mock import AsyncMock
import httpx
from qradar_mcp.tools.ariel.get_ariel_search_status import GetArielSearchStatusTool


@pytest.fixture
def tool():
    """Create a GetArielSearchStatusTool instance for testing."""
    return GetArielSearchStatusTool()


@pytest.fixture
def mock_search_status_wait():
    """Mock search status response in WAIT state."""
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


@pytest.fixture
def mock_search_status_completed():
    """Mock search status response in COMPLETED state."""
    return {
        "cursor_id": "s123",
        "search_id": "s123",
        "status": "COMPLETED",
        "query_string": "SELECT sourceip FROM events LAST 1 HOURS",
        "progress": 100,
        "record_count": 1500,
        "save_results": False,
        "compressed_data_file_count": 0,
        "compressed_data_total_size": 0,
        "data_file_count": 10,
        "data_total_size": 150000,
        "index_file_count": 2,
        "index_total_size": 5000,
        "processed_record_count": 1500,
        "error_messages": [],
        "desired_retention_time_msec": 86400000,
        "progress_details": [100, 100, 100, 100, 100],
        "query_execution_time": 2500,
        "subsearch_ids": []
    }


class TestGetArielSearchStatusToolProperties:
    """Test tool properties."""

    def test_name(self, tool):
        """Test tool name property."""
        assert tool.name == "get_ariel_search_status"

    def test_description(self, tool):
        """Test tool description property."""
        description = tool.description
        assert "status" in description.lower()
        assert "ariel" in description.lower()
        assert "search" in description.lower()

    def test_input_schema(self, tool):
        """Test input schema structure."""
        schema = tool.input_schema
        assert "properties" in schema
        assert "search_id" in schema["properties"]
        assert "wait_seconds" in schema["properties"]
        assert "search_id" in schema["required"]


class TestGetArielSearchStatusBasic:
    """Test basic search status retrieval."""

    @pytest.mark.asyncio
    async def test_get_status_wait_state(self, tool, mock_search_status_wait):
        """Test retrieving status for a search in WAIT state."""
        mock_response = httpx.Response(
            status_code=200,
            json=mock_search_status_wait,
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"search_id": "s123"})

        assert result.get("isError") is not True
        content = json.loads(result["content"][0]["text"])
        assert content["search_id"] == "s123"
        assert content["status"] == "WAIT"
        assert content["progress"] == 0

        tool.client.get.assert_called_once_with(
            api_path="ariel/searches/s123",
            headers={}
        )

    @pytest.mark.asyncio
    async def test_get_status_completed_state(self, tool, mock_search_status_completed):
        """Test retrieving status for a completed search."""
        mock_response = httpx.Response(
            status_code=200,
            json=mock_search_status_completed,
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"search_id": "s123"})

        assert result.get("isError") is not True
        content = json.loads(result["content"][0]["text"])
        assert content["status"] == "COMPLETED"
        assert content["progress"] == 100
        assert content["record_count"] == 1500

    @pytest.mark.asyncio
    async def test_get_status_execute_state(self, tool):
        """Test retrieving status for a search in EXECUTE state."""
        mock_status = {
            "search_id": "s456",
            "status": "EXECUTE",
            "progress": 45,
            "record_count": 500,
            "processed_record_count": 500
        }

        mock_response = httpx.Response(
            status_code=200,
            json=mock_status,
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"search_id": "s456"})

        assert result.get("isError") is not True
        content = json.loads(result["content"][0]["text"])
        assert content["status"] == "EXECUTE"
        assert content["progress"] == 45


class TestGetArielSearchStatusWithWait:
    """Test search status retrieval with wait parameter."""

    @pytest.mark.asyncio
    async def test_get_status_with_wait_completed(
        self, tool, mock_search_status_completed
    ):
        """Test retrieving status with wait parameter when search completes."""
        mock_response = httpx.Response(
            status_code=200,
            json=mock_search_status_completed,
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({
            "search_id": "s123",
            "wait_seconds": 30
        })

        assert result.get("isError") is not True
        content = json.loads(result["content"][0]["text"])
        assert content["status"] == "COMPLETED"

        tool.client.get.assert_called_once_with(
            api_path="ariel/searches/s123",
            headers={"Prefer": "wait=30"}
        )

    @pytest.mark.asyncio
    async def test_get_status_with_wait_timeout(self, tool, mock_search_status_wait):
        """Test retrieving status with wait parameter when timeout expires (206)."""
        mock_response = httpx.Response(
            status_code=206,
            json=mock_search_status_wait,
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({
            "search_id": "s123",
            "wait_seconds": 10
        })

        assert result.get("isError") is not True
        content = json.loads(result["content"][0]["text"])
        assert content["status"] == "WAIT"
        assert content["progress"] == 0

    @pytest.mark.asyncio
    async def test_get_status_with_zero_wait(self, tool, mock_search_status_wait):
        """Test retrieving status with wait_seconds=0."""
        mock_response = httpx.Response(
            status_code=200,
            json=mock_search_status_wait,
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({
            "search_id": "s123",
            "wait_seconds": 0
        })

        assert result.get("isError") is not True
        tool.client.get.assert_called_once_with(
            api_path="ariel/searches/s123",
            headers={"Prefer": "wait=0"}
        )

    @pytest.mark.asyncio
    async def test_get_status_with_max_wait(self, tool, mock_search_status_completed):
        """Test retrieving status with maximum wait_seconds."""
        mock_response = httpx.Response(
            status_code=200,
            json=mock_search_status_completed,
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({
            "search_id": "s123",
            "wait_seconds": 300
        })

        assert result.get("isError") is not True
        tool.client.get.assert_called_once_with(
            api_path="ariel/searches/s123",
            headers={"Prefer": "wait=300"}
        )


class TestGetArielSearchStatusValidation:
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


class TestGetArielSearchStatusErrorHandling:
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
        assert "not found" in result["content"][0]["text"].lower()

    @pytest.mark.asyncio
    async def test_invalid_parameters(self, tool):
        """Test handling of invalid parameters (422)."""
        mock_response = httpx.Response(
            status_code=422,
            text="Invalid search_id format",
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"search_id": "invalid"})

        assert result["isError"] is True
        assert "invalid request parameters" in result["content"][0]["text"].lower()

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
        assert "unavailable" in result["content"][0]["text"].lower()

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
        assert "500" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_client_exception(self, tool):
        """Test handling of client exceptions."""
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(side_effect=RuntimeError("Connection failed"))

        result = await tool.execute({"search_id": "s123"})

        assert result["isError"] is True
        assert "connection failed" in result["content"][0]["text"].lower()


class TestGetArielSearchStatusResponseFormat:
    """Test response format and content."""

    @pytest.mark.asyncio
    async def test_response_contains_search_id(self, tool, mock_search_status_wait):
        """Test that response contains search_id."""
        mock_response = httpx.Response(
            status_code=200,
            json=mock_search_status_wait,
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"search_id": "s123"})

        content = json.loads(result["content"][0]["text"])
        assert "search_id" in content
        assert content["search_id"] == "s123"

    @pytest.mark.asyncio
    async def test_response_contains_status(self, tool, mock_search_status_wait):
        """Test that response contains status field."""
        mock_response = httpx.Response(
            status_code=200,
            json=mock_search_status_wait,
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"search_id": "s123"})

        content = json.loads(result["content"][0]["text"])
        assert "status" in content
        assert content["status"] in ["WAIT", "EXECUTE", "SORTING", "COMPLETED", "CANCELED", "ERROR"]

    @pytest.mark.asyncio
    async def test_response_contains_progress(self, tool, mock_search_status_wait):
        """Test that response contains progress field."""
        mock_response = httpx.Response(
            status_code=200,
            json=mock_search_status_wait,
            request=httpx.Request("GET", "http://test")
        )

        tool.client = AsyncMock()
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"search_id": "s123"})

        content = json.loads(result["content"][0]["text"])
        assert "progress" in content
        assert isinstance(content["progress"], int)
        assert 0 <= content["progress"] <= 100

    @pytest.mark.asyncio
    async def test_response_json_formatted(self, tool, mock_search_status_wait):
        """Test that response is properly formatted JSON."""
        mock_response = httpx.Response(
            status_code=200,
            json=mock_search_status_wait,
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
