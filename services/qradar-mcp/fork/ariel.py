# SPDX-License-Identifier: Apache-2.0
"""Ariel search lifecycle with ownership (architecture §11.1).

create -> status -> paged results (``Range`` header) -> delete. The server remembers the
search IDs it created and deletes only those; an Ariel search started by an analyst or by
another tool is never touched. The paging part needs no code here: the results tool always
receives ``start`` and ``limit`` (see ``tool_specs``), so upstream always sends ``Range``.

The registry lives in process memory. After a restart the server owns no searches; those
it created before expire in QRadar on their own. Run one worker process per instance.
"""

from __future__ import annotations

import json
import logging
from collections import OrderedDict
from typing import Any

from qradar_mcp.tools.ariel.create_ariel_search import CreateArielSearchTool
from qradar_mcp.tools.ariel.delete_ariel_search import DeleteArielSearchTool
from qradar_mcp.tools.schema import schema

logger = logging.getLogger(__name__)

MAX_QUERY_LENGTH = 50_000
DEFAULT_OWNERSHIP_CAPACITY = 10_000


class SearchOwnership:
    """Search IDs this process created, oldest first, capped at ``capacity``."""

    def __init__(self, capacity: int = DEFAULT_OWNERSHIP_CAPACITY) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self._capacity = capacity
        self._ids: OrderedDict[str, None] = OrderedDict()

    def remember(self, search_id: str) -> None:
        self._ids[search_id] = None
        self._ids.move_to_end(search_id)
        while len(self._ids) > self._capacity:
            evicted, _ = self._ids.popitem(last=False)
            logger.warning(
                "Ariel ownership registry is full; search %s can no longer be deleted by "
                "this server and will expire in QRadar",
                evicted,
            )

    def owns(self, search_id: str) -> bool:
        return search_id in self._ids

    def forget(self, search_id: str) -> None:
        self._ids.pop(search_id, None)

    def __len__(self) -> int:
        return len(self._ids)


class OwnedCreateArielSearchTool(CreateArielSearchTool):
    """Starts an AQL search and records its ID as owned.

    Upstream also accepts ``saved_search_id``. A saved search runs a query the gateway's
    AQL Guard cannot inspect, so only ``query_expression`` is accepted here.
    """

    def __init__(self, ownership: SearchOwnership) -> None:
        super().__init__()
        self._ownership = ownership

    @property
    def tool_group(self) -> str:
        return "ariel"

    @property
    def description(self) -> str:
        return (
            "Start an asynchronous Ariel search for an AQL query. Returns the search object; "
            "pass its search_id to get_ariel_search_status, get_ariel_search_results and "
            "delete_ariel_search."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return (
            schema()
            .string("query_expression")
            .description("The AQL query to run, with a time range and a LIMIT.")
            .min_length(1)
            .max_length(MAX_QUERY_LENGTH)
            .required()
            .build()
        )

    async def _execute_impl(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = arguments.get("query_expression")
        if not isinstance(query, str) or not query.strip():
            return self.create_error_response("Error: query_expression is required")
        response = await self.client.post(self.endpoint, params={"query_expression": query})
        response.raise_for_status()
        search = response.json()
        search_id = search.get("search_id") if isinstance(search, dict) else None
        if not isinstance(search_id, str) or not search_id:
            raise RuntimeError("QRadar did not return a search_id for the new search")
        self._ownership.remember(search_id)
        return self.create_success_response(json.dumps(search))


class OwnedDeleteArielSearchTool(DeleteArielSearchTool):
    """Deletes an Ariel search, but only one this process created."""

    def __init__(self, ownership: SearchOwnership) -> None:
        super().__init__()
        self._ownership = ownership

    @property
    def tool_group(self) -> str:
        return "ariel"

    @property
    def description(self) -> str:
        return (
            "Delete an Ariel search created by this server and discard its results. "
            "Searches created by anyone else are refused."
        )

    async def _execute_impl(self, arguments: dict[str, Any]) -> dict[str, Any]:
        search_id = arguments.get("search_id")
        if not isinstance(search_id, str) or not search_id:
            return self.create_error_response("Error: search_id is required")
        if not self._ownership.owns(search_id):
            logger.warning(
                "Refused to delete Ariel search %s: not created by this server", search_id
            )
            return self.create_error_response(
                f"Refusing to delete Ariel search {search_id}: it was not created by this server"
            )
        result = await super()._execute_impl(arguments)
        if not result.get("isError"):
            self._ownership.forget(search_id)
        return result
