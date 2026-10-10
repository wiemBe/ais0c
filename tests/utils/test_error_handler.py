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


"""Tests for error handler utilities."""

import pytest
from unittest.mock import Mock
from requests import HTTPError, Response
from qradar_mcp.utils.error_handler import extract_qradar_error


class TestExtractQRadarError:
    """Tests for extract_qradar_error function."""

    def test_extract_message_field(self):
        """Test extracting error from 'message' field."""
        # Create mock HTTPError with response containing 'message'
        response = Mock(spec=Response)
        response.json.return_value = {"message": "Invalid AQL syntax"}

        error = HTTPError("422 Client Error")
        error.response = response

        result = extract_qradar_error(error, "creating search")
        assert result == "Error creating search: Invalid AQL syntax"

    def test_extract_description_field(self):
        """Test extracting error from 'description' field."""
        response = Mock(spec=Response)
        response.json.return_value = {"description": "Resource not found"}

        error = HTTPError("404 Not Found")
        error.response = response

        result = extract_qradar_error(error, "fetching offense")
        assert result == "Error fetching offense: Resource not found"

    def test_extract_nested_error_string(self):
        """Test extracting error from nested 'error' field (string)."""
        response = Mock(spec=Response)
        response.json.return_value = {"error": "Authentication failed"}

        error = HTTPError("401 Unauthorized")
        error.response = response

        result = extract_qradar_error(error, "API request")
        assert result == "Error API request: Authentication failed"

    def test_extract_nested_error_dict(self):
        """Test extracting error from nested 'error' dict with 'message'."""
        response = Mock(spec=Response)
        response.json.return_value = {
            "error": {"message": "Rate limit exceeded"}
        }

        error = HTTPError("429 Too Many Requests")
        error.response = response

        result = extract_qradar_error(error, "API request")
        assert result == "Error API request: Rate limit exceeded"

    def test_fallback_to_base_error_no_response(self):
        """Test fallback when response is None."""
        error = HTTPError("500 Server Error")
        error.response = None

        result = extract_qradar_error(error, "API request")
        assert "Error API request: 500 Server Error" in result

    def test_fallback_to_base_error_invalid_json(self):
        """Test fallback when response JSON is invalid."""
        response = Mock(spec=Response)
        response.json.side_effect = ValueError("Invalid JSON")

        error = HTTPError("500 Server Error")
        error.response = response

        result = extract_qradar_error(error, "API request")
        assert "Error API request: 500 Server Error" in result

    def test_fallback_to_base_error_no_known_fields(self):
        """Test fallback when response has no known error fields."""
        response = Mock(spec=Response)
        response.json.return_value = {"unknown_field": "some value"}

        error = HTTPError("500 Server Error")
        error.response = response

        result = extract_qradar_error(error, "API request")
        assert "Error API request: 500 Server Error" in result

    def test_default_context(self):
        """Test using default context parameter."""
        response = Mock(spec=Response)
        response.json.return_value = {"message": "Test error"}

        error = HTTPError("400 Bad Request")
        error.response = response

        result = extract_qradar_error(error)
        assert result == "Error API request: Test error"
