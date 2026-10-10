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
Tests for query validator functions.
"""

from qradar_mcp.utils.validators import (
    validate_aql_query,
    validate_filter_expression,
    validate_sort_expression
)


class TestValidateAqlQuery:
    """Tests for validate_aql_query function."""

    def test_valid_query(self):
        """Test with valid AQL query."""
        is_valid, error = validate_aql_query("SELECT * FROM events")
        assert is_valid is True
        assert error is None

    def test_missing_select(self):
        """Test query without SELECT."""
        is_valid, error = validate_aql_query("* FROM events")
        assert is_valid is False
        assert "SELECT" in error

    def test_missing_from(self):
        """Test query without FROM."""
        is_valid, error = validate_aql_query("SELECT *")
        assert is_valid is False
        assert "FROM" in error

    def test_unbalanced_parentheses(self):
        """Test query with unbalanced parentheses."""
        is_valid, error = validate_aql_query("SELECT * FROM events WHERE (id = 1")
        assert is_valid is False
        assert "parentheses" in error

    def test_unbalanced_quotes(self):
        """Test query with unbalanced quotes."""
        is_valid, error = validate_aql_query("SELECT * FROM events WHERE name = 'test")
        assert is_valid is False
        assert "quotes" in error


class TestValidateFilterExpression:
    """Tests for validate_filter_expression function."""

    def test_valid_filter(self):
        """Test with valid filter expression."""
        is_valid, error = validate_filter_expression("status='OPEN'")
        assert is_valid is True
        assert error is None

    def test_empty_filter(self):
        """Test with empty filter."""
        is_valid, error = validate_filter_expression("")
        assert is_valid is False

    def test_unbalanced_parentheses(self):
        """Test with unbalanced parentheses."""
        is_valid, error = validate_filter_expression("(status='OPEN'")
        assert is_valid is False
        assert "parentheses" in error


class TestValidateSortExpression:
    """Tests for validate_sort_expression function."""

    def test_valid_sort(self):
        """Test with valid sort expression."""
        is_valid, error = validate_sort_expression("+severity")
        assert is_valid is True
        assert error is None

    def test_multiple_fields(self):
        """Test with multiple sort fields."""
        is_valid, error = validate_sort_expression("+severity,-start_time")
        assert is_valid is True
        assert error is None

    def test_empty_sort(self):
        """Test with empty sort expression."""
        is_valid, error = validate_sort_expression("")
        assert is_valid is False

    def test_invalid_field_name(self):
        """Test with invalid field name."""
        is_valid, error = validate_sort_expression("+123invalid")
        assert is_valid is False