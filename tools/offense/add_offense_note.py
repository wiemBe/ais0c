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
#
# Modified in the ais0c platform fork (T-040): note_text is sent in a form body, not in the URL.


"""
Add Offense Note Tool

Adds investigation notes to offenses in QRadar SIEM.
"""

from typing import Dict, Any, Optional
import json
from urllib.parse import urlencode
from qradar_mcp.tools.base import MCPTool
from qradar_mcp.tools.schema import schema
from qradar_mcp.tools import endpoints
from qradar_mcp.utils.validators import validate_offense_id, validate_note_text

# QRadar reads note_text from the query string or from a form body. In the URL a Turkish letter
# takes six characters once percent-encoded, so a long note passes the 8190-byte request line of
# QRadar's web server, which refuses it with HTTP 414. The charset makes the server decode the
# percent-encoded bytes as UTF-8.
FORM_CONTENT_TYPE = "application/x-www-form-urlencoded; charset=UTF-8"


class AddOffenseNoteTool(MCPTool):
    """Tool for adding notes to QRadar offenses."""

    @property
    def name(self) -> str:
        return "add_offense_note"

    @property
    def description(self) -> str:
        return """Add investigation notes to an offense in QRadar SIEM.

Use cases:
  - Document investigation findings
  - Record analyst observations
  - Track remediation steps
  - Add context for other analysts

The note will be timestamped and attributed to the current user."""

    @property
    def input_schema(self) -> Dict[str, Any]:
        return (schema()
            .integer("offense_id")
                .description("The ID of the offense to add the note to")
                .minimum(0)
                .required()
            .string("note_text")
                .description("The text content of the note")
                .min_length(1)
                .max_length(10000)
                .required()
            .string("fields")
                .description("Comma-separated list of fields to return in response")
            .build())

    @property
    def http_verb(self) -> str:
        return "POST"

    @property
    def endpoint(self) -> str:
        return endpoints.SIEM_OFFENSE_NOTES

    async def _execute_impl(self, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute the add_offense_note tool.

        Args:
            arguments: Dictionary containing offense_id and note_text

        Returns:
            MCP response with created note data or error
        """
        # Validate and extract parameters
        validation_result = self._validate_arguments(arguments)
        if validation_result:
            return validation_result

        offense_id = arguments["offense_id"]
        note_text = arguments["note_text"]
        fields = arguments.get("fields")

        # Make API request
        note = await self._add_note(offense_id, note_text, fields)

        # Return formatted response
        return self.create_success_response(json.dumps(note, indent=2))

    def _validate_arguments(self, arguments: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Validate input arguments. Returns error response if invalid, None if valid."""
        offense_id = arguments.get("offense_id")
        note_text = arguments.get("note_text")

        if offense_id is None:
            return self.create_error_response("offense_id is required")

        if not validate_offense_id(offense_id):
            return self.create_error_response(f"Invalid offense_id: {offense_id}")

        if note_text is None:
            return self.create_error_response("note_text is required")

        is_valid, error_msg = validate_note_text(note_text)
        if not is_valid:
            return self.create_error_response(error_msg or "Invalid note text")

        return None

    async def _add_note(
        self,
        offense_id: int,
        note_text: str,
        fields: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Add a note to the offense via QRadar API.

        Args:
            offense_id: The offense ID to add the note to
            note_text: The text content of the note
            fields: Optional fields parameter to limit response

        Returns:
            Created note data as dictionary

        Raises:
            RuntimeError: If the API request fails
        """
        api_path = self.endpoint.format(offense_id=offense_id)

        # The note goes in a form body; only the optional fields parameter stays in the URL.
        params = {"fields": fields} if fields else None

        response = await self.client.post(
            api_path=api_path,
            headers={"Content-Type": FORM_CONTENT_TYPE},
            params=params,
            data=urlencode({"note_text": note_text}),
        )
        response.raise_for_status()
        return response.json()
