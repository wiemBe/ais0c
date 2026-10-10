# SPDX-License-Identifier: Apache-2.0
"""How each tool of a profile is exposed: input patches and output schema.

Every tool registered in any profile needs an entry here; the server refuses to start
otherwise. Output schemas describe what QRadar returns, loosely: known fields are typed,
unknown fields are allowed. Item schemas of tools that take a ``fields`` argument require
nothing, because ``fields`` lets the caller drop any field. The lab contract tests
(tests/lab) check these schemas against a real QRadar.

The exported copies of these schemas live in snapshots/tools/. Changing anything here
changes the export, and CI fails until the snapshot is regenerated and reviewed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

JsonSchema = dict[str, Any]

# Ariel search IDs are UUIDs. The pattern keeps "/" and "." out of the URL path.
SEARCH_ID_PATTERN = r"^[A-Za-z0-9_-]{1,128}$"
# Reference data names become a URL path segment: no separators, no "." or "..".
REFERENCE_NAME_PATTERN = r"^(?!\.{1,2}$)[^/\\?#%]{1,255}$"
DEFAULT_RESULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 10_000
# QRadar holds a status request open for up to wait_seconds; it must end before the
# HTTP client gives up (MCP_HTTPX_TIMEOUT, 30 seconds by default).
MAX_STATUS_WAIT_SECONDS = 20


@dataclass(frozen=True)
class ToolSpec:
    output_schema: JsonSchema
    list_output: bool = False
    """Upstream returns a JSON array; it is exposed as ``{"items": [...]}``."""
    fixed_arguments: Mapping[str, Any] = field(default_factory=dict)
    """Always sent to the upstream tool and hidden from callers."""
    property_patches: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    """JSON Schema keywords merged into upstream input properties."""
    description: str | None = None
    """Replaces the upstream description when the behaviour differs from upstream."""


def _nullable(type_name: str) -> JsonSchema:
    return {"type": [type_name, "null"]}


_INT: JsonSchema = {"type": "integer"}
_STR: JsonSchema = {"type": "string"}
_BOOL: JsonSchema = {"type": "boolean"}
_N_INT = _nullable("integer")
_N_STR = _nullable("string")
_N_NUM = _nullable("number")
_N_BOOL = _nullable("boolean")


def _array(items: JsonSchema) -> JsonSchema:
    return {"type": "array", "items": items}


def _object(properties: Mapping[str, JsonSchema], required: tuple[str, ...] = ()) -> JsonSchema:
    schema: JsonSchema = {
        "type": "object",
        "properties": dict(properties),
        "additionalProperties": True,
    }
    if required:
        schema["required"] = list(required)
    return schema


def _items(item: JsonSchema) -> JsonSchema:
    return _object({"items": _array(item)}, required=("items",))


_ANY_OBJECT = _object({})

_OFFENSE: dict[str, JsonSchema] = {
    "id": _INT,
    "description": _STR,
    "status": {"type": "string", "enum": ["OPEN", "HIDDEN", "CLOSED"]},
    "offense_type": _INT,
    "offense_source": _STR,
    "severity": _INT,
    "magnitude": _INT,
    "credibility": _INT,
    "relevance": _INT,
    "start_time": _INT,
    "last_updated_time": _INT,
    "last_persisted_time": _N_INT,
    "first_persisted_time": _N_INT,
    "close_time": _N_INT,
    "event_count": _INT,
    "flow_count": _INT,
    "device_count": _INT,
    "source_count": _INT,
    "local_destination_count": _INT,
    "remote_destination_count": _INT,
    "username_count": _INT,
    "category_count": _INT,
    "policy_category_count": _INT,
    "security_category_count": _INT,
    "categories": _array(_STR),
    "destination_networks": _array(_STR),
    "source_network": _N_STR,
    "source_address_ids": _array(_INT),
    "local_destination_address_ids": _array(_INT),
    "rules": _array(_object({"id": _INT, "type": _STR})),
    "log_sources": _array(_object({"id": _INT, "name": _STR, "type_id": _INT, "type_name": _STR})),
    "assigned_to": _N_STR,
    "closing_user": _N_STR,
    "closing_reason_id": _N_INT,
    "follow_up": _BOOL,
    "protected": _BOOL,
    "inactive": _BOOL,
    "domain_id": _N_INT,
}
# Fields the platform relies on; get_offense has no ``fields`` argument, so all are present.
_OFFENSE_REQUIRED = (
    "id",
    "description",
    "status",
    "offense_type",
    "offense_source",
    "start_time",
    "categories",
    "rules",
    "log_sources",
)

_OFFENSE_TYPE = _object(
    # property_name is null for the built-in "No Operation" type (seen on 7.6.0 FP1).
    {"id": _INT, "name": _STR, "property_name": _N_STR, "database_type": _STR, "custom": _BOOL}
)
_CLOSING_REASON = _object({"id": _INT, "text": _STR, "is_reserved": _BOOL, "is_deleted": _BOOL})
_ADDRESS_COMMON: dict[str, JsonSchema] = {
    "id": _INT,
    "magnitude": _N_INT,
    "network": _N_STR,
    "offense_ids": _array(_INT),
    "event_flow_count": _N_INT,
    "first_event_flow_seen": _N_INT,
    "last_event_flow_seen": _N_INT,
    "domain_id": _N_INT,
}
_SOURCE_ADDRESS = _object(
    {**_ADDRESS_COMMON, "source_ip": _STR, "local_destination_address_ids": _array(_INT)}
)
_LOCAL_DESTINATION_ADDRESS = _object(
    {**_ADDRESS_COMMON, "local_destination_ip": _STR, "source_address_ids": _array(_INT)}
)
_RULE = _object(
    {
        "id": _INT,
        "name": _STR,
        "type": _STR,
        "enabled": _BOOL,
        "owner": _N_STR,
        "origin": _STR,
        "base_capacity": _N_INT,
        "base_host_id": _N_INT,
        "average_capacity": _N_INT,
        "capacity_timestamp": _N_INT,
        "identifier": _N_STR,
        "linked_rule_identifier": _N_STR,
        "creation_date": _N_INT,
        "modification_date": _N_INT,
    }
)
_ASSET = _object(
    {
        "id": _INT,
        "domain_id": _N_INT,
        "hostnames": _array(_ANY_OBJECT),
        "interfaces": _array(_ANY_OBJECT),
        "products": _array(_ANY_OBJECT),
        "properties": _array(_ANY_OBJECT),
        "risk_score_sum": _N_NUM,
        "vulnerability_count": _N_INT,
    }
)
_LOG_SOURCE = _object(
    {
        "id": _INT,
        "name": _STR,
        "description": _N_STR,
        "type_id": _INT,
        "protocol_type_id": _N_INT,
        "enabled": _BOOL,
        "gateway": _BOOL,
        "internal": _BOOL,
        "credibility": _N_INT,
        "target_event_collector_id": _N_INT,
        "coalesce_events": _N_BOOL,
        "store_event_payload": _N_BOOL,
        "language_id": _N_INT,
        "group_ids": _array(_INT),
        "requires_deploy": _N_BOOL,
        "auto_discovered": _N_BOOL,
        "average_eps": _N_NUM,
        "creation_date": _N_INT,
        "modified_date": _N_INT,
        "last_event_time": _N_INT,
        "status": _nullable("object"),
    }
)
_LOG_SOURCE_TYPE = _object(
    {
        "id": _INT,
        "name": _STR,
        "internal": _BOOL,
        "custom": _BOOL,
        "default_protocol_id": _N_INT,
        "protocol_types": _array(_ANY_OBJECT),
        "language_ids": _array(_INT),
        "log_source_extension_id": _N_INT,
        "uuid": _N_STR,
    }
)
_REFERENCE_SET = _object(
    {"id": _INT, "name": _STR, "namespace": _N_STR, "entry_type": _N_STR, "description": _N_STR}
)
_REFERENCE_ENTRY = _object(
    {"value": _STR, "source": _N_STR, "first_seen": _N_INT, "last_seen": _N_INT}
)
_REFERENCE_COLLECTION: dict[str, JsonSchema] = {
    "name": _STR,
    "element_type": _STR,
    "number_of_elements": _INT,
    "timeout_type": _N_STR,
    "time_to_live": _N_STR,
    "creation_time": _N_INT,
    "namespace": _N_STR,
    "key_label": _N_STR,
}
_REFERENCE_MAP = _object(
    {
        **_REFERENCE_COLLECTION,
        "value_label": _N_STR,
        "data": {"type": "object", "additionalProperties": _REFERENCE_ENTRY},
    }
)
_REFERENCE_TABLE = _object(
    {
        **_REFERENCE_COLLECTION,
        "key_name_types": {"type": "object", "additionalProperties": _STR},
        "data": {
            "type": "object",
            "additionalProperties": {"type": "object", "additionalProperties": _REFERENCE_ENTRY},
        },
    }
)
_ARIEL_SEARCH = _object(
    {
        "search_id": _STR,
        "cursor_id": _STR,
        "status": {
            "type": "string",
            "enum": ["WAIT", "EXECUTE", "SORTING", "COMPLETED", "CANCELED", "ERROR"],
        },
        "query_string": _STR,
        "progress": {"type": "number"},
        "progress_details": _array({"type": "number"}),
        "record_count": _INT,
        "processed_record_count": _INT,
        "save_results": _BOOL,
        "completed": _BOOL,
        "desired_retention_time_msec": _INT,
        "query_execution_time": _INT,
        "error_messages": _array(_ANY_OBJECT),
        "subsearch_ids": _array(_STR),
        "data_file_count": _INT,
        "data_total_size": _INT,
        "index_file_count": _INT,
        "index_total_size": _INT,
        "compressed_data_file_count": _INT,
        "compressed_data_total_size": _INT,
    },
    required=("search_id", "status"),
)
_ARIEL_RESULTS: JsonSchema = {
    "type": "object",
    "description": 'Result rows keyed by the queried table, e.g. {"events": [...]}.',
    "additionalProperties": _array(_ANY_OBJECT),
}
_NOTE: dict[str, JsonSchema] = {
    "id": _INT,
    "note_text": _STR,
    "create_time": _INT,
    "username": _STR,
}

_NO_FORMATTING = {"format_output": False}
_LIMIT_CAP = {"limit": {"maximum": MAX_PAGE_SIZE}}
_SEARCH_ID = {"search_id": {"pattern": SEARCH_ID_PATTERN}}
_REFERENCE_NAME = {"name": {"pattern": REFERENCE_NAME_PATTERN}, "limit": {"default": 50}}

TOOL_SPECS: dict[str, ToolSpec] = {
    # Offenses
    "get_offense": ToolSpec(output_schema=_object(_OFFENSE, required=_OFFENSE_REQUIRED)),
    "list_offenses": ToolSpec(
        output_schema=_object(
            {"offenses": _array(_object(_OFFENSE)), "count": _INT, "total_count": _INT},
            required=("offenses", "count"),
        ),
        fixed_arguments=_NO_FORMATTING,
    ),
    "list_offense_types": ToolSpec(
        output_schema=_items(_OFFENSE_TYPE), list_output=True, property_patches=_LIMIT_CAP
    ),
    "list_offense_closing_reasons": ToolSpec(
        output_schema=_items(_CLOSING_REASON), list_output=True, property_patches=_LIMIT_CAP
    ),
    "list_source_addresses": ToolSpec(
        output_schema=_items(_SOURCE_ADDRESS), list_output=True, property_patches=_LIMIT_CAP
    ),
    "list_local_destination_addresses": ToolSpec(
        output_schema=_items(_LOCAL_DESTINATION_ADDRESS),
        list_output=True,
        property_patches=_LIMIT_CAP,
    ),
    # Rules
    "list_rules": ToolSpec(
        output_schema=_items(_RULE), list_output=True, fixed_arguments=_NO_FORMATTING
    ),
    "get_rule": ToolSpec(output_schema=_RULE),
    # Assets
    "list_assets": ToolSpec(
        output_schema=_items(_ASSET), list_output=True, fixed_arguments=_NO_FORMATTING
    ),
    # Log sources
    "list_log_sources": ToolSpec(
        output_schema=_items(_LOG_SOURCE), list_output=True, fixed_arguments=_NO_FORMATTING
    ),
    "get_log_source": ToolSpec(output_schema=_LOG_SOURCE),
    "list_log_source_types": ToolSpec(
        output_schema=_items(_LOG_SOURCE_TYPE), list_output=True, property_patches=_LIMIT_CAP
    ),
    # Reference data (read only)
    "list_reference_sets": ToolSpec(
        output_schema=_items(_REFERENCE_SET), list_output=True, fixed_arguments=_NO_FORMATTING
    ),
    "get_reference_set": ToolSpec(output_schema=_REFERENCE_SET),
    "list_reference_maps": ToolSpec(
        output_schema=_items(_object(_REFERENCE_COLLECTION)),
        list_output=True,
        fixed_arguments=_NO_FORMATTING,
    ),
    "get_reference_map": ToolSpec(output_schema=_REFERENCE_MAP, property_patches=_REFERENCE_NAME),
    "list_reference_tables": ToolSpec(
        output_schema=_items(_object(_REFERENCE_COLLECTION)),
        list_output=True,
        fixed_arguments=_NO_FORMATTING,
    ),
    "get_reference_table": ToolSpec(
        output_schema=_REFERENCE_TABLE, property_patches=_REFERENCE_NAME
    ),
    # Ariel search lifecycle
    "create_ariel_search": ToolSpec(output_schema=_ARIEL_SEARCH),
    "get_ariel_search_status": ToolSpec(
        output_schema=_ARIEL_SEARCH,
        property_patches={
            **_SEARCH_ID,
            "wait_seconds": {"maximum": MAX_STATUS_WAIT_SECONDS},
        },
    ),
    "get_ariel_search_results": ToolSpec(
        output_schema=_ARIEL_RESULTS,
        property_patches={
            **_SEARCH_ID,
            "start": {"default": 0},
            "limit": {
                "default": DEFAULT_RESULT_PAGE_SIZE,
                "description": f"Rows in this page (default {DEFAULT_RESULT_PAGE_SIZE}).",
            },
        },
        description=(
            "Retrieve one page of results of a completed Ariel search. The page is selected "
            f"with start and limit (default {DEFAULT_RESULT_PAGE_SIZE} rows) and requested "
            "from QRadar with a Range header. Use record_count from get_ariel_search_status "
            "to page through all rows."
        ),
    ),
    "delete_ariel_search": ToolSpec(output_schema=_ARIEL_SEARCH, property_patches=_SEARCH_ID),
    # Offense notes (qradar-note profile)
    "add_offense_note": ToolSpec(output_schema=_object(_NOTE)),
    "get_offense_notes": ToolSpec(
        output_schema=_object(
            {"offense_id": _INT, "total_notes": _INT, "notes": _array(_object(_NOTE))},
            required=("offense_id", "total_notes", "notes"),
        )
    ),
}
