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
Error Handler Utilities

Provides utilities for extracting and formatting error messages from QRadar API responses.
"""

import httpx


def extract_qradar_error(error: httpx.HTTPStatusError, context: str = "API request") -> str:
    """
    Extract a detailed error message from a QRadar HTTPStatusError response.

    QRadar API error responses typically contain a 'message' or 'description' field
    with details about what went wrong. This function attempts to extract that
    information to provide more helpful error messages.

    Args:
        error: The HTTPStatusError exception from httpx library
        context: A description of what operation failed (e.g., "creating Ariel search")

    Returns:
        A formatted error message string

    Example:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            error_msg = extract_qradar_error(e, "creating Ariel search")
            return self.create_error_response(error_msg)
    """
    base_error = f"Error {context}: {str(error)}"

    # Try to extract QRadar's detailed error message from response
    try:
        if error.response is not None:
            error_data = error.response.json()

            # QRadar typically uses 'message' or 'description' for error details
            if isinstance(error_data, dict):
                return _parse_error_from_dict(error_data, context)
    except (ValueError, KeyError, AttributeError):
        # If we can't parse the error response, fall back to the base error
        pass

    return base_error


def _parse_error_from_dict(error_data: dict, context: str) -> str:
    if 'message' in error_data:
        return f"Error {context}: {error_data['message']}"
    if 'description' in error_data:
        return f"Error {context}: {error_data['description']}"
    if 'error' in error_data:
        # Some endpoints nest the error in an 'error' field
        if isinstance(error_data['error'], str):
            return f"Error {context}: {error_data['error']}"
        if isinstance(error_data['error'], dict) and 'message' in error_data['error']:
            return f"Error {context}: {error_data['error']['message']}"

    raise ValueError(f"Unable to parse error response: {error_data}")
