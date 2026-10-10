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
Tests for combined parameter builder functions.
"""

from qradar_mcp.utils.parameters import (
    build_query_params,
    build_headers,
    parse_range_from_limit_offset
)


class TestBuildQueryParams:
    """Tests for build_query_params function."""

    def test_all_parameters(self):
        """Test with all parameters."""
        result = build_query_params(
            filter_expr="status='OPEN'",
            sort_fields=["+severity"],
            fields=["id", "description"]
        )
        assert result == {
            "filter": "status='OPEN'",
            "sort": "+severity",
            "fields": "id,description"
        }

    def test_no_parameters(self):
        """Test with no parameters."""
        result = build_query_params()
        assert result == {}

    def test_with_additional_params(self):
        """Test with additional kwargs."""
        result = build_query_params(custom_param="value")
        assert result == {"custom_param": "value"}


class TestBuildHeaders:
    """Tests for build_headers function."""

    def test_with_range(self):
        """Test building headers with range."""
        result = build_headers(start=0, end=49)
        assert result == {"Range": "items=0-49"}

    def test_with_additional_headers(self):
        """Test with additional headers."""
        result = build_headers(start=0, end=49, Authorization="Bearer token")
        assert result == {
            "Range": "items=0-49",
            "Authorization": "Bearer token"
        }

    def test_no_headers(self):
        """Test with no headers."""
        result = build_headers()
        assert result == {}


class TestParseRangeFromLimitOffset:
    """Tests for parse_range_from_limit_offset function."""

    def test_basic_range(self):
        """Test basic limit and offset."""
        start, end = parse_range_from_limit_offset(50, 0)
        assert start == 0
        assert end == 49

    def test_with_offset(self):
        """Test with offset."""
        start, end = parse_range_from_limit_offset(50, 50)
        assert start == 50
        assert end == 99

    def test_no_limit(self):
        """Test without limit."""
        start, end = parse_range_from_limit_offset(None, 0)
        assert start is None
        assert end is None

    def test_no_offset(self):
        """Test without offset (defaults to 0)."""
        start, end = parse_range_from_limit_offset(25, None)
        assert start == 0
        assert end == 24