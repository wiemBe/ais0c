# SPDX-License-Identifier: Apache-2.0
"""In-process fake of the QRadar REST API for unit tests.

Served through ``httpx.MockTransport``. All data is synthetic: documentation IP ranges
(RFC 5737), example.com names and a documentation MAC address (RFC 7042).
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any
from urllib.parse import parse_qs, unquote

import httpx

QRADAR_TOKEN = "qradar-test-token-0b5f7c1e-6a43-4f7e-9d0c-2f4b8a1c9e77"
MCP_TOKEN = "mcp-test-token-4d2a9e61c8b74f0f9a3e5b17d6c08e2a"
CONSOLE_HOST = "qradar.example.com"

OFFENSE_ID = 42
RULE_ID = 100270
LOG_SOURCE_ID = 112
REFERENCE_SET_ID = 5
REFERENCE_MAP = "watched_users"
REFERENCE_TABLE = "asset_owners"
# The longest request line (method, path with query, protocol) QRadar's web server takes; a
# longer one gets 414 before QRadar sees the request. Apache's default LimitRequestLine, and what
# the lab QRadar does (T-019: a query of 8099 characters passed, one of 8159 got 414).
MAX_REQUEST_LINE = 8190

DEFAULT_VERSIONS: list[dict[str, Any]] = [
    {"id": 1, "version": "26.0", "deprecated": True, "removed": False},
    {"id": 2, "version": "27.0", "deprecated": False, "removed": False},
    {"id": 3, "version": "28.0", "deprecated": False, "removed": False},
    {"id": 4, "version": "29.0", "deprecated": False, "removed": False},
    {"id": 5, "version": "30.0", "deprecated": False, "removed": False},
]

OFFENSE: dict[str, Any] = {
    "id": OFFENSE_ID,
    "description": "Multiple Login Failures for the Same User\n",
    "status": "OPEN",
    "offense_type": 3,
    "offense_source": "alice",
    "severity": 5,
    "magnitude": 4,
    "credibility": 3,
    "relevance": 4,
    "start_time": 1790900000000,
    "last_updated_time": 1790903600000,
    "last_persisted_time": 1790903600000,
    "first_persisted_time": 1790900000000,
    "close_time": None,
    "event_count": 37,
    "flow_count": 0,
    "device_count": 1,
    "source_count": 1,
    "local_destination_count": 1,
    "remote_destination_count": 0,
    "username_count": 1,
    "category_count": 1,
    "policy_category_count": 0,
    "security_category_count": 1,
    "categories": ["User Login Failure"],
    "destination_networks": ["other"],
    "source_network": "other",
    "source_address_ids": [7],
    "local_destination_address_ids": [9],
    "rules": [{"id": RULE_ID, "type": "CRE_RULE"}],
    "log_sources": [
        {
            "id": LOG_SOURCE_ID,
            "name": "WindowsAuthServer @ dc01.example.com",
            "type_id": 12,
            "type_name": "WindowsAuthServer",
        }
    ],
    "assigned_to": None,
    "closing_user": None,
    "closing_reason_id": None,
    "follow_up": False,
    "protected": False,
    "inactive": False,
    "domain_id": 0,
}
RULE: dict[str, Any] = {
    "id": RULE_ID,
    "name": "Multiple Login Failures for Single Username",
    "type": "EVENT",
    "enabled": True,
    "owner": "admin",
    "origin": "SYSTEM",
    "base_capacity": 1,
    "base_host_id": 53,
    "average_capacity": 1,
    "capacity_timestamp": 1790900000000,
    "identifier": "SYSTEM-1234",
    "linked_rule_identifier": None,
    "creation_date": 1600000000000,
    "modification_date": 1700000000000,
}
ASSET: dict[str, Any] = {
    "id": 1001,
    "domain_id": 0,
    "hostnames": [{"id": 1, "name": "ws01.example.com", "type": "DNS"}],
    "interfaces": [
        {
            "id": 2,
            "mac_address": "00:00:5E:00:53:01",
            "ip_addresses": [{"id": 3, "value": "192.0.2.10", "type": "IPV4"}],
        }
    ],
    "products": [],
    "properties": [{"id": 4, "name": "Unified Name", "type_id": 1002, "value": "ws01"}],
    "risk_score_sum": 0.0,
    "vulnerability_count": 0,
}
LOG_SOURCE: dict[str, Any] = {
    "id": LOG_SOURCE_ID,
    "name": "WindowsAuthServer @ dc01.example.com",
    "description": "Synthetic domain controller",
    "type_id": 12,
    "protocol_type_id": 0,
    "enabled": True,
    "gateway": False,
    "internal": False,
    "credibility": 5,
    "target_event_collector_id": 7,
    "coalesce_events": True,
    "store_event_payload": True,
    "language_id": 1,
    "group_ids": [0],
    "requires_deploy": False,
    "auto_discovered": False,
    "average_eps": 3,
    "creation_date": 1600000000000,
    "modified_date": 1700000000000,
    "last_event_time": 1790903600000,
    "status": {"status": "SUCCESS", "last_updated": 0, "messages": []},
}
LOG_SOURCE_TYPE: dict[str, Any] = {
    "id": 12,
    "name": "Microsoft Windows Security Event Log",
    "internal": False,
    "custom": False,
    "default_protocol_id": 0,
    "protocol_types": [{"protocol_id": 0, "documented": True}],
    "language_ids": [1],
    "log_source_extension_id": None,
    "uuid": "3f8a6a52-1d7c-4b8e-9b1e-0c2d4e6f8a10",
}
REFERENCE_SET: dict[str, Any] = {
    "id": REFERENCE_SET_ID,
    "name": "Suspicious IPs",
    "namespace": "SHARED",
    "entry_type": "IP",
    "description": "Synthetic reference set",
}
_ENTRY = {"value": "watch", "source": "reference data api", "first_seen": 1, "last_seen": 2}
REFERENCE_MAP_META: dict[str, Any] = {
    "name": REFERENCE_MAP,
    "element_type": "ALNIC",
    "number_of_elements": 1,
    "timeout_type": "UNKNOWN",
    "creation_time": 1600000000000,
    "namespace": "SHARED",
    "key_label": "user",
    "value_label": "reason",
}
REFERENCE_TABLE_META: dict[str, Any] = {
    "name": REFERENCE_TABLE,
    "element_type": "ALN",
    "number_of_elements": 1,
    "timeout_type": "UNKNOWN",
    "creation_time": 1600000000000,
    "key_label": "ip",
    "key_name_types": {"owner": "ALN"},
}
ARIEL_ROWS = [
    {"sourceip": "198.51.100.23", "username": "alice", "qid": 5000001},
    {"sourceip": "198.51.100.24", "username": "alice", "qid": 5000001},
    {"sourceip": "203.0.113.7", "username": "bob", "qid": 5000002},
]

_RANGE = re.compile(r"^items=(\d+)-(\d+)$")


class FakeQRadar:
    """Answers QRadar REST calls and records every request it receives."""

    def __init__(
        self,
        *,
        versions: list[dict[str, Any]] | None = None,
        token: str = QRADAR_TOKEN,
        reflect_token_in_errors: bool = False,
    ) -> None:
        self.versions = DEFAULT_VERSIONS if versions is None else versions
        self.token = token
        self.reflect_token_in_errors = reflect_token_in_errors
        self.requests: list[httpx.Request] = []
        self.searches: dict[str, dict[str, Any]] = {}
        self.notes: list[dict[str, Any]] = [
            {
                "id": 1,
                "note_text": "Synthetic analyst note",
                "create_time": 1790903000000,
                "username": "analyst1",
            }
        ]

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def api_requests(self) -> list[httpx.Request]:
        """Requests other than API version discovery."""
        return [r for r in self.requests if r.url.path != "/api/help/versions"]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        request_line = f"{request.method} {request.url.raw_path.decode('ascii')} HTTP/1.1"
        if len(request_line) > MAX_REQUEST_LINE:
            return self._error(request, 414, "Request-URI Too Long")
        if request.headers.get("SEC") != self.token:
            return self._error(
                request, 401, "You are unauthorized to access the requested resource."
            )
        path = unquote(request.url.path)
        if not path.startswith("/api/"):
            return self._error(request, 404, "Not an API path")
        route = path.removeprefix("/api/").rstrip("/")
        method = request.method
        if method == "GET":
            return self._get(request, route)
        if method == "POST":
            return self._post(request, route)
        if method == "DELETE":
            return self._delete(request, route)
        return self._error(request, 405, f"{method} is not supported")

    def _get(self, request: httpx.Request, route: str) -> httpx.Response:
        parts = route.split("/")
        simple_lists: dict[str, list[dict[str, Any]]] = {
            "siem/offenses": [OFFENSE],
            "siem/offense_types": [
                {
                    "id": 3,
                    "name": "Username",
                    "property_name": "userName",
                    "database_type": "COMMON",
                    "custom": False,
                }
            ],
            "siem/offense_closing_reasons": [
                {
                    "id": 1,
                    "text": "False-Positive, Tuned",
                    "is_reserved": False,
                    "is_deleted": False,
                }
            ],
            "siem/source_addresses": [
                {
                    "id": 7,
                    "source_ip": "198.51.100.23",
                    "magnitude": 4,
                    "network": "other",
                    "offense_ids": [OFFENSE_ID],
                    "local_destination_address_ids": [9],
                    "event_flow_count": 37,
                    "first_event_flow_seen": 1790900000000,
                    "last_event_flow_seen": 1790903600000,
                    "domain_id": 0,
                }
            ],
            "siem/local_destination_addresses": [
                {
                    "id": 9,
                    "local_destination_ip": "192.0.2.10",
                    "magnitude": 4,
                    "network": "other",
                    "offense_ids": [OFFENSE_ID],
                    "source_address_ids": [7],
                    "event_flow_count": 37,
                    "first_event_flow_seen": 1790900000000,
                    "last_event_flow_seen": 1790903600000,
                    "domain_id": 0,
                }
            ],
            "analytics/rules": [RULE],
            "asset_model/assets": [ASSET],
            "config/event_sources/log_source_management/log_sources": [LOG_SOURCE],
            "config/event_sources/log_source_management/log_source_types": [LOG_SOURCE_TYPE],
            "reference_data_collections/sets": [REFERENCE_SET],
            "reference_data/maps": [REFERENCE_MAP_META],
            "reference_data/tables": [REFERENCE_TABLE_META],
        }
        if route == "help/versions":
            return httpx.Response(200, json=self.versions)
        if route in simple_lists:
            items = simple_lists[route]
            return httpx.Response(
                200, json=items, headers={"Content-Range": f"items 0-{len(items) - 1}/{len(items)}"}
            )
        singles: dict[str, dict[str, Any]] = {
            f"siem/offenses/{OFFENSE_ID}": OFFENSE,
            f"analytics/rules/{RULE_ID}": RULE,
            f"config/event_sources/log_source_management/log_sources/{LOG_SOURCE_ID}": LOG_SOURCE,
            f"reference_data_collections/sets/{REFERENCE_SET_ID}": REFERENCE_SET,
            f"reference_data/maps/{REFERENCE_MAP}": {
                **REFERENCE_MAP_META,
                "data": {"alice": _ENTRY},
            },
            f"reference_data/tables/{REFERENCE_TABLE}": {
                **REFERENCE_TABLE_META,
                "data": {"192.0.2.10": {"owner": _ENTRY}},
            },
        }
        if route in singles:
            return httpx.Response(200, json=singles[route])
        if route == f"siem/offenses/{OFFENSE_ID}/notes":
            return httpx.Response(200, json=self.notes)
        if parts[:2] == ["ariel", "searches"] and len(parts) in (3, 4):
            search = self.searches.get(parts[2])
            if search is None:
                return self._error(request, 404, f"Search {parts[2]} does not exist")
            if len(parts) == 3:
                return httpx.Response(200, json=search)
            if parts[3] == "results":
                return self._results(request)
        return self._error(request, 404, f"No such endpoint: GET {route}")

    def _results(self, request: httpx.Request) -> httpx.Response:
        rows = ARIEL_ROWS
        match = _RANGE.match(request.headers.get("Range", ""))
        if match:
            start, end = int(match.group(1)), int(match.group(2))
            rows = rows[start : end + 1]
        return httpx.Response(200, json={"events": rows})

    def _post(self, request: httpx.Request, route: str) -> httpx.Response:
        if route == "ariel/searches":
            query = request.url.params.get("query_expression")
            if not query:
                return self._error(request, 422, "query_expression is required")
            search_id = str(uuid.uuid4())
            search = {
                "search_id": search_id,
                "cursor_id": search_id,
                "status": "COMPLETED",
                "query_string": query,
                "progress": 100,
                "progress_details": [],
                "record_count": len(ARIEL_ROWS),
                "processed_record_count": len(ARIEL_ROWS),
                "save_results": False,
                "desired_retention_time_msec": 86400000,
                "query_execution_time": 12,
                "error_messages": [],
                "subsearch_ids": [],
                "data_file_count": 1,
                "data_total_size": 512,
                "index_file_count": 0,
                "index_total_size": 0,
                "compressed_data_file_count": 0,
                "compressed_data_total_size": 0,
            }
            self.searches[search_id] = search
            return httpx.Response(201, json=search)
        if route == f"siem/offenses/{OFFENSE_ID}/notes":
            note = {
                "id": len(self.notes) + 1,
                "note_text": _note_text(request),
                "create_time": 1790904000000,
                "username": "ais0c-executor",
            }
            self.notes.append(note)
            return httpx.Response(201, json=note)
        return self._error(request, 404, f"No such endpoint: POST {route}")

    def _delete(self, request: httpx.Request, route: str) -> httpx.Response:
        parts = route.split("/")
        if parts[:2] == ["ariel", "searches"] and len(parts) == 3:
            search = self.searches.pop(parts[2], None)
            if search is None:
                return self._error(request, 404, f"Search {parts[2]} does not exist")
            return httpx.Response(202, json=search)
        return self._error(request, 404, f"No such endpoint: DELETE {route}")

    def _error(self, request: httpx.Request, status: int, message: str) -> httpx.Response:
        if self.reflect_token_in_errors:
            message = f"{message} (SEC header was {request.headers.get('SEC')})"
        body = {
            "http_response": {"code": status, "message": message},
            "code": status,
            "message": message,
        }
        return httpx.Response(
            status, content=json.dumps(body), headers={"Content-Type": "application/json"}
        )


def _note_text(request: httpx.Request) -> str:
    """note_text of a new note: QRadar takes it from the query string or from a form body."""
    if "note_text" in request.url.params:
        return request.url.params["note_text"]
    return form_fields(request).get("note_text", [""])[0]


def form_fields(request: httpx.Request) -> dict[str, list[str]]:
    """The fields of an application/x-www-form-urlencoded body.

    Percent-encoded bytes are decoded in the charset the Content-Type names. Without one, a
    servlet container decodes them as ISO-8859-1, and so does the fake: a note of Turkish
    letters sent without the charset arrives garbled.
    """
    media_type, _, parameters = request.headers.get("Content-Type", "").partition(";")
    if media_type.strip().lower() != "application/x-www-form-urlencoded":
        return {}
    charset = "iso-8859-1"
    for parameter in parameters.split(";"):
        name, _, value = parameter.strip().partition("=")
        if name.lower() == "charset" and value:
            charset = value.strip('"')
    return parse_qs(request.content.decode("ascii"), keep_blank_values=True, encoding=charset)
