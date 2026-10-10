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
Tests for Get Offense Notes Tool
"""

import json
import pytest
import httpx
from unittest.mock import AsyncMock
from qradar_mcp.tools.offense.get_offense_notes import GetOffenseNotesTool


@pytest.fixture
def tool():
    """Create a GetOffenseNotesTool instance for testing."""
    return GetOffenseNotesTool()


@pytest.fixture
def sample_notes():
    """Sample notes data from QRadar API."""
    return [
        {
            "id": 1,
            "create_time": 1640000000000,
            "username": "admin",
            "note_text": "Initial investigation started"
        },
        {
            "id": 2,
            "create_time": 1640001000000,
            "username": "analyst1",
            "note_text": "Found suspicious activity from 192.168.1.100"
        },
        {
            "id": 3,
            "create_time": 1640002000000,
            "username": "admin",
            "note_text": "Escalated to security team"
        }
    ]


class TestGetOffenseNotesToolProperties:
    """Test tool properties."""

    def test_name(self, tool):
        """Test tool name."""
        assert tool.name == "get_offense_notes"

    def test_description(self, tool):
        """Test tool description."""
        assert "Retrieve investigation notes" in tool.description
        assert "offense" in tool.description.lower()

    def test_input_schema(self, tool):
        """Test input schema structure."""
        schema = tool.input_schema
        assert "properties" in schema
        assert "offense_id" in schema["properties"]
        assert "filter" in schema["properties"]
        assert "fields" in schema["properties"]
        assert "start" in schema["properties"]
        assert "limit" in schema["properties"]
        assert schema["properties"]["offense_id"]["type"] == "integer"
        assert "offense_id" in schema["required"]


class TestGetOffenseNotesExecution:
    """Test tool execution with various scenarios."""

    @pytest.mark.asyncio
    async def test_missing_offense_id(self, tool):
        """Test execution without offense_id."""
        result = await tool.execute({})
        assert result.get("isError", False) is True
        content = result["content"][0]["text"]
        assert "error" in content.lower()
        assert "offense_id" in content.lower()

    @pytest.mark.asyncio
    async def test_invalid_offense_id_negative(self, tool):
        """Test execution with negative offense_id."""
        result = await tool.execute({"offense_id": -1})
        assert result.get("isError", False) is True
        content = result["content"][0]["text"]
        assert "error" in content.lower()
        assert "invalid" in content.lower()

    @pytest.mark.asyncio
    async def test_invalid_offense_id_string(self, tool):
        """Test execution with string offense_id."""
        result = await tool.execute({"offense_id": "invalid"})
        assert result.get("isError", False) is True
        content = result["content"][0]["text"]
        assert "error" in content.lower()

    @pytest.mark.asyncio
    async def test_successful_retrieval(self, tool, sample_notes):
        """Test successful notes retrieval."""
        tool.client = AsyncMock()
        mock_response = httpx.Response(
            status_code=200,
            json=sample_notes,
            request=httpx.Request("GET", "http://test")
        )
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"offense_id": 123})

        assert "isError" not in result or result["isError"] is False
        content = result["content"][0]["text"]
        response_data = json.loads(content)

        assert response_data["offense_id"] == 123
        assert response_data["total_notes"] == 3
        assert len(response_data["notes"]) == 3
        assert response_data["notes"][0]["note_text"] == "Initial investigation started"

    @pytest.mark.asyncio
    async def test_empty_notes_list(self, tool):
        """Test retrieval when offense has no notes."""
        tool.client = AsyncMock()
        mock_response = httpx.Response(
            status_code=200,
            json=[],
            request=httpx.Request("GET", "http://test")
        )
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"offense_id": 456})

        assert "isError" not in result or result["isError"] is False
        content = result["content"][0]["text"]
        response_data = json.loads(content)

        assert response_data["offense_id"] == 456
        assert response_data["total_notes"] == 0
        assert response_data["notes"] == []

    @pytest.mark.asyncio
    async def test_with_filter_parameter(self, tool, sample_notes):
        """Test retrieval with filter parameter."""
        tool.client = AsyncMock()
        mock_response = httpx.Response(
            status_code=200,
            json=[sample_notes[0], sample_notes[2]],
            request=httpx.Request("GET", "http://test")
        )
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({
            "offense_id": 123,
            "filter": "username='admin'"
        })

        assert "isError" not in result or result["isError"] is False
        tool.client.get.assert_called_once()
        call_args = tool.client.get.call_args
        assert call_args[1]["params"]["filter"] == "username='admin'"

    @pytest.mark.asyncio
    async def test_with_fields_parameter(self, tool):
        """Test retrieval with fields parameter."""
        tool.client = AsyncMock()
        mock_response = httpx.Response(
            status_code=200,
            json=[
                {"id": 1, "note_text": "Test note"}
            ],
            request=httpx.Request("GET", "http://test")
        )
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({
            "offense_id": 123,
            "fields": "id,note_text"
        })

        assert "isError" not in result or result["isError"] is False
        call_args = tool.client.get.call_args
        assert call_args[1]["params"]["fields"] == "id,note_text"

    @pytest.mark.asyncio
    async def test_with_pagination_parameters(self, tool, sample_notes):
        """Test retrieval with pagination."""
        tool.client = AsyncMock()
        mock_response = httpx.Response(
            status_code=200,
            json=sample_notes[:2],
            request=httpx.Request("GET", "http://test")
        )
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({
            "offense_id": 123,
            "start": 0,
            "limit": 2
        })

        assert "isError" not in result or result["isError"] is False
        call_args = tool.client.get.call_args
        assert "Range" in call_args[1]["headers"]
        assert call_args[1]["headers"]["Range"] == "items=0-1"

    @pytest.mark.asyncio
    async def test_with_custom_start_offset(self, tool, sample_notes):
        """Test retrieval with custom start offset."""
        tool.client = AsyncMock()
        mock_response = httpx.Response(
            status_code=200,
            json=[sample_notes[2]],
            request=httpx.Request("GET", "http://test")
        )
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({
            "offense_id": 123,
            "start": 2,
            "limit": 1
        })

        assert "isError" not in result or result["isError"] is False
        call_args = tool.client.get.call_args
        assert call_args[1]["headers"]["Range"] == "items=2-2"

    @pytest.mark.asyncio
    async def test_default_pagination(self, tool, sample_notes):
        """Test default pagination values."""
        tool.client = AsyncMock()
        mock_response = httpx.Response(
            status_code=200,
            json=sample_notes,
            request=httpx.Request("GET", "http://test")
        )
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"offense_id": 123})

        assert "isError" not in result or result["isError"] is False
        call_args = tool.client.get.call_args
        # Default: start=0, limit=50, so Range should be items=0-49
        assert call_args[1]["headers"]["Range"] == "items=0-49"

    @pytest.mark.asyncio
    async def test_all_parameters_combined(self, tool, sample_notes):
        """Test with all optional parameters."""
        tool.client = AsyncMock()
        mock_response = httpx.Response(
            status_code=200,
            json=[sample_notes[0]],
            request=httpx.Request("GET", "http://test")
        )
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({
            "offense_id": 123,
            "filter": "username='admin'",
            "fields": "id,note_text,create_time",
            "start": 0,
            "limit": 10
        })

        assert "isError" not in result or result["isError"] is False
        call_args = tool.client.get.call_args
        assert call_args[1]["params"]["filter"] == "username='admin'"
        assert call_args[1]["params"]["fields"] == "id,note_text,create_time"
        assert call_args[1]["headers"]["Range"] == "items=0-9"


class TestGetOffenseNotesErrorHandling:
    """Test error handling scenarios."""

    @pytest.mark.asyncio
    async def test_offense_not_found(self, tool):
        """Test handling of 404 offense not found."""
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(side_effect=ValueError("404: Offense not found"))

        result = await tool.execute({"offense_id": 99999})

        assert result.get("isError", False) is True
        content = result["content"][0]["text"]
        assert "Tool execution failed: 404: Offense not found" in content

    @pytest.mark.asyncio
    async def test_invalid_filter_parameter(self, tool):
        """Test handling of invalid filter."""
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(side_effect=ValueError("422: Invalid filter expression"))

        result = await tool.execute({
            "offense_id": 123,
            "filter": "invalid filter syntax"
        })

        assert result.get("isError", False) is True
        content = result["content"][0]["text"]
        assert "Tool execution failed: 422: Invalid filter expression" in content

    @pytest.mark.asyncio
    async def test_server_error(self, tool):
        """Test handling of 500 server error."""
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(side_effect=RuntimeError("500: Internal server error"))

        result = await tool.execute({"offense_id": 123})

        assert result.get("isError", False) is True
        content = result["content"][0]["text"]
        assert "error" in content.lower()
        assert "500" in content or "internal" in content.lower()

    @pytest.mark.asyncio
    async def test_network_error(self, tool):
        """Test handling of network errors."""
        tool.client = AsyncMock()
        tool.client.get = AsyncMock(side_effect=RuntimeError("Connection timeout"))

        result = await tool.execute({"offense_id": 123})

        assert result.get("isError", False) is True
        content = result["content"][0]["text"]
        assert "Tool execution failed: Connection timeout" in content


class TestGetOffenseNotesAPIInteraction:
    """Test API interaction details."""

    @pytest.mark.asyncio
    async def test_correct_endpoint_called(self, tool):
        """Test that correct API endpoint is called."""
        tool.client = AsyncMock()
        mock_response = httpx.Response(
            status_code=200,
            json=[],
            request=httpx.Request("GET", "http://test")
        )
        tool.client.get = AsyncMock(return_value=mock_response)

        await tool.execute({"offense_id": 123})

        tool.client.get.assert_called_once()
        call_args = tool.client.get.call_args
        assert call_args[0][0] == "siem/offenses/123/notes"

    @pytest.mark.asyncio
    async def test_headers_structure(self, tool):
        """Test that headers are properly structured."""
        tool.client = AsyncMock()
        mock_response = httpx.Response(
            status_code=200,
            json=[],
            request=httpx.Request("GET", "http://test")
        )
        tool.client.get = AsyncMock(return_value=mock_response)

        await tool.execute({"offense_id": 123, "start": 10, "limit": 20})

        call_args = tool.client.get.call_args
        headers = call_args[1]["headers"]
        assert "Range" in headers
        assert headers["Range"] == "items=10-29"

    @pytest.mark.asyncio
    async def test_params_structure(self, tool):
        """Test that query parameters are properly structured."""
        tool.client = AsyncMock()
        mock_response = httpx.Response(
            status_code=200,
            json=[],
            request=httpx.Request("GET", "http://test")
        )
        tool.client.get = AsyncMock(return_value=mock_response)

        await tool.execute({
            "offense_id": 123,
            "filter": "id>5",
            "fields": "id,note_text"
        })

        call_args = tool.client.get.call_args
        params = call_args[1]["params"]
        assert params["filter"] == "id>5"
        assert params["fields"] == "id,note_text"


class TestGetOffenseNotesResponseFormat:
    """Test response formatting."""

    @pytest.mark.asyncio
    async def test_response_structure(self, tool, sample_notes):
        """Test that response has correct structure."""
        tool.client = AsyncMock()
        mock_response = httpx.Response(
            status_code=200,
            json=sample_notes,
            request=httpx.Request("GET", "http://test")
        )
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"offense_id": 123})

        assert "isError" not in result or result["isError"] is False
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"

        content = result["content"][0]["text"]
        response_data = json.loads(content)

        assert "offense_id" in response_data
        assert "total_notes" in response_data
        assert "notes" in response_data
        assert isinstance(response_data["notes"], list)

    @pytest.mark.asyncio
    async def test_note_object_structure(self, tool, sample_notes):
        """Test that note objects have correct structure."""
        tool.client = AsyncMock()
        mock_response = httpx.Response(
            status_code=200,
            json=sample_notes,
            request=httpx.Request("GET", "http://test")
        )
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"offense_id": 123})

        content = result["content"][0]["text"]
        response_data = json.loads(content)
        note = response_data["notes"][0]

        assert "id" in note
        assert "create_time" in note
        assert "username" in note
        assert "note_text" in note

    @pytest.mark.asyncio
    async def test_total_notes_count(self, tool, sample_notes):
        """Test that total_notes count is accurate."""
        tool.client = AsyncMock()
        mock_response = httpx.Response(
            status_code=200,
            json=sample_notes,
            request=httpx.Request("GET", "http://test")
        )
        tool.client.get = AsyncMock(return_value=mock_response)

        result = await tool.execute({"offense_id": 123})

        content = result["content"][0]["text"]
        response_data = json.loads(content)

        assert response_data["total_notes"] == len(sample_notes)
        assert response_data["total_notes"] == len(response_data["notes"])
