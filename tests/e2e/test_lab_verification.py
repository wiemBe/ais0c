"""T-024 criterion 7: the Verification agent checks a real lab decision once, without Temporal.

`@pytest.mark.lab`: skipped unless the lab settings are set; a missing prerequisite (the dev
stack's gateway, a profile token, the LiteLLM key, `QRADAR_LAB_OFFENSE_ID`) skips it with a
reason.

| Variable | Meaning |
|---|---|
| `QRADAR_LAB_OFFENSE_ID` | The **closed** lab offense whose decision is checked (the planner names it) |
| `AIS0C_DATABASE_URL` | The dev stack's application database |
| `AIS0C_GATEWAY_URL` | The dev stack's gateway (deploy/compose/README.md) |
| `AIS0C_WORKER_SECRETS_DIR` | The directory of `gateway-token-qradar-verify-read` and `gateway-token-qradar-triage-read` |
| `LITELLM_BASE_URL`, `LITELLM_API_KEY` | LiteLLM, which serves `soc-verifier` |

The test never writes to QRadar: it opens no offense, closes none and writes no note. It reads
the offense through the gateway under `qradar-triage-read` (the only read profile with
`get_offense`; the Verification profile holds the four Ariel tools) and runs one AQL query in the
offense's window under `qradar-verify-read`. Both belong to a recorded system run, because the
gateway takes calls only under a run that is in progress.

That query's result is the evidence the reviewed decision's claims rest on: the test builds two
claims from it, one the rows show and one the rows refute. The agent then runs once against the
dev stack's gateway and the real `soc-verifier`, without Temporal, and the test prints what the
PR reports: which claim was contested, the re-fetch calls, the tokens and the duration.

AQL's `START`/`STOP` literals are read in the lab console's own time zone, which this test does
not know, so it tries `CONSOLE_ZONES` and keeps the query that returns rows. The agent is built
with the zone that query found (T-048): its prompt writes START and STOP in it.

Whether the model actually contested the refutable claim is a measurement, not an assertion: a
dev model may read less than a prod one (D-39). The harness measures that (T-030); here the
assertions are the ones that must hold for any model: a schema-valid result, no fabricated
evidence, no write call, and an offense that did not change.
"""

import asyncio
import json
import os
import re
import secrets
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pytest
import yaml
from e2e_support import MODEL_REGISTRY, REPO_ROOT
from pydantic import BaseModel, ConfigDict
from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart
from sqlalchemy import select

from ais0c_activities import SessionFactory
from ais0c_activities.gateway import SystemRun, system_run
from ais0c_activities.runtime import read_token
from ais0c_agents import (
    AgentManifest,
    AgentRun,
    ModelRegistry,
    ReviewedClaim,
    ReviewedDecision,
    ToolsetProfile,
    VerificationTask,
    build_model,
    build_verification_agent,
    load_agent_prompt,
    load_manifest,
    load_model_registry,
)
from ais0c_agents.gateway_http import HttpGatewayClient
from ais0c_agents.runner import FINAL_ANSWER_PROMPT
from ais0c_contracts import (
    AgentTask,
    Budget,
    CaseVerdict,
    Claim,
    Confidence,
    DataGapReason,
    EvidenceRef,
    Level,
    OffenseSnapshot,
    RunStatus,
    TimeWindow,
    ToolResult,
    VerificationResult,
)
from ais0c_policy import new_nonce
from ais0c_querylang import bound_query
from ais0c_storage import (
    create_engine,
    create_session_factory,
    create_sync_engine,
)
from ais0c_storage.migrate import upgrade
from ais0c_storage.models import AgentRunRow, EvidenceRow, ToolCallRow
from ais0c_storage.repositories import finish_agent_run, get_evidence_refs, start_agent_run

pytestmark = [pytest.mark.lab, pytest.mark.anyio]

REQUIRED = (
    "QRADAR_LAB_OFFENSE_ID",
    "AIS0C_DATABASE_URL",
    "AIS0C_GATEWAY_URL",
    "AIS0C_WORKER_SECRETS_DIR",
    "LITELLM_API_KEY",
)
VERIFICATION_MANIFEST = REPO_ROOT / "config/agents/verification.yaml"
# The Verification manifest's profile, and the one that can read an offense (architecture §11.2).
VERIFY_PROFILE = "qradar-verify-read"
OFFENSE_PROFILE = "qradar-triage-read"
ARIEL_TOOLS = frozenset(
    {
        "create_ariel_search",
        "get_ariel_search_status",
        "get_ariel_search_results",
        "delete_ariel_search",
    }
)
# At most what the verify profile's AQL Guard allows: a 2-hour window and 200 rows.
QUERY_WINDOW = timedelta(hours=1)
QUERY_LIMIT = 200
MIN_WINDOW = timedelta(minutes=1)
# Declared, not enforced: the setup's four calls, like a system run's budget (D-33).
SETUP_BUDGET = Budget(tokens=0, tool_calls=8, seconds=300)
# The window every call of this test declares (the profiles allow 31 days).
DECLARED_WINDOW = timedelta(days=30)
CONSOLE_ZONES = ("Europe/Istanbul", "UTC")
# Documentation addresses (RFC 5737) for the refutable claim: one no row holds.
ABSENT_ADDRESSES = tuple(f"198.51.100.{octet}" for octet in (7, 8, 9))
# An account name that cannot close the AQL string literal it goes into.
ACCOUNT_PATTERN = re.compile(r"^[A-Za-z0-9._$-]{1,64}$")
# What the reviewed decision says: a low-severity false positive, as Triage would have.
REVIEWED = ReviewedDecision(
    verdict=CaseVerdict.FP, confidence=Confidence.MEDIUM, ai_level=Level.LOW
)


class LabUnavailable(RuntimeError):
    """A prerequisite of the lab run is missing or does not answer."""


@dataclass(frozen=True)
class Lab:
    """The dev stack's gateway with the two profiles this test reads with."""

    url: str
    database_url: str
    verify: ToolsetProfile
    offense: ToolsetProfile
    verify_client: HttpGatewayClient
    offense_client: HttpGatewayClient


class _Ref(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int


class _Offense(BaseModel):
    """The fields of the lab offense the test reads (`get_offense`'s row)."""

    model_config = ConfigDict(extra="ignore")

    id: int
    status: str | None = None
    offense_type: int | None = None
    offense_source: str | None = None
    rules: list[_Ref] | None = None
    categories: list[str] | None = None
    magnitude: int | None = None
    start_time: int
    last_updated_time: int
    event_count: int | None = None
    log_sources: list[_Ref] | None = None


# --- fixtures -----------------------------------------------------------------------------------


@pytest.fixture
def lab() -> Lab:
    missing = [name for name in REQUIRED if not os.environ.get(name, "").strip()]
    if missing:
        pytest.skip(f"set {', '.join(missing)} to check a decision against the lab QRadar")
    url = os.environ["AIS0C_GATEWAY_URL"].rstrip("/")
    secrets = Path(os.environ["AIS0C_WORKER_SECRETS_DIR"])
    try:
        verify = asyncio.run(_profile(url, secrets, VERIFY_PROFILE))
        offense = asyncio.run(_profile(url, secrets, OFFENSE_PROFILE))
        clients = {
            name: HttpGatewayClient(url, read_token(secrets / f"gateway-token-{name}"))
            for name in (VERIFY_PROFILE, OFFENSE_PROFILE)
        }
    except (LabUnavailable, ValueError) as error:
        pytest.skip(f"the dev stack's gateway cannot serve the lab test: {error}")
    return Lab(
        url=url,
        database_url=os.environ["AIS0C_DATABASE_URL"],
        verify=verify,
        offense=offense,
        verify_client=clients[VERIFY_PROFILE],
        offense_client=clients[OFFENSE_PROFILE],
    )


@pytest.fixture
async def sessions(lab: Lab) -> AsyncIterator[SessionFactory]:
    """The dev stack's application database, migrated to the current schema."""
    engine = create_sync_engine(lab.database_url)
    try:
        with engine.begin() as connection:
            upgrade(connection)
    finally:
        engine.dispose()
    async_engine = create_engine(lab.database_url)
    try:
        yield create_session_factory(async_engine)
    finally:
        await async_engine.dispose()


async def _profile(url: str, secrets: Path, profile: str) -> ToolsetProfile:
    client = HttpGatewayClient(url, read_token(secrets / f"gateway-token-{profile}"))
    fetched = await client.fetch_toolset()
    if fetched.name != profile:
        raise LabUnavailable(f"the token is for {fetched.name}, not {profile}")
    return fetched


# --- the run ------------------------------------------------------------------------------------


async def test_a_lab_decision_is_checked_against_the_source(
    lab: Lab, sessions: SessionFactory, tmp_path: Path
) -> None:
    offense_id = int(os.environ["QRADAR_LAB_OFFENSE_ID"])
    case_id = f"case-{offense_id}"
    # A fresh run ID every run, so a test that fails halfway can be run again.
    run_id = f"{case_id}-verification-lab-{secrets.token_hex(4)}"
    manifest, registry = _config()
    before = await _read_offense(sessions, lab, case_id, offense_id)
    account = before.offense_source or ""
    if not account:
        pytest.fail(f"offense {offense_id} has no offense_source to query for")

    # The evidence: one Ariel query in the offense's window, in the verify profile.
    window = _query_window(before)
    rows, ref, zone = await _collect_evidence(sessions, lab, case_id, account, window)
    claims, absent = _claims(rows, account, ref.evidence_id)
    task = VerificationTask(
        task=_agent_task(manifest, run_id, case_id, window),
        reviewed=REVIEWED,
        claims=claims,
        evidence=[ref],
        offense=_snapshot(before),
    )
    print(
        json.dumps(
            {
                "offense_id": offense_id,
                "account": account,
                "evidence": {
                    "evidence_id": ref.evidence_id,
                    "query": ref.query_text,
                    "rows": len(rows),
                },
                "claims": [{"text": item.claim.text, "critical": item.critical} for item in claims],
                "refutable_claim_address": absent,
                "console_zone": zone,
            },
            indent=2,
        )
    )

    entry = registry[manifest.model_alias]
    prompt = load_agent_prompt(REPO_ROOT, manifest)
    async with sessions.begin() as session:
        await start_agent_run(
            session,
            run_id=run_id,
            task=task.task,
            prompt_version=prompt.version,
            model_alias=manifest.model_alias,
            model_target=_target(manifest),
            toolset_profile=VERIFY_PROFILE,
            started_at=datetime.now(UTC),
        )
    # The model context closes the provider's HTTP client when the run is over.
    async with build_model(
        manifest.model_alias,
        settings=entry.model_settings(),
        environ=os.environ,
        forced_tool_choice=entry.forced_tool_choice,
    ) as model:
        agent = build_verification_agent(
            manifest=manifest,
            prompt=prompt,
            profiles={lab.verify.name: lab.verify},
            gateway=lab.verify_client,
            model=model,
            console_zone=_zone(zone),
        )
        run = await agent.run(task, run_id=run_id, nonce=new_nonce())
    async with sessions.begin() as session:
        await finish_agent_run(
            session,
            run_id,
            status=run.status,
            result=run.result,
            tokens=run.usage.tokens,
            tool_calls=run.usage.tool_calls,
            ended_at=datetime.now(UTC),
        )
    calls, evidence, row = await _records(sessions, run_id)
    after = await _read_offense(sessions, lab, case_id, offense_id)

    report = _report(before, after, run, calls, evidence, row, task, claims)
    (tmp_path / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))

    # The real model produced a schema-valid VerificationResult.
    assert run.status is RunStatus.COMPLETED, run.error
    assert isinstance(run.result, VerificationResult)
    # No fabricated evidence: every ID the result carries is one the gateway recorded.
    cited = {
        *run.result.checked_evidence_ids,
        *(evidence_id for item in run.result.claims for evidence_id in item.evidence_ids),
    }
    recorded = {item.evidence_id for item in evidence} | {ref.evidence_id}
    assert cited <= recorded, f"cited evidence the gateway never recorded: {cited - recorded}"
    # The agent only read (AGENTS.md hard rule 2): its calls are the profile's Ariel tools.
    assert {call.intent.tool_id for call in calls} <= ARIEL_TOOLS
    # It ran on its own model and prompt.
    assert row.model_alias == "soc-verifier"
    assert row.prompt_version == "verification/v1"
    # Nothing was written to the offense.
    assert _state(before) == _state(after)


# --- reading the offense -------------------------------------------------------------------------


async def _read_offense(
    sessions: SessionFactory, lab: Lab, case_id: str, offense_id: int
) -> _Offense:
    """The offense as QRadar stores it, read in a recorded system run of the offense source."""
    now = datetime.now(UTC)
    async with system_run(
        sessions=sessions,
        gateway=lab.offense_client,
        profile=lab.offense,
        agent_id="offense-source",
        case_id=case_id,
        objective=f"Read QRadar offense {offense_id} for the T-024 lab test.",
        window=TimeWindow(start=now - DECLARED_WINDOW, end=now),
        budget=SETUP_BUDGET,
    ) as run:
        result = await run.call(
            "get_offense",
            {"offense_id": offense_id},
            reason="The Verification agent's input needs the offense's structural fields.",
            expected_evidence="One offense record with its status, times and event count.",
        )
    return _Offense.model_validate(_first_row(result))


def _first_row(result: ToolResult) -> dict[str, Any]:
    row = result.data[0] if result.data else {}
    assert isinstance(row, dict), result.model_dump(mode="json")
    return row


def _state(offense: _Offense) -> dict[str, Any]:
    """The fields that would change if the test had written to the offense."""
    return {
        "id": offense.id,
        "status": offense.status,
        "event_count": offense.event_count,
        "last_updated_time": offense.last_updated_time,
    }


def _snapshot(offense: _Offense) -> OffenseSnapshot:
    """The offense's structural fields, as the contract holds them.

    `description` and `rule_names` stay empty: the Verification agent's prompt does not take
    them (decision T-45), so this test does not read them.
    """
    return OffenseSnapshot(
        offense_id=offense.id,
        description="",
        offense_type=str(offense.offense_type or ""),
        offense_source=offense.offense_source or "",
        rule_ids=[rule.id for rule in offense.rules or []],
        rule_names=[],
        categories=list(offense.categories or []),
        magnitude=offense.magnitude or 0,
        start_time=_moment(offense.start_time),
        last_updated_time=_moment(offense.last_updated_time),
        event_count=offense.event_count or 0,
        log_source_ids=[source.id for source in offense.log_sources or []],
        source_ips=[],
        destination_ips=[],
        usernames=[offense.offense_source] if offense.offense_source else [],
    )


def _moment(millis: int) -> datetime:
    """QRadar's epoch milliseconds as an aware UTC datetime."""
    return datetime.fromtimestamp(millis / 1000, tz=UTC)


def _query_window(offense: _Offense) -> TimeWindow:
    """The offense's own window, at most what the verify profile's Guard allows."""
    start = _moment(offense.start_time)
    end = min(_moment(offense.last_updated_time), start + QUERY_WINDOW)
    return TimeWindow(start=start, end=max(end, start + MIN_WINDOW))


# --- the evidence and the claims -----------------------------------------------------------------


async def _collect_evidence(
    sessions: SessionFactory,
    lab: Lab,
    case_id: str,
    account: str,
    window: TimeWindow,
) -> tuple[list[dict[str, Any]], EvidenceRef, str]:
    """One Ariel query in the offense's window, in a recorded system run.

    Returns its rows, the `EvidenceRef` the gateway recorded for the result, so the claims
    under review rest on evidence QRadar really returned, and the console's time zone. That
    zone is unknown, so each candidate zone is tried until a query returns rows.
    """
    # The account comes from the lab's own scenario, but a value that could close the literal
    # is refused here rather than sent to QRadar.
    if not ACCOUNT_PATTERN.fullmatch(account):
        pytest.fail(f"offense source {account!r} is not an AQL-safe account name")
    query = "".join(
        (
            "SELECT username, sourceip, devicetype, QID FROM events ",
            f"WHERE username = '{account}'",
        )
    )
    tried: list[str] = []
    for zone in CONSOLE_ZONES:
        rows, ref = await _search(sessions, lab, case_id, query, window, zone)
        if rows and ref is not None:
            return rows, ref, zone
        tried.append(zone)
    pytest.fail(
        f"the query for {account} returned no rows in {', '.join(tried)}; add the lab console's "
        "time zone to CONSOLE_ZONES"
    )


async def _search(
    sessions: SessionFactory,
    lab: Lab,
    case_id: str,
    query: str,
    window: TimeWindow,
    zone: str,
) -> tuple[list[dict[str, Any]], EvidenceRef | None]:
    """The four calls of one Ariel search: its rows and the evidence of its result."""
    now = datetime.now(UTC)
    expression = bound_query(query, window=window, limit=QUERY_LIMIT, tz=_zone(zone))
    async with system_run(
        sessions=sessions,
        gateway=lab.verify_client,
        profile=lab.verify,
        # The same agent and profile as the agent run below: the evidence is collected with
        # the profile that will read it again.
        agent_id="verification",
        case_id=case_id,
        objective=f"Collect the evidence of {case_id} in the verify profile.",
        window=TimeWindow(start=now - DECLARED_WINDOW, end=now),
        budget=SETUP_BUDGET,
    ) as run:
        created = await _call(run, "create_ariel_search", {"query_expression": expression})
        search_id = str(_first_row(created).get("search_id") or "")
        if not search_id:
            return [], None
        try:
            await _call(
                run, "get_ariel_search_status", {"search_id": search_id, "wait_seconds": 20}
            )
            results = await _call(
                run, "get_ariel_search_results", {"search_id": search_id, "limit": QUERY_LIMIT}
            )
        finally:
            # The platform deletes every Ariel search it opens (architecture §11.1).
            await run.send(
                "delete_ariel_search",
                {"search_id": search_id},
                reason="The search is not needed any more.",
                expected_evidence="Confirmation that the search was deleted.",
            )
    rows = [row for row in results.data if isinstance(row, dict)]
    if not results.evidence_id:
        return [], None
    refs = await _evidence_refs(sessions, [results.evidence_id])
    return rows, refs.get(results.evidence_id)


async def _evidence_refs(
    sessions: SessionFactory, evidence_ids: Sequence[str]
) -> dict[str, EvidenceRef]:
    """The evidence the gateway recorded for these calls, as the case's prompt sees it."""
    async with sessions() as session:
        return await get_evidence_refs(session, list(evidence_ids))


async def _call(run: SystemRun, tool_id: str, arguments: dict[str, Any]) -> ToolResult:
    return await run.call(
        tool_id,
        arguments,
        reason="The lab test reads QRadar to build the evidence of the reviewed decision.",
        expected_evidence="The rows the Verification agent will read again at the source.",
    )


def _zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:  # pragma: no cover - the candidates ship with the image
        return ZoneInfo("UTC")


def _claims(
    rows: Sequence[dict[str, Any]], account: str, evidence_id: str
) -> tuple[list[ReviewedClaim], str]:
    """The two claims under review: one the rows show, one they refute.

    The refutable claim says every row has an address no row holds, so the agent can only agree
    with it by not reading the source again.
    """
    sources = {str(row.get("sourceip")) for row in rows if row.get("sourceip")}
    absent = next((address for address in ABSENT_ADDRESSES if address not in sources), None)
    if absent is None:
        pytest.fail(f"the rows hold every candidate address of {ABSENT_ADDRESSES}")
    count = len(rows)
    claims = (
        ReviewedClaim(
            claim=Claim(
                text=f"The query in the evidence returns {count} events of {account}.",
                evidence_ids=[evidence_id],
            ),
            critical=True,
        ),
        ReviewedClaim(
            claim=Claim(
                text=f"Every one of those {count} events of {account} comes from {absent}.",
                evidence_ids=[evidence_id],
            ),
            critical=True,
        ),
    )
    return list(claims), absent


# --- the run's records ---------------------------------------------------------------------------


async def _records(
    sessions: SessionFactory, run_id: str
) -> tuple[list[ToolCallRow], list[EvidenceRow], AgentRunRow]:
    """The calls the run made, the evidence they recorded and the run's own row."""
    async with sessions() as session:
        calls = list(
            await session.scalars(
                select(ToolCallRow)
                .where(ToolCallRow.run_id == run_id)
                .order_by(ToolCallRow.created_at, ToolCallRow.id)
            )
        )
        recorded = [call.evidence_id for call in calls if call.evidence_id]
        evidence = list(
            await session.scalars(select(EvidenceRow).where(EvidenceRow.evidence_id.in_(recorded)))
        )
        row = await session.get(AgentRunRow, run_id)
    assert row is not None, f"the run {run_id} is not in agent_runs"
    return calls, evidence, row


# --- the agent ----------------------------------------------------------------------------------


def _config() -> tuple[AgentManifest, ModelRegistry]:
    """The Verification manifest and the dev model registry (config/models/)."""
    registry = load_model_registry(REPO_ROOT / MODEL_REGISTRY)
    return load_manifest(VERIFICATION_MANIFEST, registry), registry


def _target(manifest: AgentManifest) -> str:
    """The model behind the manifest's alias, as the registry records it (T-24)."""
    data = yaml.safe_load((REPO_ROOT / MODEL_REGISTRY).read_text(encoding="utf-8"))
    return str(data[manifest.model_alias]["target"])


def _agent_task(
    manifest: AgentManifest, run_id: str, case_id: str, window: TimeWindow
) -> AgentTask:
    """The AgentTask of the run; its budget is the manifest's (T-026 builds this in a workflow)."""
    return AgentTask(
        task_id=run_id,
        parent_run_id=f"{case_id}-triage-1",
        case_id=case_id,
        agent_id=manifest.id,
        agent_version=manifest.version,
        objective=f"Check the decision on the QRadar offense of {case_id} (evaluation 1).",
        context_refs=[],
        time_window=TimeWindow(start=window.start, end=datetime.now(UTC)),
        budget=Budget(
            tokens=manifest.budgets.tokens,
            tool_calls=manifest.budgets.tool_calls,
            seconds=manifest.budgets.wall_clock_seconds,
        ),
    )


def _report(
    before: _Offense,
    after: _Offense,
    run: AgentRun[VerificationResult],
    calls: Sequence[ToolCallRow],
    evidence: Sequence[EvidenceRow],
    row: AgentRunRow,
    task: VerificationTask,
    claims: Sequence[ReviewedClaim],
) -> dict[str, Any]:
    """What the PR reports: the contested claim, the re-fetch calls, the tokens and the time."""
    result = run.result
    rows = {item.evidence_id: item.identifiers.get("rows") for item in evidence}
    return {
        "offense_id": before.id,
        "offense_state": _state(before),
        "offense_unchanged": _state(before) == _state(after),
        "run_id": row.run_id,
        "model_alias": row.model_alias,
        "prompt_version": row.prompt_version,
        "status": run.status.value,
        "error": run.error,
        "reviewed": task.reviewed.model_dump(mode="json"),
        "claims": [{"text": item.claim.text, "critical": item.critical} for item in claims],
        "agrees": None if result is None else result.agrees,
        "verdict": None if result is None else result.verdict.value,
        "confidence": None if result is None else result.confidence.value,
        "contested": [] if result is None else [item.claim_text for item in result.disagreements],
        "reasons": [] if result is None else [item.reason for item in result.disagreements],
        "checked_evidence_ids": [] if result is None else result.checked_evidence_ids,
        "claims_of_the_agent": [] if result is None else [item.text for item in result.claims],
        "data_gaps": (
            [] if result is None else [gap.model_dump(mode="json") for gap in result.data_gaps]
        ),
        "injection_suspected": None if result is None else result.injection_suspected,
        "re_read_calls": [
            (call.intent.tool_id, call.policy_decision.value, call.status.value, call.latency_ms)
            for call in calls
        ],
        "evidence_recorded": [item.evidence_id for item in evidence],
        # T-048: the agent's own queries (decision T-53) and whether its budget ran out (T-52).
        "aql_queries": [
            {
                "query": call.intent.arguments.get("query_expression"),
                "status": call.status.value,
                "deny_reason": call.deny_reason,
            }
            for call in calls
            if call.intent.tool_id == "create_ariel_search"
        ],
        "result_rows": [
            rows.get(call.evidence_id or "")
            for call in calls
            if call.intent.tool_id == "get_ariel_search_results"
        ],
        "tools_withdrawn": any(
            isinstance(part, UserPromptPart) and part.content == FINAL_ANSWER_PROMPT
            for message in run.messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        ),
        "budget_exhausted_gap": result is not None
        and any(gap.reason is DataGapReason.BUDGET_EXHAUSTED for gap in result.data_gaps),
        "model_requests": sum(isinstance(message, ModelResponse) for message in run.messages),
        "tokens": run.usage.tokens,
        "tool_calls": run.usage.tool_calls,
        "agent_seconds": run.usage.seconds,
        "wall_clock_seconds": (
            (row.ended_at - row.started_at).total_seconds() if row.ended_at is not None else None
        ),
    }
