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
# Modified in the ais0c platform fork (T-040): the note text is checked in the form body.


"""
Unit tests for the AddOffenseNoteTool.
"""

from urllib.parse import parse_qs

import pytest
import httpx
from unittest.mock import AsyncMock
from qradar_mcp.tools.offense.add_offense_note import AddOffenseNoteTool, FORM_CONTENT_TYPE


def _sent_note_text(call_args):
    """The note text of a post() call: in the form body, never in the URL."""
    kwargs = call_args[1]
    assert "note_text" not in (kwargs.get("params") or {})
    assert kwargs["headers"]["Content-Type"] == FORM_CONTENT_TYPE
    return parse_qs(kwargs["data"], strict_parsing=True)["note_text"][0]


class TestAddOffenseNoteTool:
    """Tests for AddOffenseNoteTool class."""

    def test_tool_name(self):
        """Test that tool has correct name."""
        tool = AddOffenseNoteTool()
        assert tool.name == "add_offense_note"

    def test_tool_description(self):
        """Test that tool has correct description."""
        tool = AddOffenseNoteTool()
        assert "Add investigation notes" in tool.description
        assert "offense" in tool.description.lower()

    def test_input_schema_structure(self):
        """Test that input schema has correct structure."""
        tool = AddOffenseNoteTool()
        schema = tool.input_schema

        assert schema["type"] == "object"
        assert "properties" in schema
        assert "offense_id" in schema["properties"]
        assert "note_text" in schema["properties"]
        assert "fields" in schema["properties"]

    def test_input_schema_required_fields(self):
        """Test that required fields are marked correctly."""
        tool = AddOffenseNoteTool()
        schema = tool.input_schema

        assert "required" in schema
        assert "offense_id" in schema["required"]
        assert "note_text" in schema["required"]
        assert "fields" not in schema["required"]

    def test_input_schema_offense_id_constraints(self):
        """Test offense_id parameter constraints."""
        tool = AddOffenseNoteTool()
        schema = tool.input_schema

        offense_id_schema = schema["properties"]["offense_id"]
        assert offense_id_schema["type"] == "integer"
        assert offense_id_schema["minimum"] == 0
        assert "description" in offense_id_schema

    def test_input_schema_note_text_constraints(self):
        """Test note_text parameter constraints."""
        tool = AddOffenseNoteTool()
        schema = tool.input_schema

        note_text_schema = schema["properties"]["note_text"]
        assert note_text_schema["type"] == "string"
        assert note_text_schema["minLength"] == 1
        assert note_text_schema["maxLength"] == 10000
        assert "description" in note_text_schema

    def test_to_mcp_tool_definition(self):
        """Test converting tool to MCP definition."""
        tool = AddOffenseNoteTool()
        definition = tool.to_mcp_tool_definition()

        assert definition["name"] == "add_offense_note"
        assert "description" in definition
        assert "inputSchema" in definition

    @pytest.mark.asyncio
    async def test_execute_add_note_success(self):
        """Test successfully adding a note to an offense."""
        tool = AddOffenseNoteTool()
        tool.client = AsyncMock()

        mock_response = httpx.Response(
            status_code=201,
            json={
                "id": 1,
                "create_time": 1234567890000,
                "username": "admin",
                "note_text": "Investigation complete"
            },
            request=httpx.Request("POST", "http://test")
        )
        tool.client.post = AsyncMock(return_value=mock_response)

        result = await tool.execute({
            "offense_id": 123,
            "note_text": "Investigation complete"
        })

        # Verify API call
        tool.client.post.assert_called_once()
        call_args = tool.client.post.call_args
        assert call_args[1]["api_path"] == "siem/offenses/123/notes"
        assert _sent_note_text(call_args) == "Investigation complete"

        # Verify result
        assert "content" in result
        assert len(result["content"]) == 1
        assert result["content"][0]["type"] == "text"
        assert "Investigation complete" in result["content"][0]["text"]

    @pytest.mark.asyncio
    async def test_execute_add_note_with_fields(self):
        """Test adding a note with fields parameter."""
        tool = AddOffenseNoteTool()
        tool.client = AsyncMock()

        mock_response = httpx.Response(
            status_code=201,
            json={
                "id": 1,
                "note_text": "Test note"
            },
            request=httpx.Request("POST", "http://test")
        )
        tool.client.post = AsyncMock(return_value=mock_response)

        result = await tool.execute({
            "offense_id": 123,
            "note_text": "Test note",
            "fields": "id,note_text"
        })

        # Verify fields parameter was sent
        call_args = tool.client.post.call_args
        assert call_args[1]["params"]["fields"] == "id,note_text"

        # Verify success
        assert "content" in result

    @pytest.mark.asyncio
    async def test_execute_add_long_note(self):
        """Test adding a long note."""
        tool = AddOffenseNoteTool()
        tool.client = AsyncMock()

        long_note = "A" * 5000  # 5000 character note

        mock_response = httpx.Response(
            status_code=201,
            json={
                "id": 1,
                "note_text": long_note
            },
            request=httpx.Request("POST", "http://test")
        )
        tool.client.post = AsyncMock(return_value=mock_response)

        result = await tool.execute({
            "offense_id": 123,
            "note_text": long_note
        })

        # Verify API call
        call_args = tool.client.post.call_args
        assert _sent_note_text(call_args) == long_note

        # Verify success
        assert "content" in result

    @pytest.mark.asyncio
    async def test_execute_add_multiline_note(self):
        """Test adding a multiline note."""
        tool = AddOffenseNoteTool()
        tool.client = AsyncMock()

        multiline_note = """Investigation findings:
1. Malicious IP detected: 192.168.1.100
2. User account compromised: jdoe
3. Remediation: Password reset required"""

        mock_response = httpx.Response(
            status_code=201,
            json={
                "id": 1,
                "note_text": multiline_note
            },
            request=httpx.Request("POST", "http://test")
        )
        tool.client.post = AsyncMock(return_value=mock_response)

        result = await tool.execute({
            "offense_id": 123,
            "note_text": multiline_note
        })

        # Verify API call
        call_args = tool.client.post.call_args
        assert _sent_note_text(call_args) == multiline_note

        # Verify success
        assert "content" in result

    @pytest.mark.asyncio
    async def test_execute_missing_offense_id(self):
        """Test error when offense_id is missing."""
        tool = AddOffenseNoteTool()
        result = await tool.execute({"note_text": "Test note"})

        # Verify error response
        assert "content" in result
        assert result["content"][0]["type"] == "text"
        assert "offense_id is required" in result["content"][0]["text"].lower()
        assert result["isError"] is True

    @pytest.mark.asyncio
    async def test_execute_invalid_offense_id(self):
        """Test error when offense_id is invalid."""
        tool = AddOffenseNoteTool()
        result = await tool.execute({
            "offense_id": -1,
            "note_text": "Test note"
        })

        # Verify error response
        assert "content" in result
        assert "invalid offense_id" in result["content"][0]["text"].lower()
        assert result["isError"] is True

    @pytest.mark.asyncio
    async def test_execute_missing_note_text(self):
        """Test error when note_text is missing."""
        tool = AddOffenseNoteTool()
        result = await tool.execute({"offense_id": 123})

        # Verify error response
        assert "content" in result
        assert "note_text is required" in result["content"][0]["text"].lower()
        assert result["isError"] is True

    @pytest.mark.asyncio
    async def test_execute_empty_note_text(self):
        """Test error when note_text is empty."""
        tool = AddOffenseNoteTool()
        result = await tool.execute({
            "offense_id": 123,
            "note_text": ""
        })

        # Verify error response
        assert "content" in result
        assert "empty" in result["content"][0]["text"].lower()
        assert result["isError"] is True

    @pytest.mark.asyncio
    async def test_execute_note_text_too_long(self):
        """Test error when note_text exceeds maximum length."""
        tool = AddOffenseNoteTool()
        result = await tool.execute({
            "offense_id": 123,
            "note_text": "A" * 10001  # Exceeds 10000 character limit
        })

        # Verify error response
        assert "content" in result
        assert "exceeds maximum length" in result["content"][0]["text"].lower()
        assert result["isError"] is True

    @pytest.mark.asyncio
    async def test_execute_offense_not_found(self):
        """Test error when offense not found."""
        tool = AddOffenseNoteTool()
        tool.client = AsyncMock()

        mock_request = httpx.Request("POST", "http://test")
        mock_response = httpx.Response(
            status_code=404,
            text="Not found",
            request=mock_request
        )
        tool.client.post = AsyncMock(side_effect=httpx.HTTPStatusError(
            "404 Client Error: Not Found",
            request=mock_request,
            response=mock_response
        ))

        result = await tool.execute({
            "offense_id": 999,
            "note_text": "Test note"
        })

        # Verify error response
        assert "content" in result
        assert "error" in result["content"][0]["text"].lower()
        assert result["isError"] is True

    @pytest.mark.asyncio
    async def test_execute_invalid_parameters(self):
        """Test error when parameters are invalid."""
        tool = AddOffenseNoteTool()
        tool.client = AsyncMock()

        mock_request = httpx.Request("POST", "http://test")
        mock_response = httpx.Response(
            status_code=422,
            text="Invalid note_text",
            request=mock_request
        )
        tool.client.post = AsyncMock(side_effect=httpx.HTTPStatusError(
            "422 Client Error: Unprocessable Entity",
            request=mock_request,
            response=mock_response
        ))

        result = await tool.execute({
            "offense_id": 123,
            "note_text": "Test note"
        })

        # Verify error response
        assert "content" in result
        assert "error" in result["content"][0]["text"].lower()
        assert result["isError"] is True

    @pytest.mark.asyncio
    async def test_execute_server_error(self):
        """Test error when server returns 500."""
        tool = AddOffenseNoteTool()
        tool.client = AsyncMock()

        mock_request = httpx.Request("POST", "http://test")
        mock_response = httpx.Response(
            status_code=500,
            text="Internal server error",
            request=mock_request
        )
        tool.client.post = AsyncMock(side_effect=httpx.HTTPStatusError(
            "500 Server Error: Internal Server Error",
            request=mock_request,
            response=mock_response
        ))

        result = await tool.execute({
            "offense_id": 123,
            "note_text": "Test note"
        })

        # Verify error response
        assert "content" in result
        assert "error" in result["content"][0]["text"].lower()
        assert result["isError"] is True

    @pytest.mark.asyncio
    async def test_execute_logs_add_attempt(self):
        """Test that note additions are logged."""
        tool = AddOffenseNoteTool()
        tool.client = AsyncMock()

        mock_response = httpx.Response(
            status_code=201,
            json={"id": 1, "note_text": "Test note"},
            request=httpx.Request("POST", "http://test")
        )
        tool.client.post = AsyncMock(return_value=mock_response)

        result = await tool.execute({
            "offense_id": 123,
            "note_text": "Test note"
        })

        # Verify execution completed successfully
        assert "content" in result
        assert result["content"][0]["type"] == "text"
        # Response contains the note data (id and note_text from mock)
        response_text = result["content"][0]["text"]
        assert '"id": 1' in response_text
        assert 'Test note' in response_text

    @pytest.mark.asyncio
    async def test_execute_note_with_special_characters(self):
        """Test adding a note with special characters."""
        tool = AddOffenseNoteTool()
        tool.client = AsyncMock()

        special_note = "Note with special chars: <>&\"'@#$%"

        mock_response = httpx.Response(
            status_code=201,
            json={
                "id": 1,
                "note_text": special_note
            },
            request=httpx.Request("POST", "http://test")
        )
        tool.client.post = AsyncMock(return_value=mock_response)

        result = await tool.execute({
            "offense_id": 123,
            "note_text": special_note
        })

        # Verify API call
        call_args = tool.client.post.call_args
        assert _sent_note_text(call_args) == special_note

        # Verify success
        assert "content" in result
