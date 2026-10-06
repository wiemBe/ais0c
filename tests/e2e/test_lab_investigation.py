"""T-023 criterion 7: run Investigation once against the dev stack, without Temporal.

The target is the closed offense named by ``QRADAR_LAB_OFFENSE_ID``. The offense snapshot and
its catalog context come from the same code the case workflow uses (the gateway offense source
and ``build_enrichment``). The test records the agent run before the gateway can receive a call,
gives the windows-dcsync draft directly (no router), and only uses read-profile tools. It reports
the call/AQL/model measurements requested by T-023 and verifies the offense stayed closed and
unchanged.
"""

import json
import os
import secrets
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from e2e_support import MODEL_REGISTRY, REPO_ROOT
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from ais0c_activities import (
    GatewayOffenseSource,
    NoIocMatcher,
    SessionFactory,
    build_enrichment,
)
from ais0c_activities.gateway import system_run
from ais0c_activities.runtime import read_token
from ais0c_agents import (
    AgentManifest,
    AgentRun,
    InvestigationTask,
    InvestigationTriage,
    ModelRegistry,
    SkillInput,
    ToolsetProfile,
    build_investigation_agent,
    build_model,
    load_agent_prompt,
    load_manifest,
    load_model_registry,
)
from ais0c_agents.gateway_http import HttpGatewayClient
from ais0c_contracts import (
    AgentTask,
    Budget,
    CaseVerdict,
    Confidence,
    InvestigationResult,
    Level,
    OffenseSnapshot,
    RunStatus,
    TimeWindow,
    ToolResult,
)
from ais0c_knowledge.skills import Skill, load_skills
from ais0c_policy import new_nonce
from ais0c_storage import create_engine, create_session_factory, create_sync_engine
from ais0c_storage.migrate import upgrade
from ais0c_storage.models import AgentRunRow, EvidenceRow, ToolCallRow
from ais0c_storage.repositories import finish_agent_run, start_agent_run

pytestmark = [pytest.mark.lab, pytest.mark.anyio]

REQUIRED = (
    "QRADAR_LAB_OFFENSE_ID",
    "AIS0C_DATABASE_URL",
    "AIS0C_GATEWAY_URL",
    "AIS0C_WORKER_SECRETS_DIR",
    "LITELLM_API_KEY",
)
INVESTIGATION_MANIFEST = REPO_ROOT / "config/agents/investigation.yaml"
INVESTIGATION_PROFILE = "qradar-investigate-read"
OFFENSE_PROFILE = "qradar-triage-read"
DECLARED_WINDOW = timedelta(days=30)
MIN_WINDOW = timedelta(minutes=1)
CREATE_TOOL = "create_ariel_search"
DELETE_TOOL = "delete_ariel_search"
SETUP_BUDGET = Budget(tokens=0, tool_calls=4, seconds=120)
FORBIDDEN_TOOL_PARTS = ("note", "close", "write", "delete_offense")
QRADAR_QUERY_REJECTION_TEXT = (
    "does not exist in catalog",
    "Wrong argument type",
    "Error Parsing",
    "Parsing error",
)


class LabUnavailable(RuntimeError):
    """A dev-stack prerequisite is missing or does not answer."""


@dataclass(frozen=True)
class Lab:
    database_url: str = field(repr=False)
    investigate: ToolsetProfile
    offense: ToolsetProfile
    investigate_client: HttpGatewayClient
    offense_client: HttpGatewayClient


class _Offense(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int
    status: str | None = None
    event_count: int | None = None
    last_updated_time: int


@pytest.fixture
def lab() -> Lab:
    missing = [name for name in REQUIRED if not os.environ.get(name, "").strip()]
    if missing:
        pytest.skip(f"set {', '.join(missing)} to run Investigation against the lab")
    url = os.environ["AIS0C_GATEWAY_URL"].rstrip("/")
    secret_dir = Path(os.environ["AIS0C_WORKER_SECRETS_DIR"])
    try:
        investigate = _fetch_profile(url, secret_dir, INVESTIGATION_PROFILE)
        offense = _fetch_profile(url, secret_dir, OFFENSE_PROFILE)
        clients = {
            name: HttpGatewayClient(url, read_token(secret_dir / f"gateway-token-{name}"))
            for name in (INVESTIGATION_PROFILE, OFFENSE_PROFILE)
        }
    except (LabUnavailable, ValueError) as error:
        pytest.skip(f"the dev stack cannot serve the Investigation lab test: {error}")
    return Lab(
        database_url=os.environ["AIS0C_DATABASE_URL"],
        investigate=investigate,
        offense=offense,
        investigate_client=clients[INVESTIGATION_PROFILE],
        offense_client=clients[OFFENSE_PROFILE],
    )


@pytest.fixture
async def sessions(lab: Lab) -> AsyncIterator[SessionFactory]:
    sync_engine = create_sync_engine(lab.database_url)
    try:
        with sync_engine.begin() as connection:
            upgrade(connection)
    finally:
        sync_engine.dispose()
    engine = create_engine(lab.database_url)
    try:
        yield create_session_factory(engine)
    finally:
        await engine.dispose()


def _fetch_profile(url: str, secret_dir: Path, name: str) -> ToolsetProfile:
    async def fetch() -> ToolsetProfile:
        client = HttpGatewayClient(url, read_token(secret_dir / f"gateway-token-{name}"))
        profile = await client.fetch_toolset()
        if profile.name != name:
            raise LabUnavailable(f"the {name} token serves {profile.name}")
        return profile

    import asyncio

    return asyncio.run(fetch())


async def test_closed_dcsync_offense_is_investigated_once(
    lab: Lab, sessions: SessionFactory, tmp_path: Path
) -> None:
    offense_id = int(os.environ["QRADAR_LAB_OFFENSE_ID"])
    case_id = f"case-{offense_id}"
    run_id = f"{case_id}-investigation-lab-{secrets.token_hex(4)}"
    manifest, registry = _config()
    before = await _read_offense(sessions, lab, case_id, offense_id)
    assert before.status == "CLOSED", f"lab offense {offense_id} must be closed"
    source = GatewayOffenseSource(
        gateway=lab.offense_client, profile=lab.offense, sessions=sessions
    )
    snapshot = await source.get_offense(offense_id)
    assert snapshot is not None, f"the offense source cannot read lab offense {offense_id}"
    async with sessions() as session:
        enrichment = await build_enrichment(session, snapshot, ioc_matcher=NoIocMatcher())
    skill = _dcsync_skill()
    task = InvestigationTask(
        task=_agent_task(manifest, run_id, case_id, _window(snapshot)),
        offense=snapshot,
        enrichment=enrichment,
        triage=InvestigationTriage(
            verdict=CaseVerdict.SUSPICIOUS,
            confidence=Confidence.MEDIUM,
            ai_level=Level.HIGH,
            investigation_focus=[
                "Determine whether a non-domain-controller account used replication rights, "
                "and identify the request source."
            ],
            claims=[],
            data_gaps=[],
        ),
        context_evidence=[],
        skill=skill,
        knowledge=[],
    )
    prompt = load_agent_prompt(REPO_ROOT, manifest)
    entry = registry[manifest.model_alias]
    started = datetime.now(UTC)
    async with sessions.begin() as session:
        await start_agent_run(
            session,
            run_id=run_id,
            task=task.task,
            prompt_version=prompt.version,
            model_alias=manifest.model_alias,
            model_target=_target(manifest),
            toolset_profile=INVESTIGATION_PROFILE,
            started_at=started,
        )

    async with build_model(
        manifest.model_alias,
        settings=entry.model_settings(),
        environ=os.environ,
        forced_tool_choice=entry.forced_tool_choice,
    ) as model:
        agent = build_investigation_agent(
            manifest=manifest,
            prompt=prompt,
            profiles={lab.investigate.name: lab.investigate},
            gateway=lab.investigate_client,
            model=model,
            aql_rules_path=REPO_ROOT / "config/policies/qradar.yaml",
        )
        run = await agent.run(task, run_id=run_id, nonce=new_nonce())

    ended = datetime.now(UTC)
    async with sessions.begin() as session:
        await finish_agent_run(
            session,
            run_id,
            status=run.status,
            result=run.result,
            tokens=run.usage.tokens,
            tool_calls=run.usage.tool_calls,
            ended_at=ended,
        )
    calls, evidence, row = await _records(sessions, run_id)
    searches_cleaned = await _cleanup_searches(
        sessions, lab, case_id, task.task.time_window, _searches_left(calls, evidence)
    )
    after = await _read_offense(sessions, lab, case_id, offense_id)
    report = _report(
        before,
        after,
        run,
        calls,
        evidence,
        row,
        skill,
        started,
        ended,
        searches_cleaned,
    )
    (tmp_path / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))

    assert {call.intent.tool_id for call in calls} <= {tool.id for tool in lab.investigate.tools}
    assert all(call.intent.run_id == run_id for call in calls)
    assert not any(part in call.intent.tool_id for call in calls for part in FORBIDDEN_TOOL_PARTS)
    assert row.model_alias == "soc-reasoning"
    assert row.prompt_version == "investigation/v1"
    assert _state(before) == _state(after)
    if run.status is RunStatus.BUDGET_EXHAUSTED:
        # The manifest and draft skill carry starting budgets that T-030 will tune. Exhaustion
        # is therefore a measured lab outcome, provided the runner stopped at that boundary.
        assert run.result is None
        assert run.error is not None
        assert run.error.startswith("UsageLimitExceeded:")
    else:
        assert run.status is RunStatus.COMPLETED, run.error
        assert isinstance(run.result, InvestigationResult)
        recorded = {item.evidence_id for item in evidence}
        cited = {
            *(evidence_id for claim in run.result.claims for evidence_id in claim.evidence_ids),
            *(evidence_id for item in run.result.timeline for evidence_id in item.evidence_ids),
            *(item.evidence_id for item in run.result.urgent_event_candidates),
        }
        assert cited <= recorded, (
            f"the model cited evidence the gateway did not record: {cited - recorded}"
        )


async def _read_offense(
    sessions: SessionFactory, lab: Lab, case_id: str, offense_id: int
) -> _Offense:
    now = datetime.now(UTC)
    async with system_run(
        sessions=sessions,
        gateway=lab.offense_client,
        profile=lab.offense,
        agent_id="offense-source",
        case_id=case_id,
        objective=f"Read QRadar offense {offense_id} for the T-023 lab test.",
        window=TimeWindow(start=now - DECLARED_WINDOW, end=now),
        budget=SETUP_BUDGET,
    ) as run:
        result = await run.call(
            "get_offense",
            {"offense_id": offense_id},
            reason="Investigation needs the closed offense snapshot.",
            expected_evidence="The offense status, time window, rules and source.",
        )
    return _Offense.model_validate(_first_row(result))


def _first_row(result: ToolResult) -> dict[str, Any]:
    row = result.data[0] if result.data else {}
    assert isinstance(row, dict), result.model_dump(mode="json")
    return row


def _window(offense: OffenseSnapshot) -> TimeWindow:
    return TimeWindow(
        start=offense.start_time,
        end=max(offense.last_updated_time, offense.start_time + MIN_WINDOW),
    )


def _state(offense: _Offense) -> dict[str, Any]:
    return {
        "id": offense.id,
        "status": offense.status,
        "event_count": offense.event_count,
        "last_updated_time": offense.last_updated_time,
    }


def _dcsync_skill() -> SkillInput:
    loaded = next(
        skill
        for skill in load_skills(REPO_ROOT / "skills", mode="dev")
        if skill.manifest.id == "windows-dcsync" and skill.manifest.version == "1.0.0"
    )
    return _skill_input(loaded)


def _skill_input(skill: Skill) -> SkillInput:
    manifest = skill.manifest
    return SkillInput.model_validate(
        {
            "ref": skill.ref,
            "allowed_agent_roles": manifest.allowed_agent_roles,
            "budgets": manifest.budgets.model_dump(),
            "instructions": skill.instructions,
            "required_telemetry": [item.model_dump() for item in manifest.required_telemetry],
            "required_evidence": [item.model_dump() for item in manifest.required_evidence],
        }
    )


def _config() -> tuple[AgentManifest, ModelRegistry]:
    registry = load_model_registry(REPO_ROOT / MODEL_REGISTRY)
    return load_manifest(INVESTIGATION_MANIFEST, registry), registry


def _target(manifest: AgentManifest) -> str:
    raw = yaml.safe_load((REPO_ROOT / MODEL_REGISTRY).read_text(encoding="utf-8"))
    return str(raw[manifest.model_alias]["target"])


def _agent_task(
    manifest: AgentManifest, run_id: str, case_id: str, window: TimeWindow
) -> AgentTask:
    return AgentTask(
        task_id=run_id,
        parent_run_id=f"{case_id}-triage-1",
        case_id=case_id,
        agent_id=manifest.id,
        agent_version=manifest.version,
        objective="Investigate the suspected DCSync activity and identify its source.",
        context_refs=[],
        time_window=window,
        budget=Budget(
            tokens=manifest.budgets.tokens,
            tool_calls=manifest.budgets.tool_calls,
            seconds=manifest.budgets.wall_clock_seconds,
        ),
    )


async def _records(
    sessions: SessionFactory, run_id: str
) -> tuple[list[ToolCallRow], list[EvidenceRow], AgentRunRow]:
    async with sessions() as session:
        calls = list(
            await session.scalars(
                select(ToolCallRow)
                .where(ToolCallRow.run_id == run_id)
                .order_by(ToolCallRow.created_at, ToolCallRow.id)
            )
        )
        evidence_ids = [call.evidence_id for call in calls if call.evidence_id]
        evidence = list(
            await session.scalars(
                select(EvidenceRow).where(EvidenceRow.evidence_id.in_(evidence_ids))
            )
        )
        row = await session.get(AgentRunRow, run_id)
    assert row is not None
    return calls, evidence, row


def _searches_left(calls: Sequence[ToolCallRow], evidence: Sequence[EvidenceRow]) -> list[str]:
    """Searches the agent created and did not delete; the fork refuses a second delete."""

    created_evidence = {
        call.evidence_id
        for call in calls
        if call.intent.tool_id == CREATE_TOOL and call.evidence_id
    }
    created = {
        search_id
        for item in evidence
        if item.evidence_id in created_evidence
        and isinstance((search_id := item.identifiers.get("search_id")), str)
    }
    deleted = {
        search_id
        for call in calls
        if call.intent.tool_id == DELETE_TOOL
        and call.status.value == "ok"
        and isinstance((search_id := call.intent.arguments.get("search_id")), str)
    }
    return sorted(created - deleted)


async def _cleanup_searches(
    sessions: SessionFactory,
    lab: Lab,
    case_id: str,
    window: TimeWindow,
    search_ids: Sequence[str],
) -> int:
    if not search_ids:
        return 0
    async with system_run(
        sessions=sessions,
        gateway=lab.investigate_client,
        profile=lab.investigate,
        agent_id="investigation",
        case_id=case_id,
        objective="Delete Ariel searches left by the T-023 lab run.",
        window=window,
        budget=Budget(tokens=0, tool_calls=len(search_ids), seconds=120),
    ) as cleanup:
        for search_id in search_ids:
            await cleanup.call(
                DELETE_TOOL,
                {"search_id": search_id},
                reason="The lab run must leave no Ariel search behind.",
                expected_evidence="Confirmation that the search was deleted.",
            )
    return len(search_ids)


def _report(
    before: _Offense,
    after: _Offense,
    run: AgentRun[InvestigationResult],
    calls: Sequence[ToolCallRow],
    evidence: Sequence[EvidenceRow],
    row: AgentRunRow,
    skill: SkillInput,
    started: datetime,
    ended: datetime,
    searches_cleaned: int,
) -> dict[str, Any]:
    create_calls = [call for call in calls if call.intent.tool_id == CREATE_TOOL]
    quoted = [
        call
        for call in create_calls
        if '"' in str(call.intent.arguments.get("query_expression") or "")
    ]
    guard_rejections = [
        call
        for call in create_calls
        if call.deny_reason is not None and call.deny_reason.startswith("aql_guard")
    ]
    # The fork returns QRadar's AQL validation detail but not its HTTP status. These are the
    # parser/catalog/type messages QRadar emits with 422 for a create request that passed Guard.
    qradar_422 = [
        call
        for call in create_calls
        if call.status.value == "error"
        and call.policy_decision.value == "allow"
        and call.deny_reason is not None
        and any(marker in call.deny_reason for marker in QRADAR_QUERY_REJECTION_TEXT)
    ]
    result = run.result
    return {
        "offense_id": before.id,
        "offense_state": _state(before),
        "offense_unchanged": _state(before) == _state(after),
        "run_id": row.run_id,
        "model_alias": row.model_alias,
        "prompt_version": row.prompt_version,
        "skill": skill.ref.model_dump(mode="json"),
        "status": run.status.value,
        "error": run.error,
        "tool_calls": len(calls),
        "tool_call_usage": run.usage.tool_calls,
        "tools": [call.intent.tool_id for call in calls],
        "aql_create_calls": len(create_calls),
        "aql_guard_rejections": len(guard_rejections),
        "qradar_422_rejections": len(qradar_422),
        "double_quoted_field_queries": [
            {
                "status": call.status.value,
                "policy_decision": call.policy_decision.value,
                "deny_reason": call.deny_reason,
            }
            for call in quoted
        ],
        "double_quoted_field_json_broken": any(call.status.value == "error" for call in quoted),
        "ariel_searches_cleaned": searches_cleaned,
        "tokens": run.usage.tokens,
        "agent_seconds": run.usage.seconds,
        "wall_clock_seconds": (ended - started).total_seconds(),
        "verdict": None if result is None else result.verdict.value,
        "confidence": None if result is None else result.confidence.value,
        "ai_level": None if result is None else result.ai_level.value,
        "claims": [] if result is None else [claim.text for claim in result.claims],
        "timeline_entries": 0 if result is None else len(result.timeline),
        "hypotheses": 0 if result is None else len(result.hypotheses),
        "urgent_event_candidates": (0 if result is None else len(result.urgent_event_candidates)),
        "injection_suspected": None if result is None else result.injection_suspected,
        "evidence_recorded": [item.evidence_id for item in evidence],
    }
