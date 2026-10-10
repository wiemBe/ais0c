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
Unit tests for reference data and asset formatters.
"""

from qradar_mcp.utils.formatters import (
    format_reference_set,
    format_asset
)


class TestFormatReferenceSet:
    """Tests for format_reference_set function."""

    def test_format_reference_set_metadata_only(self):
        """Test formatting reference set without data."""
        ref_set = {
            "name": "suspicious_ips",
            "element_type": "IP",
            "number_of_elements": 100,
            "timeout_type": "FIRST_SEEN"
        }

        result = format_reference_set(ref_set)

        assert "Name: suspicious_ips" in result
        assert "Element Type: IP" in result
        assert "Number of Elements: 100" in result
        assert "Timeout Type: FIRST_SEEN" in result

    def test_format_reference_set_with_data(self):
        """Test formatting reference set with data."""
        ref_set = {
            "name": "suspicious_ips",
            "element_type": "IP",
            "number_of_elements": 3
        }
        data = [
            {"value": "192.168.1.1"},
            {"value": "192.168.1.2"},
            {"value": "192.168.1.3"}
        ]

        result = format_reference_set(ref_set, data)

        assert "Elements:" in result
        assert "192.168.1.1" in result
        assert "192.168.1.2" in result
        assert "192.168.1.3" in result

    def test_format_reference_set_truncates_data(self):
        """Test that reference set data is truncated to 50 elements."""
        ref_set = {"name": "test_set", "element_type": "IP",
                  "number_of_elements": 60}
        data = [{"value": f"192.168.1.{i}"} for i in range(60)]

        result = format_reference_set(ref_set, data)

        assert "192.168.1.0" in result
        assert "192.168.1.49" in result
        assert "and 10 more" in result
        assert "192.168.1.59" not in result


class TestFormatAsset:
    """Tests for format_asset function."""

    def test_format_asset_basic(self):
        """Test formatting asset with basic fields."""
        asset = {
            "id": 123,
            "hostname": "server01.example.com"
        }

        result = format_asset(asset)

        assert "Asset ID: 123" in result
        assert "Hostname: server01.example.com" in result

    def test_format_asset_with_interfaces(self):
        """Test formatting asset with IP addresses."""
        asset = {
            "id": 456,
            "hostname": "server02.example.com",
            "interfaces": [
                {
                    "ip_addresses": [
                        {"value": "192.168.1.10"},
                        {"value": "10.0.0.5"}
                    ]
                }
            ]
        }

        result = format_asset(asset)

        assert "Asset ID: 456" in result
        assert "IP Addresses:" in result
        assert "192.168.1.10" in result
        assert "10.0.0.5" in result

    def test_format_asset_minimal(self):
        """Test formatting asset with minimal fields."""
        asset = {}

        result = format_asset(asset)

        assert "Asset ID: N/A" in result
        assert "Hostname: N/A" in result