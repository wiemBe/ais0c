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
Tests for basic validator functions.
"""

from qradar_mcp.utils.validators import (
    validate_offense_id,
    validate_ip_address,
    validate_status,
    validate_severity,
    validate_element_type,
    validate_timeout_type,
    validate_field_name,
    validate_search_id
)


class TestValidateOffenseId:
    """Tests for validate_offense_id function."""

    def test_valid_id(self):
        """Test with valid offense ID."""
        assert validate_offense_id(123) is True
        assert validate_offense_id(0) is True
        assert validate_offense_id("456") is True

    def test_invalid_id(self):
        """Test with invalid offense ID."""
        assert validate_offense_id(-1) is False
        assert validate_offense_id("abc") is False
        assert validate_offense_id(None) is False


class TestValidateIpAddress:
    """Tests for validate_ip_address function."""

    def test_valid_ipv4(self):
        """Test with valid IPv4 addresses."""
        assert validate_ip_address("192.168.1.1") is True
        assert validate_ip_address("10.0.0.1") is True
        assert validate_ip_address("255.255.255.255") is True

    def test_invalid_ipv4(self):
        """Test with invalid IPv4 addresses."""
        assert validate_ip_address("256.1.1.1") is False
        assert validate_ip_address("192.168.1") is False
        assert validate_ip_address("abc.def.ghi.jkl") is False

    def test_valid_ipv6(self):
        """Test with valid IPv6 addresses."""
        assert validate_ip_address("2001:0db8:85a3:0000:0000:8a2e:0370:7334") is True
        assert validate_ip_address("::1") is True
        assert validate_ip_address("fe80::1") is True


class TestValidateStatus:
    """Tests for validate_status function."""

    def test_valid_statuses(self):
        """Test with valid status values."""
        assert validate_status("OPEN") is True
        assert validate_status("CLOSED") is True
        assert validate_status("HIDDEN") is True
        assert validate_status("open") is True  # Case insensitive

    def test_invalid_status(self):
        """Test with invalid status."""
        assert validate_status("INVALID") is False
        assert validate_status("") is False


class TestValidateSeverity:
    """Tests for validate_severity function."""

    def test_valid_severity(self):
        """Test with valid severity values."""
        assert validate_severity(1) is True
        assert validate_severity(5) is True
        assert validate_severity(10) is True
        assert validate_severity("7") is True

    def test_invalid_severity(self):
        """Test with invalid severity values."""
        assert validate_severity(0) is False
        assert validate_severity(11) is False
        assert validate_severity(-1) is False
        assert validate_severity("abc") is False

class TestValidateElementType:
    """Tests for validate_element_type function."""

    def test_valid_element_types(self):
        """Test with valid element types."""
        assert validate_element_type("ALN") is True
        assert validate_element_type("NUM") is True
        assert validate_element_type("IP") is True
        assert validate_element_type("PORT") is True
        assert validate_element_type("DATE") is True
        # Test case insensitive
        assert validate_element_type("aln") is True
        assert validate_element_type("num") is True

    def test_invalid_element_type(self):
        """Test with invalid element type."""
        assert validate_element_type("INVALID") is False
        assert validate_element_type("STRING") is False
        assert validate_element_type("") is False


class TestValidateTimeoutType:
    """Tests for validate_timeout_type function."""

    def test_valid_timeout_types(self):
        """Test with valid timeout types."""
        assert validate_timeout_type("UNKNOWN") is True
        assert validate_timeout_type("FIRST_SEEN") is True
        assert validate_timeout_type("LAST_SEEN") is True
        # Test case insensitive
        assert validate_timeout_type("unknown") is True
        assert validate_timeout_type("first_seen") is True

    def test_invalid_timeout_type(self):
        """Test with invalid timeout type."""
        assert validate_timeout_type("INVALID") is False
        assert validate_timeout_type("NEVER") is False
        assert validate_timeout_type("") is False


class TestValidateFieldName:
    """Tests for validate_field_name function."""

    def test_valid_field_names(self):
        """Test with valid field names."""
        assert validate_field_name("field_name") is True
        assert validate_field_name("_private") is True
        assert validate_field_name("field123") is True
        assert validate_field_name("CamelCase") is True

    def test_invalid_field_names(self):
        """Test with invalid field names."""
        assert validate_field_name("123field") is False
        assert validate_field_name("field-name") is False
        assert validate_field_name("field name") is False
        assert validate_field_name("field.name") is False

    def test_field_name_with_allowed_list(self):
        """Test field name validation with allowed list."""
        allowed = ["sourceip", "destinationip", "username"]
        assert validate_field_name("sourceip", allowed) is True
        assert validate_field_name("destinationip", allowed) is True
        assert validate_field_name("invalid_field", allowed) is False


class TestValidateSearchId:
    """Tests for validate_search_id function."""

    def test_valid_search_ids(self):
        """Test with valid search IDs."""
        # Numeric format
        assert validate_search_id("s123") is True
        assert validate_search_id("s456789") is True
        # UUID format
        assert validate_search_id("550e8400-e29b-41d4-a716-446655440000") is True
        assert validate_search_id("6ba7b810-9dad-11d1-80b4-00c04fd430c8") is True

    def test_invalid_search_ids(self):
        """Test with invalid search IDs."""
        assert validate_search_id("123") is False
        assert validate_search_id("search123") is False
        assert validate_search_id("s") is False
        assert validate_search_id("invalid-uuid") is False
        assert validate_search_id("") is False