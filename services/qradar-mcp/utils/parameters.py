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
API Parameter Builders

Utilities for building QRadar API parameters in the correct format.
"""

from typing import Dict, List, Optional, Any, Tuple


def build_filter_param(filter_expr: Optional[str]) -> Dict[str, str]:
    """
    Build filter query parameter.

    Args:
        filter_expr: AQL-style filter expression
                    Example: "status='OPEN' and severity > 5"

    Returns:
        Dictionary with filter parameter if provided, empty dict otherwise
    """
    if filter_expr:
        return {"filter": filter_expr}
    return {}


def build_sort_param(sort_fields: Optional[List[str]]) -> Dict[str, str]:
    """
    Build sort query parameter.

    Args:
        sort_fields: List of field names with optional +/- prefix
                    Example: ["+severity", "-start_time"]
                    + for ascending (default), - for descending

    Returns:
        Dictionary with sort parameter if provided, empty dict otherwise
    """
    if sort_fields:
        return {"sort": ",".join(sort_fields)}
    return {}


def build_range_header(start: Optional[int] = None,
                       end: Optional[int] = None) -> Dict[str, str]:
    """
    Build Range header for pagination.

    Args:
        start: Starting index (0-based)
        end: Ending index (inclusive)

    Returns:
        Dictionary with Range header if parameters provided, empty dict otherwise

    Examples:
        build_range_header(0, 49) -> {"Range": "items=0-49"}
        build_range_header(50, 99) -> {"Range": "items=50-99"}
    """
    if start is not None and end is not None:
        return {"Range": f"items={start}-{end}"}
    return {}


def build_fields_param(fields: Optional[List[str]]) -> Dict[str, str]:
    """
    Build fields query parameter for field selection.

    Args:
        fields: List of field names to include in response
                Example: ["id", "description", "severity"]
                Supports nested fields: ["id", "rules(id,type)"]

    Returns:
        Dictionary with fields parameter if provided, empty dict otherwise
    """
    if fields:
        return {"fields": ",".join(fields)}
    return {}


def build_query_params(filter_expr: Optional[str] = None,
                      sort_fields: Optional[List[str]] = None,
                      fields: Optional[List[str]] = None,
                      **kwargs: Any) -> Dict[str, str]:
    """
    Build complete query parameters dictionary.

    Args:
        filter_expr: AQL-style filter expression
        sort_fields: List of sort fields with +/- prefix
        fields: List of fields to include
        **kwargs: Additional query parameters

    Returns:
        Dictionary of all query parameters
    """
    params = {}
    params.update(build_filter_param(filter_expr))
    params.update(build_sort_param(sort_fields))
    params.update(build_fields_param(fields))
    params.update(kwargs)
    return params


def build_headers(start: Optional[int] = None,
                 end: Optional[int] = None,
                 **kwargs: Any) -> Dict[str, str]:
    """
    Build complete headers dictionary.

    Args:
        start: Starting index for Range header
        end: Ending index for Range header
        **kwargs: Additional headers (values will be converted to strings)

    Returns:
        Dictionary of all headers
    """
    headers = {}
    headers.update(build_range_header(start, end))
    # Convert all header values to strings as required by requests library
    for key, value in kwargs.items():
        headers[key] = str(value) if value is not None else value
    return headers


def parse_range_from_limit_offset(limit: Optional[int] = None,
                                  offset: Optional[int] = None) -> Tuple[Optional[int], Optional[int]]:
    """
    Convert limit/offset pagination to start/end range.

    Args:
        limit: Maximum number of items to return
        offset: Number of items to skip

    Returns:
        Tuple of (start, end) for Range header, or (None, None) if not provided

    Examples:
        parse_range_from_limit_offset(50, 0) -> (0, 49)
        parse_range_from_limit_offset(50, 50) -> (50, 99)
        parse_range_from_limit_offset(25, 100) -> (100, 124)
    """
    if limit is not None:
        start = offset if offset is not None else 0
        end = start + limit - 1
        return (start, end)
    return (None, None)


def validate_sort_fields(sort_fields: List[str],
                        allowed_fields: List[str]) -> bool:
    """
    Validate that sort fields are in the allowed list.

    Args:
        sort_fields: List of sort fields (may include +/- prefix)
        allowed_fields: List of allowed field names

    Returns:
        True if all fields are valid, False otherwise
    """
    for field in sort_fields:
        # Remove +/- prefix if present
        clean_field = field.lstrip('+-')
        if clean_field not in allowed_fields:
            return False
    return True


def build_aql_filter(conditions: Dict[str, Any]) -> str:
    """
    Build AQL filter expression from dictionary of conditions.

    Args:
        conditions: Dictionary of field: value pairs
                   Supports operators: eq, gt, lt, gte, lte, in, like

    Returns:
        AQL filter expression string

    Examples:
        build_aql_filter({"status": "OPEN", "severity": {"gt": 5}})
        -> "status='OPEN' and severity > 5"

        build_aql_filter({"id": {"in": [1, 2, 3]}})
        -> "id in (1,2,3)"
    """
    filters = []

    for field, value in conditions.items():
        if isinstance(value, dict):
            # Handle operator-based conditions
            filters.extend(_build_operator_filter(field, value))
        else:
            # Simple equality
            filters.append(_build_equality_filter(field, value))

    return " and ".join(filters)



def _build_operator_filter(field: str, operators: Dict[str, Any]) -> List[str]:
    """Helper to build operator-based filters."""
    filters = []
    for op, val in operators.items():
        if op == "eq":
            filters.append(f"{field}='{val}'" if isinstance(val, str) else f"{field}={val}")
        elif op == "gt":
            filters.append(f"{field} > {val}")
        elif op == "lt":
            filters.append(f"{field} < {val}")
        elif op == "gte":
            filters.append(f"{field} >= {val}")
        elif op == "lte":
            filters.append(f"{field} <= {val}")
        elif op == "in":
            if isinstance(val, (list, tuple)):
                val_str = ",".join(str(v) for v in val)
                filters.append(f"{field} in ({val_str})")
        elif op == "like":
            filters.append(f"{field} like '{val}'")
    return filters


def _build_equality_filter(field: str, value: Any) -> str:
    """Helper to build simple equality filter."""
    if isinstance(value, str):
        return f"{field}='{value}'"
    return f"{field}={value}"
