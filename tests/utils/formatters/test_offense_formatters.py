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
Unit tests for offense-related formatters.
"""

from qradar_mcp.utils.formatters import (
    format_offense_summary,
    format_offense_list,
    format_note,
    format_notes_list
)


class TestFormatOffenseSummary:
    """Tests for format_offense_summary function."""

    def test_format_complete_offense(self):
        """Test formatting offense with all fields."""
        offense = {
            "id": 123,
            "description": "Test Offense",
            "status": "OPEN",
            "severity": 5,
            "magnitude": 3,
            "credibility": 4,
            "relevance": 5,
            "assigned_to": "analyst1",
            "follow_up": True,
            "protected": False,
            "start_time": 1705324245000,
            "last_updated_time": 1705324345000,
            "close_time": None,
            "event_count": 100,
            "flow_count": 50,
            "source_count": 5,
            "local_destination_count": 3,
            "category_count": 2,
            "categories": ["Suspicious Activity", "Malware"]
        }

        result = format_offense_summary(offense)

        assert "Offense ID: 123" in result
        assert "Test Offense" in result
        assert "Status: OPEN" in result
        assert "Severity: 5" in result
        assert "Magnitude: 3" in result
        assert "Assigned To: analyst1" in result
        assert "Follow Up: Yes" in result
        assert "Protected: No" in result
        assert "Event Count: 100" in result
        assert "Suspicious Activity" in result
        assert "Malware" in result

    def test_format_minimal_offense(self):
        """Test formatting offense with minimal fields."""
        offense = {"id": 456}

        result = format_offense_summary(offense)

        assert "Offense ID: 456" in result
        assert "N/A" in result
        assert "Unassigned" in result

    def test_format_offense_with_many_categories(self):
        """Test formatting offense with more than 10 categories."""
        offense = {
            "id": 789,
            "categories": [f"Category {i}" for i in range(15)]
        }

        result = format_offense_summary(offense)

        assert "Category 0" in result
        assert "Category 9" in result
        assert "and 5 more" in result
        assert "Category 14" not in result


class TestFormatOffenseList:
    """Tests for format_offense_list function."""

    def test_format_empty_list(self):
        """Test formatting empty offense list."""
        result = format_offense_list([])
        assert result == "No offenses found."

    def test_format_single_offense(self):
        """Test formatting list with single offense."""
        offenses = [{
            "id": 1,
            "status": "OPEN",
            "severity": 5,
            "magnitude": 3,
            "event_count": 100,
            "description": "Test Offense"
        }]

        result = format_offense_list(offenses)

        assert "1" in result
        assert "OPEN" in result
        assert "5" in result
        assert "3" in result
        assert "100" in result
        assert "Test Offense" in result
        assert "Total: 1 offenses" in result

    def test_format_multiple_offenses(self):
        """Test formatting list with multiple offenses."""
        offenses = [
            {"id": 1, "status": "OPEN", "severity": 5, "magnitude": 3,
             "event_count": 100, "description": "Offense 1"},
            {"id": 2, "status": "CLOSED", "severity": 3, "magnitude": 2,
             "event_count": 50, "description": "Offense 2"}
        ]

        result = format_offense_list(offenses)

        assert "1" in result
        assert "2" in result
        assert "OPEN" in result
        assert "CLOSED" in result
        assert "Total: 2 offenses" in result

    def test_format_with_total_count(self):
        """Test formatting with total count greater than returned."""
        offenses = [{"id": 1, "status": "OPEN", "severity": 5,
                    "magnitude": 3, "event_count": 100,
                    "description": "Test"}]

        result = format_offense_list(offenses, total_count=100)

        assert "Showing 1 of 100 total offenses" in result

    def test_format_truncates_long_description(self):
        """Test that long descriptions are truncated."""
        offenses = [{
            "id": 1,
            "status": "OPEN",
            "severity": 5,
            "magnitude": 3,
            "event_count": 100,
            "description": "A" * 100  # 100 character description
        }]

        result = format_offense_list(offenses)

        # Description should be truncated to 50 chars
        lines = result.split("\n")
        data_line = [l for l in lines if l.startswith("1")][0]
        assert len(data_line) <= 120  # Total line width


class TestFormatNote:
    """Tests for format_note function."""

    def test_format_complete_note(self):
        """Test formatting note with all fields."""
        note = {
            "create_time": 1705324245000,
            "username": "analyst1",
            "note_text": "Investigation findings"
        }

        result = format_note(note)

        assert "2024-01-15" in result or "2024-01-14" in result  # Timezone dependent
        assert "analyst1" in result
        assert "Investigation findings" in result

    def test_format_note_missing_fields(self):
        """Test formatting note with missing fields."""
        note = {}

        result = format_note(note)

        assert "Unknown" in result
        assert "N/A" in result


class TestFormatNotesList:
    """Tests for format_notes_list function."""

    def test_format_empty_notes_list(self):
        """Test formatting empty notes list."""
        result = format_notes_list([])
        assert result == "No notes found."

    def test_format_single_note(self):
        """Test formatting list with single note."""
        notes = [{
            "create_time": 1705324245000,
            "username": "analyst1",
            "note_text": "Test note"
        }]

        result = format_notes_list(notes)

        assert "Total Notes: 1" in result
        assert "analyst1" in result
        assert "Test note" in result

    def test_format_multiple_notes(self):
        """Test formatting list with multiple notes."""
        notes = [
            {"create_time": 1705324245000, "username": "analyst1",
             "note_text": "Note 1"},
            {"create_time": 1705324345000, "username": "analyst2",
             "note_text": "Note 2"}
        ]

        result = format_notes_list(notes)

        assert "Total Notes: 2" in result
        assert "analyst1" in result
        assert "analyst2" in result
        assert "Note 1" in result
        assert "Note 2" in result