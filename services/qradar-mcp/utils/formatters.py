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
Response Formatters

Utilities for formatting QRadar API responses into human-readable text.
"""

from typing import Dict, List, Any, Optional
from datetime import datetime


def format_timestamp(timestamp_ms: Optional[int]) -> str:
    """
    Format milliseconds since epoch to readable datetime string.

    Args:
        timestamp_ms: Milliseconds since epoch

    Returns:
        Formatted datetime string or "N/A" if None
    """
    if timestamp_ms is None:
        return "N/A"
    try:
        dt = datetime.fromtimestamp(timestamp_ms / 1000.0)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, OSError):
        return f"Invalid timestamp: {timestamp_ms}"


def format_offense_summary(offense: Dict[str, Any]) -> str:
    """
    Format offense data into a human-readable summary.

    Args:
        offense: Offense dictionary from QRadar API

    Returns:
        Formatted offense summary string
    """
    lines = [
        "=" * 60,
        f"Offense ID: {offense.get('id', 'N/A')}",
        f"Description: {offense.get('description', 'N/A')}",
        f"Status: {offense.get('status', 'N/A')}",
        f"Severity: {offense.get('severity', 'N/A')}",
        f"Magnitude: {offense.get('magnitude', 'N/A')}",
        f"Credibility: {offense.get('credibility', 'N/A')}",
        f"Relevance: {offense.get('relevance', 'N/A')}",
        "-" * 60,
        f"Assigned To: {offense.get('assigned_to', 'Unassigned')}",
        f"Follow Up: {'Yes' if offense.get('follow_up') else 'No'}",
        f"Protected: {'Yes' if offense.get('protected') else 'No'}",
        "-" * 60,
        f"Start Time: {format_timestamp(offense.get('start_time'))}",
        f"Last Updated: {format_timestamp(offense.get('last_updated_time'))}",
        f"Close Time: {format_timestamp(offense.get('close_time'))}",
        "-" * 60,
        f"Event Count: {offense.get('event_count', 0)}",
        f"Flow Count: {offense.get('flow_count', 0)}",
        f"Source Count: {offense.get('source_count', 0)}",
        f"Destination Count: {offense.get('local_destination_count', 0)}",
        f"Category Count: {offense.get('category_count', 0)}",
        "=" * 60
    ]

    # Add categories if present
    categories = offense.get('categories', [])
    if categories:
        lines.append("Categories:")
        for cat in categories[:10]:  # Limit to first 10
            lines.append(f"  - {cat}")
        if len(categories) > 10:
            lines.append(f"  ... and {len(categories) - 10} more")
        lines.append("=" * 60)

    return "\n".join(lines)


def format_offense_list(offenses: List[Dict[str, Any]],
                       total_count: Optional[int] = None) -> str:
    """
    Format a list of offenses into a table-like summary.

    Args:
        offenses: List of offense dictionaries
        total_count: Total count from API (may be more than returned)

    Returns:
        Formatted offense list string
    """
    if not offenses:
        return "No offenses found."

    lines = [
        "=" * 120,
        f"{'ID':<8} {'Status':<10} {'Severity':<8} {'Magnitude':<9} {'Events':<8} {'Description':<50}",
        "=" * 120
    ]

    for offense in offenses:
        offense_id = str(offense.get('id', 'N/A'))
        status = offense.get('status', 'N/A')[:10]
        severity = str(offense.get('severity', 'N/A'))
        magnitude = str(offense.get('magnitude', 'N/A'))
        event_count = str(offense.get('event_count', 0))
        description = offense.get('description', 'N/A')[:50]

        lines.append(
            f"{offense_id:<8} {status:<10} {severity:<8} {magnitude:<9} {event_count:<8} {description:<50}"
        )

    lines.append("=" * 120)

    if total_count and total_count > len(offenses):
        lines.append(f"Showing {len(offenses)} of {total_count} total offenses")
    else:
        lines.append(f"Total: {len(offenses)} offenses")

    return "\n".join(lines)


def format_note(note: Dict[str, Any]) -> str:
    """
    Format a single offense note.

    Args:
        note: Note dictionary from QRadar API

    Returns:
        Formatted note string
    """
    timestamp = format_timestamp(note.get('create_time'))
    username = note.get('username', 'Unknown')
    text = note.get('note_text', '')

    return f"[{timestamp}] {username}:\n{text}\n"


def format_notes_list(notes: List[Dict[str, Any]]) -> str:
    """
    Format a list of offense notes.

    Args:
        notes: List of note dictionaries

    Returns:
        Formatted notes string
    """
    if not notes:
        return "No notes found."

    lines = [
        "=" * 80,
        f"Total Notes: {len(notes)}",
        "=" * 80
    ]

    for note in notes:
        lines.append(format_note(note))
        lines.append("-" * 80)

    return "\n".join(lines)


def format_search_status(search: Dict[str, Any]) -> str:
    """
    Format Ariel search status information.

    Args:
        search: Search status dictionary from QRadar API

    Returns:
        Formatted search status string
    """
    lines = [
        "=" * 60,
        f"Search ID: {search.get('search_id', 'N/A')}",
        f"Status: {search.get('status', 'N/A')}",
        f"Progress: {search.get('progress', 0)}%",
        "-" * 60,
        f"Query: {search.get('query_string', 'N/A')}",
        "-" * 60,
        f"Record Count: {search.get('record_count', 0)}",
        f"Processed Records: {search.get('processed_record_count', 0)}",
        f"Execution Time: {search.get('query_execution_time', 0)}ms",
        "=" * 60
    ]

    # Add error messages if present
    errors = search.get('error_messages', [])
    if errors:
        lines.append("Errors:")
        for error in errors:
            severity = error.get('severity', 'UNKNOWN')
            message = error.get('message', 'No message')
            lines.append(f"  [{severity}] {message}")
        lines.append("=" * 60)

    return "\n".join(lines)


def format_search_results(results: Dict[str, Any],
                         max_rows: int = 100) -> str:
    """
    Format Ariel search results into a readable table.

    Args:
        results: Search results dictionary from QRadar API
        max_rows: Maximum number of rows to display

    Returns:
        Formatted search results string
    """
    events = results.get('events', [])

    if not events:
        return "No results found."

    # Get column names from first event
    columns = list(events[0].keys()) if events else []

    lines = [
        "=" * 120,
        f"Search Results ({len(events)} rows)",
        "=" * 120
    ]

    # Header row
    header = " | ".join(f"{col[:15]:<15}" for col in columns)
    lines.append(header)
    lines.append("-" * 120)

    # Data rows
    for event in events[:max_rows]:
        row = " | ".join(
            f"{str(event.get(col, ''))[:15]:<15}"
            for col in columns
        )
        lines.append(row)

    if len(events) > max_rows:
        lines.append(f"... and {len(events) - max_rows} more rows")

    lines.append("=" * 120)

    return "\n".join(lines)


def format_reference_set(ref_set: Dict[str, Any],
                        data: Optional[List[Dict[str, Any]]] = None) -> str:
    """
    Format reference set information.

    Args:
        ref_set: Reference set metadata dictionary
        data: Optional list of reference set data entries

    Returns:
        Formatted reference set string
    """
    lines = [
        "=" * 60,
        f"Name: {ref_set.get('name', 'N/A')}",
        f"Element Type: {ref_set.get('element_type', 'N/A')}",
        f"Number of Elements: {ref_set.get('number_of_elements', 0)}",
        f"Timeout Type: {ref_set.get('timeout_type', 'N/A')}",
        "=" * 60
    ]

    if data:
        lines.append("Elements:")
        for item in data[:50]:  # Limit to first 50
            value = item.get('value', 'N/A')
            lines.append(f"  - {value}")
        if len(data) > 50:
            lines.append(f"  ... and {len(data) - 50} more")
        lines.append("=" * 60)

    return "\n".join(lines)


def format_asset(asset: Dict[str, Any]) -> str:
    """
    Format asset information.

    Args:
        asset: Asset dictionary from QRadar API

    Returns:
        Formatted asset string
    """
    lines = [
        "=" * 60,
        f"Asset ID: {asset.get('id', 'N/A')}",
        f"Hostname: {asset.get('hostname', 'N/A')}",
        "-" * 60
    ]

    # Add IP addresses
    interfaces = asset.get('interfaces', [])
    if interfaces:
        lines.append("IP Addresses:")
        for iface in interfaces:
            ip = iface.get('ip_addresses', [])
            for addr in ip:
                lines.append(f"  - {addr.get('value', 'N/A')}")

    lines.append("=" * 60)

    return "\n".join(lines)



def format_reference_sets_table(sets: List[Dict[str, Any]]) -> str:
    """
    Format a list of reference sets into a table-like summary.

    Args:
        sets: List of reference set dictionaries

    Returns:
        Formatted reference sets string
    """
    if not sets:
        return "No reference sets found."

    lines = [
        "=" * 140,
        f"{'ID':<6} {'Name':<30} {'Type':<8} {'Entries':<8} {'Namespace':<10} {'TTL':<10} {'Description':<40}",
        "=" * 140
    ]

    for ref_set in sets:
        set_id = str(ref_set.get('id', 'N/A'))
        name = (ref_set.get('name') or 'N/A')[:30]
        entry_type = (ref_set.get('entry_type') or 'N/A')[:8]
        num_entries = str(ref_set.get('number_of_entries', 0))
        namespace = (ref_set.get('namespace') or 'N/A')[:10]
        ttl = ref_set.get('time_to_live', 'N/A')
        ttl_str = str(ttl) if ttl != 'N/A' else 'N/A'
        description = (ref_set.get('description') or '')[:40]

        lines.append(
            f"{set_id:<6} {name:<30} {entry_type:<8} {num_entries:<8} "
            f"{namespace:<10} {ttl_str:<10} {description:<40}"
        )

    lines.append("=" * 140)
    lines.append(f"Total: {len(sets)} reference sets")

    return "\n".join(lines)


def format_reference_set_detail(ref_set: Dict[str, Any]) -> str:
    """
    Format reference set details into a human-readable summary.

    Args:
        ref_set: Reference set dictionary from QRadar API

    Returns:
        Formatted reference set detail string
    """
    lines = [
        "=" * 60,
        f"Reference Set ID: {ref_set.get('id', 'N/A')}",
        f"Name: {ref_set.get('name', 'N/A')}",
        f"Entry Type: {ref_set.get('entry_type', 'N/A')}",
        "-" * 60,
        f"Namespace: {ref_set.get('namespace', 'N/A')}",
        f"Tenant ID: {ref_set.get('tenant_id', 'N/A')}",
        f"Global ID: {ref_set.get('global_id', 'N/A')}",
        "-" * 60,
        f"Number of Entries: {ref_set.get('number_of_entries', 0)}",
        f"Creation Time: {format_timestamp(ref_set.get('creation_time'))}",
        "-" * 60,
        f"Expiry Type: {ref_set.get('expiry_type', 'N/A')}",
        f"Time to Live: {ref_set.get('time_to_live', 'N/A')}",
        f"Expired Log Option: {ref_set.get('expired_log_option', 'N/A')}",
        f"Delete Entries: {'Yes' if ref_set.get('delete_entries') else 'No'}",
        "-" * 60,
        f"Description: {ref_set.get('description', 'None')}",
        "=" * 60
    ]

    return "\n".join(lines)
