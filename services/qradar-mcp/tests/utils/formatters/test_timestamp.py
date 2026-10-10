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
Unit tests for timestamp formatting.
"""

from qradar_mcp.utils.formatters import format_timestamp


class TestFormatTimestamp:
    """Tests for format_timestamp function."""

    def test_format_valid_timestamp(self):
        """Test formatting a valid timestamp."""
        # 2024-01-15 12:30:45 UTC (but will be in local time)
        timestamp_ms = 1705324245000
        result = format_timestamp(timestamp_ms)
        assert "2024-01-15" in result or "2024-01-14" in result  # Could be day before in some timezones
        assert "UTC" in result
        assert len(result) > 10  # Should have date and time

    def test_format_none_timestamp(self):
        """Test formatting None timestamp."""
        result = format_timestamp(None)
        assert result == "N/A"

    def test_format_zero_timestamp(self):
        """Test formatting zero timestamp (epoch)."""
        result = format_timestamp(0)
        # Epoch could be 1969-12-31 or 1970-01-01 depending on timezone
        assert "1970" in result or "1969" in result
        assert "UTC" in result

    def test_format_invalid_timestamp(self):
        """Test formatting invalid timestamp."""
        # Very large invalid timestamp
        result = format_timestamp(999999999999999999)
        assert "Invalid timestamp" in result