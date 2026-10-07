"""The gateway of a `replay` run: a recording instead of QRadar (T-052 criterion 5, decision T-70).

It is the fixture gateway (eval/fixture_gateway.py) with its answers taken from a recording. The
intent checks are the gateway's own; what follows them mirrors the gateway's pipeline
(services/mcp-gateway, pipeline.py) step by step, so a model sees what production would give it:

- `create_ariel_search`: the profile's AQL Guard (`check_aql`) and, for a profile with an output
  filter, the filtered-field check; a rejected query is `denied` with the gateway's reasons. An
  allowed query runs on the recorded event table (replay/aql.py). The answer is a search
  object, as the fork returns one, with a deterministic search ID. A query QRadar would
  refuse (an unknown column) is `upstream_error` with the text QRadar's 422 has; a query the
  engine does not run is `replay_unsupported`: an error to the model, a separate count in the run;
- `get_ariel_search_status` says `COMPLETED` at once; `get_ariel_search_results` pages the rows
  with the gateway's rules (`limit` lowered to the tool's `max_rows`, the profile's output
  filter, the byte cap, `truncated`); `delete_ariel_search` ends the search. A search ID of
  another run, or one never made, is `denied` as `search_not_owned` (`only_own_searches`); a
  deleted search is an `upstream_error`, as the fork gives for a search it no longer has;
- the evidence ID and the query hash are the gateway's: `build_evidence` and the Guard's hash.
  The window of a search made with numeric START/STOP is the query's own exact window (T-60); the
  status, results and delete calls carry the creating call's window and hash. The evidence of
  each call is kept in the exchange;
- the offense and its read tools: a recorded `get_offense`, `get_rule` or `get_log_source` is
  answered from the recording by its ID; `list_source_addresses`,
  `list_local_destination_addresses` and `get_log_source` the recording does not hold are
  derived (derived.py); every other call is `unscripted`.

`now` is the moment of the evaluation: `retrieved_at` of the evidence and the end of `LAST n`.
There is no quota, wait or latency: replay is for measuring the model.
"""

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from pydantic import JsonValue

from ais0c_contracts import (
    DataGap,
    DataGapReason,
    EvidenceRef,
    TimeWindow,
    ToolCoverage,
    ToolIntent,
    ToolResult,
    ToolStatus,
)
from ais0c_harness.eval.fixture_gateway import Answer, FixtureGateway, denied
from ais0c_harness.replay.aql import QueryError, Unsupported, run_query
from ais0c_harness.replay.derived import DerivedAnswers
from ais0c_harness.replay.recording import Recording
from ais0c_mcp_gateway.evidence import build_evidence, tool_query
from ais0c_mcp_gateway.pipeline import Denial
from ais0c_mcp_gateway.registry import Profile, SearchStep, Tool
from ais0c_policy import aql_filtered_field_references, check_aql, filter_rows

SEARCH_NAMESPACE: Final = uuid.UUID("5d0cb8a4-3f4e-4e0e-9a4c-7d4f2d0f6a11")
DEFAULT_RESULT_LIMIT: Final = 100
ID_ARGUMENTS: Final = {
    "get_offense": "offense_id",
    "get_rule": "rule_id",
    "get_log_source": "log_source_id",
}
"""The read tools a recording answers by the ID in their arguments."""
UNSUPPORTED: Final = "replay_unsupported"
UPSTREAM_ERROR: Final = "upstream_error"


@dataclass
class _Search:
    query: str
    rows: list[dict[str, JsonValue]]
    created: EvidenceRef
    deleted: bool = False


class ReplayGateway(FixtureGateway):
    """Answers one run's tool calls from a recording; one instance per run."""

    def __init__(
        self,
        profile: Profile,
        recording: Recording,
        *,
        now: datetime,
        run_id: str,
        derived: DerivedAnswers | None = None,
    ) -> None:
        super().__init__(
            profile,
            {},
            now=now,
            derived=DerivedAnswers(recording.offense, recording.enrichment)
            if derived is None
            else derived,
        )
        self._recording = recording
        self._run_id = run_id
        self._events = recording.event_rows
        self._searches: dict[str, _Search] = {}
        self._recorded = {
            _recorded_key(call.tool_id, call.arguments): call.result for call in recording.calls
        }

    @property
    def evidence(self) -> dict[str, EvidenceRef]:
        """The evidence the run's calls recorded, by evidence ID."""
        return {
            exchange.evidence.evidence_id: exchange.evidence
            for exchange in self.exchanges
            if exchange.evidence is not None
        }

    def _respond(self, intent: ToolIntent, tool: Tool) -> Answer:
        if tool.search is SearchStep.CREATE:
            return self._create(intent, tool)
        if tool.search is not None:
            return self._lifecycle(intent, tool)
        recorded = self._recorded.get(_recorded_key(tool.id, intent.arguments))
        if recorded is not None:
            return Answer(recorded, "recorded")
        return super()._respond(intent, tool)

    # --- the Ariel lifecycle ----------------------------------------------------------------

    def _create(self, intent: ToolIntent, tool: Tool) -> Answer:
        profile = self._profile
        query = intent.arguments.get("query_expression")
        if not isinstance(query, str) or profile.aql is None:
            return Answer(denied(Denial("aql_guard", "no query")), "denied")
        guard = check_aql(query, profile.aql, profile.connector.indexed_fields)
        if not guard.allowed:
            return Answer(denied(Denial("aql_guard", ", ".join(guard.reasons))), "denied")
        if profile.output_filter is not None:
            filtered = aql_filtered_field_references(query, profile.output_filter)
            if filtered:
                names = ", ".join(filtered)
                return Answer(
                    denied(Denial("aql_filtered_field", f"{profile.name} may not query {names}")),
                    "denied",
                )
        try:
            result = run_query(query, self._events, now=self._now)
        except Unsupported as error:
            return Answer(self._error(intent, UNSUPPORTED, str(error)), "replay_unsupported")
        except QueryError as error:
            detail = f"Error executing {tool.id}: {error}"
            return Answer(self._error(intent, UPSTREAM_ERROR, detail), "replayed")

        search_id = str(uuid.uuid5(SEARCH_NAMESPACE, f"{self._run_id}/{len(self._searches) + 1}"))
        rows = [dict(row) for row in result.rows]
        search_object = _search_object(search_id, query, rows, completed=False)
        window = intent.time_window
        if guard.window is not None:
            if guard.window.clause == "last":
                window = TimeWindow(start=self._now - guard.window.duration, end=self._now)
            elif (
                guard.window.start is not None
                and guard.window.stop is not None
                and guard.window.start.tzinfo is not None
                and guard.window.stop.tzinfo is not None
            ):
                window = TimeWindow(start=guard.window.start, end=guard.window.stop)
        evidence = build_evidence(
            source=profile.connector.evidence_source,
            tool_id=tool.id,
            arguments=intent.arguments,
            query_text=query,
            query_hash=guard.query_hash or tool_query(tool.id, intent.arguments)[1],
            window=window,
            rows=[search_object],
            retrieved_at=self._now,
            extra_identifiers={"search_id": search_id},
        )
        self._searches[search_id] = _Search(query=query, rows=rows, created=evidence)
        return Answer(_ok([search_object], evidence), "replayed", evidence)

    def _lifecycle(self, intent: ToolIntent, tool: Tool) -> Answer:
        search_id = intent.arguments.get("search_id")
        search = self._searches.get(search_id) if isinstance(search_id, str) else None
        if search is None or not isinstance(search_id, str):
            denial = Denial(
                "search_not_owned",
                "the search was not started through the gateway in this case or hunt "
                f"under {self._profile.name}",
            )
            return Answer(denied(denial), "denied")
        if search.deleted:
            detail = f"Error executing {tool.id}: the search does not exist"
            return Answer(self._error(intent, UPSTREAM_ERROR, detail), "replayed")
        if tool.search is SearchStep.RESULTS:
            rows, truncated = self._page(intent, tool, search)
        else:
            if tool.search is SearchStep.DELETE:
                search.deleted = True
            rows = [_search_object(search_id, search.query, search.rows, completed=True)]
            truncated = False
        created = search.created
        evidence = build_evidence(
            source=created.source,
            tool_id=tool.id,
            arguments=intent.arguments,
            query_text=created.query_text,
            query_hash=created.query_hash,
            window=TimeWindow(start=created.time_start, end=created.time_end),
            rows=rows,
            retrieved_at=self._now,
            extra_identifiers={"search_id": search_id},
        )
        return Answer(_ok(rows, evidence, truncated=truncated), "replayed", evidence)

    def _page(
        self, intent: ToolIntent, tool: Tool, search: _Search
    ) -> tuple[list[dict[str, JsonValue]], bool]:
        """One page of results, as the gateway clamps, filters and caps it."""
        requested = intent.arguments.get("limit", DEFAULT_RESULT_LIMIT)
        requested = requested if isinstance(requested, int) else DEFAULT_RESULT_LIMIT
        start = intent.arguments.get("start", 0)
        start = start if isinstance(start, int) else 0
        page_size = min(requested, tool.max_rows)
        clamped = requested > tool.max_rows
        page = search.rows[start : start + page_size]
        profile = self._profile
        rows = (
            list(page)
            if profile.output_filter is None
            else filter_rows(page, profile.output_filter)
        )
        data, cut = _cap(rows, tool.max_rows, profile.connector.limits.max_result_bytes)
        return data, cut or (clamped and len(rows) >= page_size)

    def _error(self, intent: ToolIntent, code: str, detail: str) -> ToolResult:
        window = intent.time_window
        gap = DataGap(
            source=self._profile.connector.id,
            period_start=window.start,
            period_end=window.end,
            reason=DataGapReason.QUERY_FAILED,
        )
        return ToolResult(
            status=ToolStatus.ERROR,
            deny_reason=Denial(code, detail).text(),
            data=[],
            truncated=False,
            coverage=ToolCoverage(complete=False, gaps=[gap]),
        )


def _recorded_key(tool_id: str, arguments: Mapping[str, JsonValue]) -> str:
    name = ID_ARGUMENTS.get(tool_id)
    if name is not None and name in arguments:
        return f"{tool_id}:{arguments[name]}"
    return f"{tool_id}:{json.dumps(arguments, sort_keys=True)}"


def _search_object(
    search_id: str, query: str, rows: Sequence[object], *, completed: bool
) -> dict[str, JsonValue]:
    """The search as the fork returns it (a subset of the Ariel API's fields)."""
    return {
        "search_id": search_id,
        "cursor_id": search_id,
        "status": "COMPLETED" if completed else "WAIT",
        "completed": completed,
        "progress": 100 if completed else 0,
        "record_count": len(rows) if completed else 0,
        "processed_record_count": len(rows) if completed else 0,
        "query_string": query,
        "save_results": False,
    }


def _ok(
    rows: Sequence[dict[str, JsonValue]], evidence: EvidenceRef, *, truncated: bool = False
) -> ToolResult:
    return ToolResult(
        status=ToolStatus.OK,
        evidence_id=evidence.evidence_id,
        data=list(rows),
        truncated=truncated,
        coverage=ToolCoverage(complete=not truncated, gaps=[]),
    )


def _cap(
    rows: Sequence[dict[str, JsonValue]], max_rows: int, max_bytes: int
) -> tuple[list[dict[str, JsonValue]], bool]:
    """At most `max_rows` rows whose JSON fits in `max_bytes`; True when rows were left out.
    The gateway's own cap (pipeline.py), which is private there."""
    kept: list[dict[str, JsonValue]] = []
    size = 2  # the brackets of the JSON array
    for row in rows[:max_rows]:
        encoded = len(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        if size + encoded + 1 > max_bytes:
            return kept, True
        kept.append(row)
        size += encoded + 1
    return kept, len(rows) > max_rows
