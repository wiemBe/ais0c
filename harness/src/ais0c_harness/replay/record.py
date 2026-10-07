"""`python -m ais0c_harness.eval record`: record a closed lab offense (T-052 criteria 2, 3).

The recorder reads through the dev stack's MCP Policy Gateway, as the sham agent
`harness-recorder`, with the profiles `qradar-triage-read` (the offense, its rules and log
sources) and `qradar-investigate-read` (the events). Nothing is written to the lab: no offense is
opened, closed or noted, and every Ariel search it starts it deletes.

What it records (recording.py): the offense as the case workflow's offense source reads it, the
enrichment against the dev database's catalog, the read tools' results, the event table of the
window from an hour before the offense's start to an hour after its last update, and the audit
queries with the lab's answers. The table leaves out the log source types in `excluded` (by
default QRadar's own "Health Metrics", 98% of the lab's events and of no use to an analysis).
Everything is anonymized (anonymize.py) before it is written.

The lab offense may already be closed when it is recorded. A recording is an analysis input, so
`build_recording` normalizes the recorded `get_offense` row to the view the agent would have seen
while it was open: `status=OPEN`, `inactive=false`, and the three closing fields null. The compact
`OffenseSnapshot` contract has no closing fields; the manifest marks both representations as the
open view.

`LabReader` is what the recorder needs of the lab; `GatewayReader` is the live one. `build_recording`
takes what a reader returned and writes the recording, so the rest is tested without a lab.
"""

# ruff: noqa: S608 - the AQL below is built from fixed templates and the offense, not SQL

import asyncio
import hashlib
import importlib.metadata
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final, Protocol

import yaml
from pydantic import JsonValue

from ais0c_activities import GatewayOffenseSource, NoIocMatcher, build_enrichment
from ais0c_activities.db import SessionFactory
from ais0c_activities.gateway import SystemRun, system_run
from ais0c_activities.runtime import read_token
from ais0c_agents import GatewayClient, ToolsetProfile
from ais0c_agents.gateway_http import HttpGatewayClient
from ais0c_contracts import (
    Budget,
    EnrichmentContext,
    OffenseSnapshot,
    TimeWindow,
    ToolResult,
    ToolStatus,
)
from ais0c_harness.replay.anonymize import Anonymizer, foreign_addresses, strings
from ais0c_harness.replay.aql import Column, QueryError, Unsupported, parse, run_query
from ais0c_harness.replay.recording import (
    EVENT_COLUMNS,
    AuditQuery,
    RecordedCall,
    RecordedEvent,
    RecordingError,
    RecordingManifest,
    load_recording,
    write_recording,
)

AGENT_ID: Final = "harness-recorder"
TRIAGE_PROFILE: Final = "qradar-triage-read"
INVESTIGATE_PROFILE: Final = "qradar-investigate-read"
DEFAULT_EXCLUDED: Final = ("Health Metrics",)
WINDOW_MARGIN: Final = timedelta(hours=1)
SEARCH_LIMIT: Final = 1000
"""The most rows one search returns (the investigate profile's `max_limit`); a search that
returns that many is split in time and read again."""
RESULT_PAGE: Final = 500
QUOTA_WAIT_SECONDS: Final = 10.0
QUOTA_RETRIES: Final = 30
"""The gateway's case pool allows 120 calls a minute and denies a call that would wait longer than
its `max_wait_seconds`; a recording makes a few hundred calls, so the recorder waits and asks again."""
STATUS_WAIT_SECONDS: Final = 20
STATUS_POLLS: Final = 30
MIN_SLICE_MS: Final = 1000
SCAN_PAD_MS: Final = 600_000
"""START and STOP of a search are padded by this much on both sides. QRadar bounds a search by
the time it received the events, not by their `starttime` (measured on the lab: a search of a
two-second window returned events whose starttime lay 42 seconds before it), so the exact
bound of a recording is the `starttime BETWEEN` condition. The replay engine bounds a query's
START and STOP by `starttime` too, which is exact for every event of the DCSync offense (they share
the offense's start) and an approximation at the window's edge for events received late."""
SYSTEM_BUDGET: Final = Budget(tokens=0, tool_calls=100_000, seconds=3600)
EVENT_SELECT: Final = (
    "SELECT starttime, endtime, qid, QIDNAME(qid) AS qidname, category, "
    "CATEGORYNAME(category) AS categoryname, logsourceid, LOGSOURCENAME(logsourceid) AS "
    "logsourcename, LOGSOURCETYPENAME(devicetype) AS logsourcetypename, devicetype, sourceip, "
    "destinationip, sourceport, destinationport, username, eventcount, magnitude, "
    "UTF8(payload) AS payload FROM events"
)
CONNECTOR_FILE: Final = "config/connectors/qradar.yaml"
POLICY_FILE: Final = "config/policies/qradar.yaml"


class RecordError(RuntimeError):
    """The recording cannot be made; the message says why and no file is written."""


class LabReader(Protocol):
    """What the recorder reads from the lab, through the gateway."""

    async def offense(self, offense_id: int) -> OffenseSnapshot: ...

    async def enrichment(self, offense: OffenseSnapshot) -> EnrichmentContext: ...

    async def read_tool(
        self, offense: OffenseSnapshot, tool_id: str, arguments: dict[str, JsonValue]
    ) -> ToolResult: ...

    async def ariel(
        self, offense: OffenseSnapshot, window: TimeWindow, query: str
    ) -> list[dict[str, JsonValue]]:
        """The rows of an AQL query (with its LIMIT and START/STOP), all pages."""
        ...


@dataclass(frozen=True)
class RawRecording:
    """What the lab gave, before anonymization."""

    offense: OffenseSnapshot
    enrichment: EnrichmentContext
    calls: list[RecordedCall]
    events: list[dict[str, JsonValue]]
    audits: list[AuditQuery]
    window: TimeWindow


def event_window(offense: OffenseSnapshot) -> TimeWindow:
    return TimeWindow(
        start=offense.start_time - WINDOW_MARGIN, end=offense.last_updated_time + WINDOW_MARGIN
    )


def millis(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def exclusion(excluded: Sequence[str]) -> str:
    """The AQL condition that leaves out the excluded log source types; empty for none."""
    if not excluded:
        return ""
    names = ", ".join("'" + name.replace("'", "''") + "'" for name in excluded)
    return f"LOGSOURCETYPENAME(devicetype) NOT IN ({names})"


def scan(low: int, high: int) -> str:
    """The time clause of a search for the events with `starttime` in [low, high]."""
    return f"START {low - SCAN_PAD_MS} STOP {high + SCAN_PAD_MS}"


def event_query(low: int, high: int, excluded: Sequence[str]) -> str:
    where = " AND ".join(
        part for part in (exclusion(excluded), f"starttime BETWEEN {low} AND {high}") if part
    )
    return (
        f"{EVENT_SELECT} WHERE {where} ORDER BY starttime ASC "
        f"LIMIT {SEARCH_LIMIT} {scan(low, high)}"
    )


async def read_events(
    reader: LabReader, offense: OffenseSnapshot, window: TimeWindow, excluded: Sequence[str]
) -> list[dict[str, JsonValue]]:
    """The events of `window`, oldest first. A slice that fills the search's limit is split
    in two and read again, so no row is lost to the limit."""
    rows: list[dict[str, JsonValue]] = []

    async def read(low: int, high: int) -> None:
        part = TimeWindow(
            start=datetime.fromtimestamp(low / 1000, UTC),
            end=datetime.fromtimestamp(high / 1000, UTC),
        )
        found = await reader.ariel(offense, part, event_query(low, high, excluded))
        if len(found) < SEARCH_LIMIT or high - low < MIN_SLICE_MS:
            if len(found) >= SEARCH_LIMIT:
                raise RecordError(
                    f"more than {SEARCH_LIMIT} events within one second at {low}; "
                    "the recording would lose rows"
                )
            rows.extend(found)
            return
        middle = (low + high) // 2
        await read(low, middle)
        await read(middle + 1, high)

    await read(millis(window.start), millis(window.end))
    return rows


def audit_queries(
    offense: OffenseSnapshot, window: TimeWindow, excluded: Sequence[str]
) -> list[tuple[str, str]]:
    """(name, query) of the audit queries. They cover the operators and functions the agents
    use. All but `agent_form` bound the events by `starttime BETWEEN` and let START and STOP only
    bound the scan (see SCAN_PAD_MS); `agent_form` is a query as the prompts teach it, bounded by
    START and STOP alone from the offense's start. AQL orders by one column, and many events
    share a millisecond, so the comparison (`same_answer`) takes rows of an equal sort key in
    any order, and no query cuts a group of equal keys with its LIMIT."""
    low, high = millis(window.start), millis(window.end)
    near = (millis(offense.start_time) - 1000, millis(offense.last_updated_time) + 1000)
    scope = exclusion(excluded)
    base = f"{scope} AND " if scope else ""
    user = offense.offense_source.replace("'", "''")

    def within(bounds: tuple[int, int]) -> str:
        return f"{base}starttime BETWEEN {bounds[0]} AND {bounds[1]}"

    def clause(bounds: tuple[int, int]) -> str:
        return scan(*bounds)

    wide, narrow = (low, high), near
    exact = (millis(offense.start_time), millis(offense.last_updated_time) + 60_000)
    order = "ORDER BY starttime ASC"
    return [
        (
            "count_all",
            f"SELECT COUNT(*) AS n FROM events WHERE {within(wide)} LIMIT 10 {clause(wide)}",
        ),
        (
            "qidname_filter",
            "SELECT starttime, sourceip, username, QIDNAME(qid) AS q FROM events "
            f"WHERE {within(narrow)} AND QIDNAME(qid) ILIKE '%object%' {order} LIMIT 50 "
            f"{clause(narrow)}",
        ),
        (
            "group_count",
            "SELECT logsourceid, COUNT(*) AS n FROM events "
            f"WHERE {within(wide)} GROUP BY logsourceid ORDER BY logsourceid ASC LIMIT 20 "
            f"{clause(wide)}",
        ),
        (
            "ilike_payload",
            "SELECT starttime, sourceip, username FROM events "
            f"WHERE {within(narrow)} AND UTF8(payload) ILIKE '%{user}%' {order} LIMIT 50 "
            f"{clause(narrow)}",
        ),
        (
            "epoch_window",
            "SELECT starttime, sourceip, username, qid FROM events "
            f"WHERE {within(narrow)} {order} LIMIT 50 {clause(narrow)}",
        ),
        (
            "dateformat_user",
            "SELECT DATEFORMAT(starttime, 'yyyy-MM-dd HH:mm:ss') AS t, username, sourceip "
            f"FROM events WHERE {within(narrow)} AND username = '{user}' {order} LIMIT 20 "
            f"{clause(narrow)}",
        ),
        (
            "aggregates",
            "SELECT SUM(eventcount) AS total, MIN(starttime) AS earliest, MAX(starttime) AS "
            f"latest, COUNT(username) AS named FROM events WHERE {within(narrow)} LIMIT 10 "
            f"{clause(narrow)}",
        ),
        (
            "agent_form",
            "SELECT starttime, username, sourceip, QIDNAME(qid) AS event_name FROM events "
            f"WHERE username = '{user}' ORDER BY starttime ASC LIMIT 100 "
            f"START {exact[0]} STOP {exact[1]}",
        ),
        (
            "in_between_null",
            "SELECT starttime, sourceip, username FROM events "
            f"WHERE {within(narrow)} AND (username IS NULL OR username IN ('{user}')) "
            f"AND eventcount BETWEEN 1 AND 5 AND NOT (sourceport = 1) {order} LIMIT 50 "
            f"{clause(narrow)}",
        ),
    ]


async def fetch(
    reader: LabReader, offense_id: int, *, excluded: Sequence[str] = DEFAULT_EXCLUDED
) -> RawRecording:
    """Everything the recording needs from the lab, through `reader`."""
    offense = await reader.offense(offense_id)
    enrichment = await reader.enrichment(offense)
    calls: list[RecordedCall] = []
    wanted: list[tuple[str, dict[str, JsonValue]]] = [
        ("get_offense", {"offense_id": offense.offense_id}),
        *(("get_rule", {"rule_id": rule_id}) for rule_id in offense.rule_ids),
        *(("get_log_source", {"log_source_id": item}) for item in offense.log_source_ids),
    ]
    for tool_id, arguments in wanted:
        result = await reader.read_tool(offense, tool_id, arguments)
        if result.status is ToolStatus.OK:
            calls.append(RecordedCall(tool_id=tool_id, arguments=arguments, result=result))
    window = event_window(offense)
    events = await read_events(reader, offense, window, excluded)
    audits = []
    for name, query in audit_queries(offense, window, excluded):
        audits.append(
            AuditQuery(name=name, query=query, rows=await reader.ariel(offense, window, query))
        )
    return RawRecording(
        offense=offense,
        enrichment=enrichment,
        calls=calls,
        events=events,
        audits=audits,
        window=window,
    )


def build_recording(
    raw: RawRecording,
    directory: Path,
    *,
    excluded: Sequence[str] = DEFAULT_EXCLUDED,
    domains: Sequence[str] = (),
    hosts: Sequence[str] = (),
    recorded_at: datetime | None = None,
    gateway_version: str = "unknown",
    fork_version: str = "unknown",
) -> RecordingManifest:
    """Anonymize `raw`, check it, write it into `directory` and read it back.

    Raises RecordError, writing nothing, when an address outside the documentation ranges
    survives anonymization or the replay engine disagrees with the lab on an audit query.
    """
    anonymizer = Anonymizer(domains=domains, hosts=hosts)
    raw_calls = _open_offense_calls(raw.calls)
    documents: list[JsonValue] = [
        raw.offense.model_dump(mode="json"),
        raw.enrichment.model_dump(mode="json"),
        *(call.model_dump(mode="json") for call in raw_calls),
        *raw.events,
        *(audit.model_dump(mode="json") for audit in raw.audits),
    ]
    for document in documents:
        anonymizer.collect_all(document)
    anonymizer.freeze()

    offense = OffenseSnapshot.model_validate(anonymizer.value(raw.offense.model_dump(mode="json")))
    enrichment = EnrichmentContext.model_validate(
        anonymizer.value(raw.enrichment.model_dump(mode="json"))
    )
    calls = [
        RecordedCall.model_validate(anonymizer.value(call.model_dump(mode="json")))
        for call in raw_calls
    ]
    events = [_event(anonymizer.value(row)) for row in raw.events]
    audits = [
        AuditQuery.model_validate(anonymizer.value(audit.model_dump(mode="json")))
        for audit in raw.audits
    ]
    leaks = {
        address
        for document in (
            offense.model_dump(mode="json"),
            enrichment.model_dump(mode="json"),
            *(call.model_dump(mode="json") for call in calls),
            *(event.model_dump(mode="json") for event in events),
            *(audit.model_dump(mode="json") for audit in audits),
        )
        for text in strings(document)
        for address in foreign_addresses(text)
    }
    if leaks:
        raise RecordError(f"{len(leaks)} addresses outside the documentation ranges survived")
    mismatches = audit_mismatches(events, audits, now=raw.offense.last_updated_time + WINDOW_MARGIN)
    if mismatches:
        raise RecordError("the replay engine and the lab disagree: " + "; ".join(mismatches))

    manifest = write_recording(
        directory,
        recording_id=directory.name,
        offense=offense,
        enrichment=enrichment,
        calls=calls,
        events=events,
        audits=audits,
        window=raw.window,
        excluded=list(excluded),
        recorded_at=recorded_at or datetime.now(UTC),
        gateway_version=gateway_version,
        fork_version=fork_version,
    )
    try:
        load_recording(directory)
    except RecordingError as error:  # pragma: no cover - the writer and the reader agree
        raise RecordError(f"the written recording does not read back: {error}") from None
    return manifest


def _open_offense_calls(calls: Sequence[RecordedCall]) -> list[RecordedCall]:
    """Copy calls, making every successful `get_offense` row an open-offense view."""
    normalized: list[RecordedCall] = []
    for call in calls:
        if call.tool_id != "get_offense" or call.result.status is not ToolStatus.OK:
            normalized.append(call)
            continue
        rows: list[dict[str, JsonValue]] = []
        for source in call.result.data:
            row = dict(source)
            row.update(
                {
                    "status": "OPEN",
                    "inactive": False,
                    "close_time": None,
                    "closing_user": None,
                    "closing_reason_id": None,
                }
            )
            rows.append(row)
        normalized.append(
            call.model_copy(update={"result": call.result.model_copy(update={"data": rows})})
        )
    return normalized


def _event(row: JsonValue) -> RecordedEvent:
    if not isinstance(row, dict):
        raise RecordError("an event row is not an object")
    unknown = set(row) - set(EVENT_COLUMNS)
    if unknown:
        raise RecordError(
            f"the lab returned columns the recording does not keep: {sorted(unknown)}"
        )
    return RecordedEvent.model_validate(row)


def audit_mismatches(
    events: Sequence[RecordedEvent], audits: Sequence[AuditQuery], *, now: datetime
) -> list[str]:
    """The audit queries on which the replay engine's rows differ from the lab's."""
    table = [event.model_dump(mode="json") for event in events]
    found: list[str] = []
    for audit in audits:
        try:
            result = run_query(audit.query, table, now=now)
        except (QueryError, Unsupported) as error:
            found.append(f"{audit.name}: {error}")
            continue
        if not same_answer(audit.query, [dict(row) for row in result.rows], audit.rows):
            found.append(f"{audit.name}: {len(result.rows)} rows, the lab gave {len(audit.rows)}")
    return found


def same_answer(
    query: str, ours: Sequence[dict[str, JsonValue]], lab: Sequence[dict[str, JsonValue]]
) -> bool:
    """Whether two answers to `query` are the same. The order is compared by the ORDER BY
    column; rows with an equal value there, or all rows of a query without ORDER BY, are taken
    in any order."""
    order = _order_column(query)
    return _canonical(ours, order) == _canonical(lab, order)


def _order_column(query: str) -> str | None:
    parsed = parse(query)
    if len(parsed.order_by) != 1 or not isinstance(parsed.order_by[0].expr, Column):
        return None
    wanted = parsed.order_by[0].expr.name
    for item in parsed.items:
        if isinstance(item.expr, Column) and item.expr.name == wanted:
            return item.key
        if item.key.lower() == wanted:
            return item.key
    return None


def _canonical(rows: Sequence[dict[str, JsonValue]], order: str | None) -> list[str]:
    encoded = [json.dumps(row, sort_keys=True) for row in rows]
    if order is None:
        return sorted(encoded)
    first: dict[str, int] = {}
    for position, row in enumerate(rows):
        first.setdefault(json.dumps(row.get(order)), position)
    keyed = [
        (first[json.dumps(row.get(order))], text) for row, text in zip(rows, encoded, strict=True)
    ]
    return [text for _, text in sorted(keyed)]


# --- the live reader ----------------------------------------------------------------------------


class GatewayReader:
    """Reads the lab through the dev stack's gateway, as the sham agent `harness-recorder`."""

    def __init__(
        self,
        *,
        sessions: SessionFactory,
        triage_client: GatewayClient,
        triage_profile: ToolsetProfile,
        investigate_client: GatewayClient,
        investigate_profile: ToolsetProfile,
    ) -> None:
        self._sessions = sessions
        self._triage = (triage_client, triage_profile)
        self._investigate = (investigate_client, investigate_profile)

    async def offense(self, offense_id: int) -> OffenseSnapshot:
        client, profile = self._triage
        source = GatewayOffenseSource(gateway=client, profile=profile, sessions=self._sessions)
        snapshot = await source.get_offense(offense_id)
        if snapshot is None:
            raise RecordError(f"the offense source cannot read offense {offense_id}")
        return snapshot

    async def enrichment(self, offense: OffenseSnapshot) -> EnrichmentContext:
        async with self._sessions() as session:
            return await build_enrichment(session, offense, ioc_matcher=NoIocMatcher())

    async def read_tool(
        self, offense: OffenseSnapshot, tool_id: str, arguments: dict[str, JsonValue]
    ) -> ToolResult:
        client, profile = self._triage
        async with system_run(
            sessions=self._sessions,
            gateway=client,
            profile=profile,
            agent_id=AGENT_ID,
            case_id=f"case-{offense.offense_id}",
            objective=f"Record the result of {tool_id} for offense {offense.offense_id}.",
            window=event_window(offense),
            budget=SYSTEM_BUDGET,
        ) as run:
            return await run.send(
                tool_id,
                arguments,
                reason=f"Record offense {offense.offense_id} for the replay harness.",
                expected_evidence="The record QRadar holds, for a recording.",
            )

    async def ariel(
        self, offense: OffenseSnapshot, window: TimeWindow, query: str
    ) -> list[dict[str, JsonValue]]:
        client, profile = self._investigate
        async with system_run(
            sessions=self._sessions,
            gateway=client,
            profile=profile,
            agent_id=AGENT_ID,
            case_id=f"case-{offense.offense_id}",
            objective=f"Record the events around offense {offense.offense_id}.",
            window=window,
            budget=SYSTEM_BUDGET,
        ) as run:
            return await _search(run, query)


async def _call(
    run: SystemRun,
    tool_id: str,
    arguments: dict[str, JsonValue],
    *,
    reason: str,
    expected_evidence: str,
) -> ToolResult:
    """One call; a quota denial is waited out, any other result but `ok` is an error."""
    for _ in range(QUOTA_RETRIES):
        result = await run.send(
            tool_id, arguments, reason=reason, expected_evidence=expected_evidence
        )
        if result.status is ToolStatus.DENIED and (result.deny_reason or "").startswith(
            "quota_exhausted"
        ):
            await asyncio.sleep(QUOTA_WAIT_SECONDS)
            continue
        if result.status is not ToolStatus.OK:
            raise RecordError(f"{tool_id}: {result.status.value}: {result.deny_reason or ''}")
        return result
    raise RecordError(f"{tool_id}: the gateway's quota did not clear")


async def _search(run: SystemRun, query: str) -> list[dict[str, JsonValue]]:
    reason = "Record the events around a closed lab offense for the replay harness."
    evidence = "The events of the window, as a table."
    created = await _call(
        run,
        "create_ariel_search",
        {"query_expression": query},
        reason=reason,
        expected_evidence=evidence,
    )
    search_id = _search_id(created)
    try:
        total = 0
        for _ in range(STATUS_POLLS):
            status = await _call(
                run,
                "get_ariel_search_status",
                {"search_id": search_id, "wait_seconds": STATUS_WAIT_SECONDS},
                reason=reason,
                expected_evidence="The search's status.",
            )
            state = _first(status).get("status")
            if state == "COMPLETED":
                count = _first(status).get("record_count")
                total = count if isinstance(count, int) else 0
                break
            if state in ("ERROR", "CANCELED"):
                raise RecordError(f"the search ended {state}")
        else:
            raise RecordError("the search did not complete in time")
        # The gateway cuts a page at its byte cap, so a page may hold fewer rows than asked
        # for: the next page starts where the rows received end.
        rows: list[dict[str, JsonValue]] = []
        while len(rows) < total:
            page = await _call(
                run,
                "get_ariel_search_results",
                {"search_id": search_id, "limit": RESULT_PAGE, "start": len(rows)},
                reason=reason,
                expected_evidence=evidence,
            )
            if not page.data:
                raise RecordError(f"the search holds {total} rows and returned {len(rows)}")
            rows.extend(page.data)
        return rows
    finally:
        await run.send(
            "delete_ariel_search",
            {"search_id": search_id},
            reason="Delete the search the recorder started.",
            expected_evidence="Confirmation that the search was deleted.",
        )


def _first(result: ToolResult) -> dict[str, JsonValue]:
    if not result.data:
        raise RecordError("the gateway returned no row")
    return result.data[0]


def _search_id(result: ToolResult) -> str:
    value = _first(result).get("search_id")
    if not isinstance(value, str):
        raise RecordError("the gateway returned no search ID")
    return value


# --- settings -----------------------------------------------------------------------------------


def versions(root: Path) -> tuple[str, str]:
    """(gateway version, fork version) for the manifest.

    The gateway's version is its package's, with a digest of the registry files it serves the
    tools and rules from (`config/connectors/qradar.yaml`, `config/policies/qradar.yaml`): the
    same package serves other tools after a config change. The fork's is the connector's
    `server_version`, the commit of the MCP server the tool schemas are snapshots of."""
    try:
        package = importlib.metadata.version("ais0c-mcp-gateway")
    except importlib.metadata.PackageNotFoundError:
        package = "unknown"
    digest = hashlib.sha256()
    for name in (CONNECTOR_FILE, POLICY_FILE):
        digest.update((root / name).read_bytes())
    connector = yaml.safe_load((root / CONNECTOR_FILE).read_text(encoding="utf-8"))
    fork = (
        str(connector.get("server_version", "unknown"))
        if isinstance(connector, dict)
        else "unknown"
    )
    return f"{package}+registry.{digest.hexdigest()[:12]}", fork


async def record_offense(
    *,
    root: Path,
    offense_id: int,
    directory: Path,
    gateway_url: str,
    secrets_dir: Path,
    database_url: str,
    domains: Sequence[str],
    hosts: Sequence[str],
    excluded: Sequence[str],
) -> RecordingManifest:
    """Record `offense_id` from the lab behind the dev stack's gateway into `directory`."""
    from ais0c_storage import create_engine, create_session_factory

    triage_client = HttpGatewayClient(
        gateway_url, read_token(secrets_dir / f"gateway-token-{TRIAGE_PROFILE}")
    )
    investigate_client = HttpGatewayClient(
        gateway_url, read_token(secrets_dir / f"gateway-token-{INVESTIGATE_PROFILE}")
    )
    engine = create_engine(database_url)
    try:
        reader = GatewayReader(
            sessions=create_session_factory(engine),
            triage_client=triage_client,
            triage_profile=await triage_client.fetch_toolset(),
            investigate_client=investigate_client,
            investigate_profile=await investigate_client.fetch_toolset(),
        )
        raw = await fetch(reader, offense_id, excluded=excluded)
    finally:
        await engine.dispose()
    gateway_version, fork_version = versions(root)
    return build_recording(
        raw,
        directory,
        excluded=excluded,
        domains=domains,
        hosts=hosts,
        gateway_version=gateway_version,
        fork_version=fork_version,
    )


__all__ = [
    "AGENT_ID",
    "DEFAULT_EXCLUDED",
    "GatewayReader",
    "LabReader",
    "RawRecording",
    "RecordError",
    "audit_mismatches",
    "audit_queries",
    "build_recording",
    "event_window",
    "fetch",
    "read_events",
    "record_offense",
]
