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
Tests for data validator functions.
"""

from qradar_mcp.utils.validators import (
    validate_range,
    validate_note_text
)


class TestValidateRange:
    """Tests for validate_range function."""

    def test_valid_range(self):
        """Test with valid range."""
        is_valid, error = validate_range(0, 49)
        assert is_valid is True
        assert error is None

    def test_negative_start(self):
        """Test with negative start."""
        is_valid, error = validate_range(-1, 49)
        assert is_valid is False
        assert "non-negative" in error

    def test_end_before_start(self):
        """Test with end before start."""
        is_valid, error = validate_range(50, 49)
        assert is_valid is False
        assert "greater than" in error

    def test_range_too_large(self):
        """Test with range exceeding maximum."""
        is_valid, error = validate_range(0, 10001)
        assert is_valid is False
        assert "too large" in error


class TestValidateNoteText:
    """Tests for validate_note_text function."""

    def test_valid_note(self):
        """Test with valid note text."""
        is_valid, error = validate_note_text("This is a valid note")
        assert is_valid is True
        assert error is None

    def test_empty_note(self):
        """Test with empty note."""
        is_valid, error = validate_note_text("")
        assert is_valid is False
        assert "empty" in error

    def test_note_too_long(self):
        """Test with note exceeding maximum length."""
        long_note = "x" * 10001
        is_valid, error = validate_note_text(long_note)
        assert is_valid is False
        assert "maximum length" in error

    def test_custom_max_length(self):
        """Test with custom maximum length."""
        is_valid, error = validate_note_text("test", max_length=3)
        assert is_valid is False