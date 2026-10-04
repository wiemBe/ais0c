"""The call pipeline (architecture §13.1, §13.3).

A call passes these steps in order; the first one that fails decides the answer:

1. the run: the intent's `run_id` names an agent run that is in progress and belongs to the
   caller's profile, agent and case or hunt;
2. the profile: the intent names the caller's profile, and the tool is in that profile; a
   profile that belongs to a platform component (`caller`, such as the Action Executor's
   qradar-note-write) serves only runs of that component;
3. the ToolIntent: no NUL character anywhere (the database cannot store one; the record holds
   U+FFFD instead), the semantic checks of packages/policy, the tool's schema version, its
   arguments against the registry's JSON Schema, and the profile's text rules (length, control
   characters; text_rules.py);
4. for a call that starts an Ariel search: the AQL Guard, and for a profile with an output
   filter, no reference to a filtered field;
5. for a call that reads or deletes an Ariel search: ownership;
6. the hunt pool's allowed hours for a new search, then room in the quota pool (may wait);
7. the call to the MCP server: one attempt with a deadline;
8. the result: rows taken from the structured result, the profile's output filter applied,
   rows and bytes capped;
9. the records: the call in tool_calls, a successful call's evidence in evidence; an empty read
   of the offense source has no evidence (D-33, evidence.py).

A denial (`denied`) or a failed call (`error`) is an answer, not an exception, and is recorded
like any other call. An exception means no answer can be given: the run is unknown, or the
database cannot be reached. In both cases the MCP server is never called (fail closed).
"""

import json
import logging
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as SchemaValidationError
from jsonschema.exceptions import best_match
from pydantic import JsonValue
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ais0c_contracts import (
    SHORT_TEXT_MAX_LENGTH,
    DataGap,
    DataGapReason,
    EvidenceRef,
    TimeWindow,
    ToolCoverage,
    ToolIntent,
    ToolResult,
    ToolStatus,
)
from ais0c_mcp_gateway.ariel import FINAL_STATUSES, SEARCH_ID, find_owned_search
from ais0c_mcp_gateway.evidence import build_evidence, is_evidence, record_call, tool_query
from ais0c_mcp_gateway.logs import Redactor
from ais0c_mcp_gateway.quotas import Admission, QuotaDenial, QuotaPool
from ais0c_mcp_gateway.registry import POOL_NAMES, PoolName, Profile, Registry, SearchStep, Tool
from ais0c_mcp_gateway.text_rules import text_problem
from ais0c_mcp_gateway.upstream import Upstream, UpstreamFailure, UpstreamOutcome, one_line
from ais0c_policy import (
    AqlGuardResult,
    aql_filtered_field_references,
    check_aql,
    check_intent,
    filter_rows,
)
from ais0c_storage import PolicyDecision
from ais0c_storage.models import AgentRunRow
from ais0c_storage.repositories import get_agent_run

logger = logging.getLogger("ais0c.gateway")

_STORAGE_ERRORS: Final = (OperationalError, InterfaceError, PoolTimeoutError, OSError)
_DETAIL_LENGTH: Final = 120
# The form of the run IDs the platform issues: workflow IDs (`case-12345-triage-1`) and UUIDs.
# An ID of another form names no run and is not looked up.
_RUN_ID: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}")
# JSON can carry U+0000 ("\u0000"); PostgreSQL text and jsonb cannot store it. An intent that
# holds one is denied, and its record holds U+FFFD instead. JSON cannot carry lone surrogates.
_NUL: Final = chr(0)
_REPLACEMENT: Final = chr(0xFFFD)


class UnknownRunError(LookupError):
    """The intent's `run_id` names no recorded agent run; the call cannot be recorded or made."""


class StorageUnavailableError(RuntimeError):
    """The database cannot be reached; no call is made (fail closed)."""


@dataclass(frozen=True)
class Denial:
    code: str
    detail: str = ""

    def text(self) -> str:
        text = f"{self.code}: {self.detail}" if self.detail else self.code
        return one_line(text, SHORT_TEXT_MAX_LENGTH)


@dataclass
class _Plan:
    """An authorized call, ready for the quota pool and the MCP server."""

    tool: Tool
    arguments: dict[str, JsonValue]
    pool: PoolName
    page_size: int | None = None
    page_clamped: bool = False
    aql: AqlGuardResult | None = None
    owned_search: EvidenceRef | None = None

    @property
    def starts_search(self) -> bool:
        return self.tool.search is SearchStep.CREATE


@dataclass(frozen=True)
class _Reply:
    result: ToolResult
    decision: PolicyDecision
    evidence: EvidenceRef | None = None


@dataclass
class Gateway:
    registry: Registry
    upstreams: Mapping[str, Upstream]
    """One per MCP instance name."""
    sessions: async_sessionmaker[AsyncSession]
    redactor: Redactor
    pools: Mapping[tuple[str, PoolName], QuotaPool] = field(default_factory=dict)
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    monotonic: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        if not self.pools:
            self.pools = build_pools(self.registry, monotonic=self.monotonic)
        missing = self.registry.instances() - self.upstreams.keys()
        if missing:
            raise ValueError(f"no MCP client for instances: {', '.join(sorted(missing))}")

    async def call(self, profile_name: str, intent: ToolIntent) -> ToolResult:
        """Check, run and record one call of the profile `profile_name` in the agent run that
        `intent.run_id` names."""
        started = self.monotonic()
        profile = self.registry.profiles[profile_name]
        if not _RUN_ID.fullmatch(intent.run_id):
            raise UnknownRunError(intent.run_id)
        try:
            async with self.sessions.begin() as session:
                run = await get_agent_run(session, intent.run_id)
                if run is None:
                    raise UnknownRunError(intent.run_id)
                checked = await self._authorize(session, profile, run, intent)
        except _STORAGE_ERRORS as error:
            raise StorageUnavailableError("the database cannot be reached") from error

        if isinstance(checked, Denial):
            reply = _denied(checked)
        else:
            reply = await self._run(profile, intent, checked)
        return await self._record(profile, intent, reply, started)

    # --- 1-5: checks -----------------------------------------------------------------------

    async def _authorize(
        self, session: AsyncSession, profile: Profile, run: AgentRunRow, intent: ToolIntent
    ) -> _Plan | Denial:
        if _holds_nul(intent.model_dump(mode="json")):
            return Denial("invalid_intent", "the intent holds a NUL character (U+0000)")
        if run.ended_at is not None or run.status is not None:
            return Denial("run_not_active", "the agent run has ended")
        if intent.toolset_profile != profile.name:
            return Denial(
                "profile_mismatch",
                f"the token is for {profile.name}, the intent names {_short(intent.toolset_profile)}",
            )
        if (run.toolset_profile, run.agent_id, run.case_id, run.hunt_id) != (
            profile.name,
            intent.agent_id,
            intent.case_id,
            intent.hunt_id,
        ):
            return Denial("run_mismatch", "the run belongs to another profile, agent, case or hunt")
        # The token is the boundary. This keeps a component's token useless in another run too:
        # a run is recorded under its agent ID by platform code, never by a model.
        if profile.caller is not None and run.agent_id != profile.caller:
            return Denial("caller_not_allowed", f"{profile.name} serves only {profile.caller}")
        tool = profile.tools.get(intent.tool_id)
        if tool is None:
            return Denial(
                "tool_not_in_profile", f"{_short(intent.tool_id)} is not a tool of {profile.name}"
            )

        reasons = check_intent(intent, profile.intent_rules, self.now())
        if reasons:
            return Denial("invalid_intent", ", ".join(reasons))
        if intent.tool_schema_version != tool.entry.schema_version:
            return Denial(
                "schema_version_mismatch",
                f"{tool.id} is at schema version {tool.entry.schema_version}",
            )
        problem = _argument_problem(tool, intent.arguments)
        if problem is not None:
            return Denial("invalid_arguments", problem)
        problem = _text_problem(profile, intent.arguments)
        if problem is not None:
            return Denial("invalid_text", problem)

        plan = _Plan(tool=tool, arguments=dict(intent.arguments), pool=_pool_of(intent))
        _clamp_page_size(plan)
        if tool.search is SearchStep.CREATE:
            return self._check_query(profile, plan)
        if tool.only_own_searches:
            return await self._check_ownership(session, profile, intent, plan)
        return plan

    def _check_query(self, profile: Profile, plan: _Plan) -> _Plan | Denial:
        query = plan.arguments.get("query_expression")
        if not isinstance(query, str) or profile.aql is None:
            return Denial("aql_guard", "no query")
        guard = check_aql(query, profile.aql, profile.connector.indexed_fields)
        if not guard.allowed:
            return Denial("aql_guard", ", ".join(guard.reasons))
        if profile.output_filter is not None:
            filtered = aql_filtered_field_references(query, profile.output_filter)
            if filtered:
                names = ", ".join(_short(name) for name in filtered)
                return Denial("aql_filtered_field", f"{profile.name} may not query {names}")
        plan.aql = guard
        return plan

    async def _check_ownership(
        self, session: AsyncSession, profile: Profile, intent: ToolIntent, plan: _Plan
    ) -> _Plan | Denial:
        search_id = plan.arguments.get(SEARCH_ID)
        create_tools = [t.id for t in profile.tools.values() if t.search is SearchStep.CREATE]
        owned = None
        if isinstance(search_id, str):
            owned = await find_owned_search(
                session,
                search_id=search_id,
                case_id=intent.case_id,
                hunt_id=intent.hunt_id,
                profile=profile.name,
                create_tools=create_tools,
            )
        if owned is None:
            return Denial(
                "search_not_owned",
                "the search was not started through the gateway in this case or hunt "
                f"under {profile.name}",
            )
        plan.owned_search = owned
        return plan

    # --- 6-8: quota, call, result ----------------------------------------------------------

    async def _run(self, profile: Profile, intent: ToolIntent, plan: _Plan) -> _Reply:
        pool = self.pools[(profile.connector.id, plan.pool)]
        if plan.starts_search and not pool.allows_new_search(self.now()):
            return _denied(
                Denial("outside_allowed_hours", f"the {pool.name} pool starts no search now")
            )
        admission = await pool.admit(starts_search=plan.starts_search)
        if isinstance(admission, QuotaDenial):
            return _denied(
                Denial("quota_exhausted", f"{admission.pool} pool, {admission.limit}; try later")
            )
        try:
            outcome = await self.upstreams[profile.instance].call_tool(plan.tool.id, plan.arguments)
            return await self._reply(profile, intent, plan, outcome, admission)
        finally:
            await pool.release(admission)

    async def _reply(
        self,
        profile: Profile,
        intent: ToolIntent,
        plan: _Plan,
        outcome: UpstreamOutcome,
        admission: Admission,
    ) -> _Reply:
        structured = outcome.structured
        if structured is None:
            failure = outcome.failure or UpstreamFailure.ERROR
            return self._error(profile, intent, failure, outcome.detail)

        search_id = _search_id(plan, structured)
        if plan.tool.search is not None and search_id is None:
            return self._error(profile, intent, UpstreamFailure.INVALID_RESULT, "no search_id")
        if search_id is not None:
            await self._track_search(plan, structured, search_id, admission)

        rows = _rows(plan.tool, structured)
        if rows is None:
            return self._error(profile, intent, UpstreamFailure.INVALID_RESULT, "no result rows")
        if profile.output_filter is not None:
            rows = filter_rows(rows, profile.output_filter)
        limits = profile.connector.limits
        data, cut = _cap(rows, plan.tool.max_rows, limits.max_result_bytes)
        truncated = cut or (plan.page_clamped and len(rows) >= (plan.page_size or 0))

        retrieved_at = self.now()
        evidence = (
            self._evidence(profile, intent, plan, search_id, data, retrieved_at)
            if is_evidence(intent.agent_id, rows)
            else None
        )
        result = ToolResult(
            status=ToolStatus.OK,
            evidence_id=None if evidence is None else evidence.evidence_id,
            data=data,
            truncated=truncated,
            coverage=ToolCoverage(complete=not truncated, gaps=[]),
        )
        return _Reply(result=result, decision=PolicyDecision.ALLOW, evidence=evidence)

    async def _track_search(
        self,
        plan: _Plan,
        structured: Mapping[str, JsonValue],
        search_id: str,
        admission: Admission,
    ) -> None:
        pool = admission.pool
        step = plan.tool.search
        if step is SearchStep.CREATE:
            await pool.bind_search(admission, search_id)
        if step is SearchStep.DELETE or (
            step in (SearchStep.CREATE, SearchStep.STATUS)
            and structured.get("status") in FINAL_STATUSES
        ):
            await pool.finish_search(search_id)

    def _evidence(
        self,
        profile: Profile,
        intent: ToolIntent,
        plan: _Plan,
        search_id: str | None,
        rows: Sequence[Mapping[str, JsonValue]],
        retrieved_at: datetime,
    ) -> EvidenceRef:
        window = intent.time_window
        if plan.aql is not None and plan.aql.query_hash is not None:
            query_text = str(plan.arguments["query_expression"])
            query_hash = plan.aql.query_hash
            if plan.aql.window is not None and plan.aql.window.clause == "last":
                window = TimeWindow(start=retrieved_at - plan.aql.window.duration, end=retrieved_at)
        elif plan.owned_search is not None:
            owned = plan.owned_search
            query_text, query_hash = owned.query_text, owned.query_hash
            window = TimeWindow(start=owned.time_start, end=owned.time_end)
        else:
            query_text, query_hash = tool_query(plan.tool.id, plan.arguments)
        return build_evidence(
            source=profile.connector.evidence_source,
            tool_id=plan.tool.id,
            arguments=plan.arguments,
            query_text=query_text,
            query_hash=query_hash,
            window=window,
            rows=rows,
            retrieved_at=retrieved_at,
            extra_identifiers=None if search_id is None else {SEARCH_ID: search_id},
        )

    def _error(
        self, profile: Profile, intent: ToolIntent, failure: UpstreamFailure, detail: str
    ) -> _Reply:
        text = Denial(failure.value, self.redactor.redact(detail)).text()
        window = intent.time_window
        gap = DataGap(
            source=profile.connector.id,
            period_start=window.start,
            period_end=window.end,
            reason=DataGapReason.QUERY_FAILED,
        )
        result = ToolResult(
            status=ToolStatus.ERROR,
            deny_reason=text,
            data=[],
            truncated=False,
            coverage=ToolCoverage(complete=False, gaps=[gap]),
        )
        return _Reply(result=result, decision=PolicyDecision.ALLOW)

    # --- 9: records ------------------------------------------------------------------------

    async def _record(
        self, profile: Profile, intent: ToolIntent, reply: _Reply, started: float
    ) -> ToolResult:
        latency_ms = max(0, round((self.monotonic() - started) * 1000))
        result = reply.result
        try:
            async with self.sessions.begin() as session:
                await record_call(
                    session,
                    intent=_storable(intent),
                    decision=reply.decision,
                    status=result.status,
                    latency_ms=latency_ms,
                    deny_reason=result.deny_reason,
                    evidence=reply.evidence,
                )
        except _STORAGE_ERRORS as error:
            raise StorageUnavailableError("the database cannot be reached") from error
        logger.info(
            "tool call profile=%s agent=%r tool=%r run=%r case=%r hunt=%r decision=%s "
            "status=%s reason=%s evidence=%s rows=%d latency_ms=%d",
            profile.name,
            _short(intent.agent_id),
            _short(intent.tool_id),
            _short(intent.run_id),
            _short(intent.case_id or ""),
            _short(intent.hunt_id or ""),
            reply.decision.value,
            result.status.value,
            (result.deny_reason or "").split(":", 1)[0] or "-",
            result.evidence_id or "-",
            len(result.data),
            latency_ms,
        )
        return result


def build_pools(
    registry: Registry, *, monotonic: Callable[[], float] = time.monotonic
) -> dict[tuple[str, PoolName], QuotaPool]:
    return {
        (connector.id, name): QuotaPool(name, connector.quota_pools[name], monotonic=monotonic)
        for connector in registry.connectors.values()
        for name in POOL_NAMES
    }


def _holds_nul(value: JsonValue) -> bool:
    if isinstance(value, str):
        return _NUL in value
    if isinstance(value, dict):
        return any(_NUL in key or _holds_nul(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_holds_nul(item) for item in value)
    return False


def _without_nul(value: JsonValue) -> JsonValue:
    if isinstance(value, str):
        return value.replace(_NUL, _REPLACEMENT)
    if isinstance(value, dict):
        return {key.replace(_NUL, _REPLACEMENT): _without_nul(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_without_nul(item) for item in value]
    return value


def _storable(intent: ToolIntent) -> ToolIntent:
    """The intent as tool_calls can hold it: a NUL character becomes U+FFFD."""
    data = intent.model_dump(mode="json")
    if not _holds_nul(data):
        return intent
    return ToolIntent.model_validate(_without_nul(data))


def _denied(denial: Denial) -> _Reply:
    result = ToolResult(
        status=ToolStatus.DENIED,
        deny_reason=denial.text(),
        data=[],
        truncated=False,
        coverage=ToolCoverage(complete=False, gaps=[]),
    )
    return _Reply(result=result, decision=PolicyDecision.DENY)


def _pool_of(intent: ToolIntent) -> PoolName:
    return "case" if intent.case_id else "hunt"


def _argument_problem(tool: Tool, arguments: Mapping[str, JsonValue]) -> str | None:
    """What is wrong with the arguments, without echoing their values."""
    error = best_match(Draft202012Validator(tool.entry.input_schema).iter_errors(arguments))
    if error is None:
        return None
    return _describe(error, tool)


def _text_problem(profile: Profile, arguments: Mapping[str, JsonValue]) -> str | None:
    """What breaks one of the profile's text rules, without echoing the text."""
    for name, rule in profile.text_rules.items():
        value = arguments.get(name)
        if isinstance(value, str) and (problem := text_problem(name, value, rule)) is not None:
            return problem
    return None


def _describe(error: SchemaValidationError, tool: Tool) -> str:
    properties = tool.entry.input_schema.get("properties")
    names = sorted(properties) if isinstance(properties, dict) else []
    if error.validator == "required" and isinstance(error.instance, dict):
        required = error.validator_value if isinstance(error.validator_value, list) else []
        missing = [str(name) for name in required if name not in error.instance]
        return f"missing {', '.join(missing)}"
    if error.validator == "additionalProperties":
        return f"unknown argument; {tool.id} takes {', '.join(names)}"
    where = "/".join(str(part) for part in error.absolute_path) or "arguments"
    if where not in names and "/" not in where and where != "arguments":
        where = "an argument"
    return f"{_short(where)} does not match the schema ({error.validator})"


def _clamp_page_size(plan: _Plan) -> None:
    """Lower the page size argument to the tool's max_rows; fetch no more than is returned."""
    name = plan.tool.entry.page_size_argument
    if name is None:
        return
    properties = plan.tool.entry.input_schema.get("properties")
    spec = properties.get(name) if isinstance(properties, dict) else None
    default = spec.get("default") if isinstance(spec, dict) else None
    requested = plan.arguments.get(name, default)
    if isinstance(requested, bool) or not isinstance(requested, int):
        return
    plan.page_size = min(requested, plan.tool.max_rows)
    if requested > plan.tool.max_rows:
        plan.arguments[name] = plan.tool.max_rows
        plan.page_clamped = True


def _search_id(plan: _Plan, structured: Mapping[str, JsonValue]) -> str | None:
    """The Ariel search a lifecycle call is about; None for other tools."""
    if plan.tool.search is None:
        return None
    value = structured.get(SEARCH_ID) if plan.starts_search else plan.arguments.get(SEARCH_ID)
    if isinstance(value, str) and value and len(value) <= 128 and value.isprintable():
        return value
    return None


def _rows(tool: Tool, structured: dict[str, JsonValue]) -> list[dict[str, JsonValue]] | None:
    key = tool.entry.rows_key
    if key is None:
        return [structured]
    value = structured.get(key)
    if not isinstance(value, list):
        return None
    return [item if isinstance(item, dict) else {"value": item} for item in value]


def _cap(
    rows: Sequence[dict[str, JsonValue]], max_rows: int, max_bytes: int
) -> tuple[list[dict[str, JsonValue]], bool]:
    """At most `max_rows` rows whose JSON fits in `max_bytes`; True when rows were left out."""
    kept: list[dict[str, JsonValue]] = []
    size = 2  # the brackets of the JSON array
    for row in rows[:max_rows]:
        encoded = len(json.dumps(row, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        if size + encoded + 1 > max_bytes:
            return kept, True
        kept.append(row)
        size += encoded + 1
    return kept, len(rows) > max_rows


def _short(text: str) -> str:
    return one_line(text, _DETAIL_LENGTH)
