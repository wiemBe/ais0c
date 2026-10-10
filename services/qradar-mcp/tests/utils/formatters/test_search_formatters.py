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
Unit tests for Ariel search formatters.
"""

from qradar_mcp.utils.formatters import (
    format_search_status,
    format_search_results
)


class TestFormatSearchStatus:
    """Tests for format_search_status function."""

    def test_format_complete_search_status(self):
        """Test formatting search status with all fields."""
        search = {
            "search_id": "abc123",
            "status": "COMPLETED",
            "progress": 100,
            "query_string": "SELECT * FROM events",
            "record_count": 1000,
            "processed_record_count": 1000,
            "query_execution_time": 5000
        }

        result = format_search_status(search)

        assert "Search ID: abc123" in result
        assert "Status: COMPLETED" in result
        assert "Progress: 100%" in result
        assert "SELECT * FROM events" in result
        assert "Record Count: 1000" in result
        assert "5000ms" in result

    def test_format_search_with_errors(self):
        """Test formatting search status with error messages."""
        search = {
            "search_id": "def456",
            "status": "ERROR",
            "error_messages": [
                {"severity": "ERROR", "message": "Query syntax error"},
                {"severity": "WARNING", "message": "Slow query"}
            ]
        }

        result = format_search_status(search)

        assert "Search ID: def456" in result
        assert "Errors:" in result
        assert "[ERROR] Query syntax error" in result
        assert "[WARNING] Slow query" in result

    def test_format_minimal_search_status(self):
        """Test formatting search status with minimal fields."""
        search = {"search_id": "ghi789"}

        result = format_search_status(search)

        assert "Search ID: ghi789" in result
        assert "N/A" in result


class TestFormatSearchResults:
    """Tests for format_search_results function."""

    def test_format_empty_results(self):
        """Test formatting empty search results."""
        results = {"events": []}
        result = format_search_results(results)
        assert result == "No results found."

    def test_format_single_result(self):
        """Test formatting single search result."""
        results = {
            "events": [
                {"sourceip": "192.168.1.1", "destinationip": "10.0.0.1",
                 "eventname": "Login"}
            ]
        }

        result = format_search_results(results)

        assert "Search Results (1 rows)" in result
        assert "sourceip" in result
        assert "192.168.1.1" in result

    def test_format_multiple_results(self):
        """Test formatting multiple search results."""
        results = {
            "events": [
                {"sourceip": "192.168.1.1", "eventname": "Login"},
                {"sourceip": "192.168.1.2", "eventname": "Logout"}
            ]
        }

        result = format_search_results(results)

        assert "Search Results (2 rows)" in result
        assert "192.168.1.1" in result
        assert "192.168.1.2" in result

    def test_format_truncates_long_results(self):
        """Test that results are truncated to max_rows."""
        results = {
            "events": [{"id": i} for i in range(150)]
        }

        result = format_search_results(results, max_rows=100)

        assert "Search Results (150 rows)" in result
        assert "and 50 more rows" in result