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
Tests for advanced parameter builder functions.
"""

from qradar_mcp.utils.parameters import (
    validate_sort_fields,
    build_aql_filter
)


class TestValidateSortFields:
    """Tests for validate_sort_fields function."""

    def test_valid_fields(self):
        """Test with valid sort fields."""
        result = validate_sort_fields(
            ["+severity", "-start_time"],
            ["severity", "start_time", "id"]
        )
        assert result is True

    def test_invalid_field(self):
        """Test with invalid sort field."""
        result = validate_sort_fields(
            ["+invalid_field"],
            ["severity", "start_time"]
        )
        assert result is False

    def test_without_prefix(self):
        """Test fields without +/- prefix."""
        result = validate_sort_fields(
            ["severity"],
            ["severity", "start_time"]
        )
        assert result is True


class TestBuildAqlFilter:
    """Tests for build_aql_filter function."""

    def test_simple_equality(self):
        """Test simple equality condition."""
        result = build_aql_filter({"status": "OPEN"})
        assert result == "status='OPEN'"

    def test_numeric_equality(self):
        """Test numeric equality."""
        result = build_aql_filter({"severity": 5})
        assert result == "severity=5"

    def test_greater_than(self):
        """Test greater than operator."""
        result = build_aql_filter({"severity": {"gt": 5}})
        assert result == "severity > 5"

    def test_less_than(self):
        """Test less than operator."""
        result = build_aql_filter({"severity": {"lt": 5}})
        assert result == "severity < 5"

    def test_in_operator(self):
        """Test IN operator."""
        result = build_aql_filter({"id": {"in": [1, 2, 3]}})
        assert result == "id in (1,2,3)"

    def test_like_operator(self):
        """Test LIKE operator."""
        result = build_aql_filter({"description": {"like": "%test%"}})
        assert result == "description like '%test%'"

    def test_multiple_conditions(self):
        """Test multiple conditions."""
        result = build_aql_filter({
            "status": "OPEN",
            "severity": {"gt": 5}
        })
        assert "status='OPEN'" in result
        assert "severity > 5" in result
        assert " and " in result

    def test_gte_operator(self):
        """Test greater than or equal operator."""
        result = build_aql_filter({"severity": {"gte": 5}})
        assert result == "severity >= 5"

    def test_lte_operator(self):
        """Test less than or equal operator."""
        result = build_aql_filter({"severity": {"lte": 5}})
        assert result == "severity <= 5"