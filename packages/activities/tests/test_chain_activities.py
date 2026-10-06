"""The chain's activities on a real database (T-026 criteria 1, 3, 4 and 5): the run records of
the chain agents with their model release and skill, the skill check, the evidence a run reads,
the router's candidates, the plan budgets and the plan record."""

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from activity_payloads import offense
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from ais0c_activities import (
    CaseSettings,
    ChainActivities,
    ChainAgent,
    SessionFactory,
    check_skill,
    evaluation_window,
    load_model_releases,
)
from ais0c_agents import load_agent_prompt, load_manifest, load_model_registry
from ais0c_contracts import (
    AgentTask,
    Budget,
    CasePlan,
    CatalogContext,
    CatalogMode,
    CatalogRule,
    EnrichmentContext,
    EvidenceRef,
    PlanStep,
    RunStatus,
    SkillRef,
    TimeWindow,
    Usage,
)
from ais0c_knowledge.skills import Mode, SkillRegistry, load_skills, read_content_hash
from ais0c_storage import ActorKind
from ais0c_storage.repositories import get_agent_run, list_audit, record_evidence

pytestmark = pytest.mark.anyio

REPO_ROOT = Path(__file__).resolve().parents[3]
MODEL_REGISTRY = REPO_ROOT / "config/models/registry.dev.yaml"
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
WINDOW = TimeWindow(start=NOW - timedelta(hours=2), end=NOW)
STEP_BUDGET = Budget(tokens=20000, tool_calls=6, seconds=120)
DCSYNC = REPO_ROOT / "skills/windows-dcsync/1.0.0"
# The rule the catalog maps to DCSync's technique trigger.
DCSYNC_RULE = 100201


def chain_agents() -> dict[str, ChainAgent]:
    registry = load_model_registry(MODEL_REGISTRY)
    releases = load_model_releases(MODEL_REGISTRY)
    agents: dict[str, ChainAgent] = {}
    for agent_id, profile in (
        ("orchestrator", ""),
        ("investigation", "qradar-investigate-read"),
        ("verification", "qradar-verify-read"),
        ("reporting", ""),
    ):
        manifest = load_manifest(REPO_ROOT / f"config/agents/{agent_id}.yaml", registry)
        agents[agent_id] = ChainAgent(
            manifest=manifest,
            prompt_version=load_agent_prompt(REPO_ROOT, manifest).version,
            toolset_profile=profile,
            model_release=releases[manifest.model_alias],
            takes_skill=agent_id == "investigation",
        )
    return agents


AGENTS = chain_agents()


def write_skill(
    root: Path,
    *,
    version: str = "1.0.0",
    approved: bool = True,
    expires_at: str = "2027-10-01",
    roles: str = "investigation",
) -> Path:
    """The repository's DCSync draft as `version`, approved unless `approved` is False."""
    target = root / "windows-dcsync" / version
    target.mkdir(parents=True)
    shutil.copy(DCSYNC / "instructions.md", target / "instructions.md")
    text = (
        (DCSYNC / "skill.yaml")
        .read_text(encoding="utf-8")
        .replace("version: 1.0.0", f"version: {version}")
        .replace("expires_at: 2027-10-01", f"expires_at: {expires_at}")
        .replace("allowed_agent_roles: [investigation]", f"allowed_agent_roles: [{roles}]")
    )
    if approved:
        text = text.replace("status: draft", "status: approved").replace(
            "approved_by: null", "approved_by: soc-lead"
        )
    (target / "skill.yaml").write_text(text, encoding="utf-8")
    if approved:
        digest = read_content_hash(target)
        text = text.replace("content_hash: null", f"content_hash: {digest}")
        (target / "skill.yaml").write_text(text, encoding="utf-8")
    return target


def ref_of(registry: SkillRegistry, version: str = "1.0.0") -> SkillRef:
    skill = registry.get("windows-dcsync", version)
    assert skill is not None
    return skill.ref


def activities(
    sessions: SessionFactory, skills: SkillRegistry, mode: Mode = "prod"
) -> ChainActivities:
    return ChainActivities(
        sessions=sessions,
        agents=AGENTS,
        skills=skills,
        skills_mode=mode,
        settings=CaseSettings(
            case_url_base="https://ais0c.example.com/cases",
        ),
        clock=lambda: NOW,
    )


@pytest.fixture
def skills(tmp_path: Path) -> SkillRegistry:
    write_skill(tmp_path)
    return load_skills(tmp_path, mode="prod")


async def begin(
    chain: ChainActivities,
    run_id: str = "case-7-investigation-1",
    agent: str = "investigation",
    *,
    budget: Budget | None = STEP_BUDGET,
    context_refs: list[str] | None = None,
    skill: SkillRef | None = None,
) -> tuple[AgentTask, str, SkillRef | None]:
    return await ActivityEnvironment().run(
        chain.begin_agent_run,
        run_id,
        agent,
        "case-7",
        "case-run-1",
        "Investigate the replication by svc_backup.",
        WINDOW,
        budget,
        ["ev_1", "ev_2"] if context_refs is None else context_refs,
        skill,
    )


async def audit(sessions: SessionFactory, run_id: str) -> list[tuple[str, dict[str, object]]]:
    async with sessions() as session:
        rows = await list_audit(session, object_id=run_id)
    return [(row.action, dict(row.details)) for row in rows]


# --- begin_agent_run ---------------------------------------------------------------------------


async def test_a_run_is_recorded_with_its_release_and_skill(
    sessions: SessionFactory, skills: SkillRegistry
) -> None:
    """Criterion 1: the record holds the agent's model release and the skill it uses."""
    chain = activities(sessions, skills)
    skill = ref_of(skills)

    task, nonce, used = await begin(chain, skill=skill)

    assert used == skill
    assert nonce
    async with sessions() as session:
        run = await get_agent_run(session, "case-7-investigation-1")
    assert run is not None
    assert run.task == task
    manifest = AGENTS["investigation"].manifest
    assert (run.agent_id, run.agent_version, run.prompt_version, run.toolset_profile) == (
        "investigation",
        manifest.version,
        AGENTS["investigation"].prompt_version,
        "qradar-investigate-read",
    )
    assert (run.model_alias, run.model_release) == (
        "soc-reasoning",
        AGENTS["investigation"].model_release,
    )
    assert run.skill == skill
    # The plan step's objective, window and budget, and the evidence the input cites.
    assert (run.task.objective, run.task.time_window, run.task.budget) == (
        "Investigate the replication by svc_backup.",
        WINDOW,
        STEP_BUDGET,
    )
    assert (run.task.context_refs, run.task.parent_run_id, run.task.case_id) == (
        ["ev_1", "ev_2"],
        "case-run-1",
        "case-7",
    )


async def test_without_a_step_budget_the_manifests_applies(
    sessions: SessionFactory, skills: SkillRegistry
) -> None:
    task, _, _ = await begin(
        activities(sessions, skills), "case-7-reporting-1", "reporting", budget=None
    )

    budgets = AGENTS["reporting"].manifest.budgets
    assert task.budget == Budget(
        tokens=budgets.tokens, tool_calls=budgets.tool_calls, seconds=budgets.wall_clock_seconds
    )
    async with sessions() as session:
        run = await get_agent_run(session, "case-7-reporting-1")
    assert run is not None
    assert (run.toolset_profile, run.skill) == ("", None)


async def test_a_retried_begin_returns_the_recorded_run(
    sessions: SessionFactory, skills: SkillRegistry
) -> None:
    chain = activities(sessions, skills)
    first, first_nonce, skill = await begin(chain, skill=ref_of(skills))

    again, again_nonce, skill_again = await begin(chain, skill=ref_of(skills))

    assert (again, skill_again) == (first, skill)
    assert again_nonce != first_nonce


async def test_a_finished_run_cannot_begin_again(
    sessions: SessionFactory, skills: SkillRegistry
) -> None:
    chain = activities(sessions, skills)
    await begin(chain)
    await ActivityEnvironment().run(
        chain.finish_agent_run,
        "case-7-investigation-1",
        RunStatus.FAILED,
        None,
        Usage(tokens=10, tool_calls=0, seconds=1.0),
        "UnexpectedModelBehavior",
    )

    async with sessions() as session:
        failed = await get_agent_run(session, "case-7-investigation-1")
    assert failed is not None
    assert failed.error == "UnexpectedModelBehavior"

    with pytest.raises(ApplicationError) as error:
        await begin(chain)
    assert error.value.non_retryable


async def test_an_unknown_agent_cannot_begin(
    sessions: SessionFactory, skills: SkillRegistry
) -> None:
    with pytest.raises(ApplicationError, match="no chain agent 'triage'") as error:
        await begin(activities(sessions, skills), "case-7-triage-1", "triage")
    assert error.value.non_retryable


async def test_a_skill_that_fails_its_check_is_left_out_and_the_reason_recorded(
    sessions: SessionFactory, skills: SkillRegistry
) -> None:
    """Criterion 5: the step runs without the skill; the reason is in the audit log under the
    run."""
    wrong_hash = ref_of(skills).model_copy(update={"content_hash": "sha256:" + "0" * 64})

    _, _, used = await begin(activities(sessions, skills), skill=wrong_hash)

    assert used is None
    async with sessions() as session:
        run = await get_agent_run(session, "case-7-investigation-1")
    assert run is not None
    assert run.skill is None
    [(action, details)] = await audit(sessions, "case-7-investigation-1")
    assert action == "case.skill.rejected"
    assert details["skill_id"] == "windows-dcsync"
    assert "content hash" in str(details["reason"])


async def test_a_skill_reaches_only_an_agent_that_takes_one(
    sessions: SessionFactory, skills: SkillRegistry
) -> None:
    """Verification's prompt has no Skill section (T-49)."""
    _, _, used = await begin(
        activities(sessions, skills),
        "case-7-verification-1",
        "verification",
        skill=ref_of(skills),
    )

    assert used is None
    [(action, details)] = await audit(sessions, "case-7-verification-1")
    assert (action, details["reason"]) == (
        "case.skill.rejected",
        "the verification agent takes no skill",
    )


# --- check_skill (criterion 5) ---------------------------------------------------------------


def test_an_approved_current_skill_passes(skills: SkillRegistry) -> None:
    assert (
        check_skill(skills, ref_of(skills), agent_role="investigation", now=NOW, mode="prod")
        is None
    )


def test_a_skill_of_another_role_fails(skills: SkillRegistry) -> None:
    reason = check_skill(skills, ref_of(skills), agent_role="verification", now=NOW, mode="prod")

    assert reason == "skill windows-dcsync 1.0.0 does not allow the verification role"


def test_a_skill_that_is_not_loaded_fails(skills: SkillRegistry) -> None:
    unknown = ref_of(skills).model_copy(update={"version": "9.9.9"})

    reason = check_skill(skills, unknown, agent_role="investigation", now=NOW, mode="prod")

    assert reason == "skill windows-dcsync 9.9.9 is not loaded"


def test_an_expired_skill_fails(tmp_path: Path) -> None:
    write_skill(tmp_path, expires_at="2026-10-01")
    registry = load_skills(tmp_path, mode="prod")

    reason = check_skill(
        registry, ref_of(registry), agent_role="investigation", now=NOW, mode="prod"
    )

    assert reason == "skill windows-dcsync 1.0.0 expired on 2026-10-01"


def test_an_older_approved_version_fails(tmp_path: Path) -> None:
    write_skill(tmp_path, version="1.0.0")
    write_skill(tmp_path, version="1.1.0")
    registry = load_skills(tmp_path, mode="prod")

    reason = check_skill(
        registry, ref_of(registry), agent_role="investigation", now=NOW, mode="prod"
    )

    assert reason == "skill windows-dcsync 1.0.0 is not the latest approved version, 1.1.0"
    assert (
        check_skill(
            registry, ref_of(registry, "1.1.0"), agent_role="investigation", now=NOW, mode="prod"
        )
        is None
    )


def test_a_draft_passes_only_in_dev(tmp_path: Path) -> None:
    write_skill(tmp_path, approved=False)
    registry = load_skills(tmp_path, mode="dev")
    draft = ref_of(registry)

    assert check_skill(registry, draft, agent_role="investigation", now=NOW, mode="dev") is None
    assert (
        check_skill(registry, draft, agent_role="investigation", now=NOW, mode="prod")
        == "skill windows-dcsync 1.0.0 is a draft, not approved"
    )


# --- the evidence and the end of a run --------------------------------------------------------


def evidence(evidence_id: str) -> EvidenceRef:
    return EvidenceRef.model_validate(
        {
            "evidence_id": evidence_id,
            "source": "qradar",
            "query_hash": "sha256:5d41402abc4b2a76",
            "query_text": "SELECT username FROM events",
            "time_start": WINDOW.start,
            "time_end": WINDOW.end,
            "identifiers": {"qid": "5000849"},
            "excerpt": "4662 on DC-01",
            "retrieved_at": NOW,
        }
    )


async def test_evidence_is_read_in_the_order_cited(
    sessions: SessionFactory, skills: SkillRegistry
) -> None:
    """Criterion 4: the EvidenceRefs of the task, from storage; IDs storage lacks are left
    out."""
    async with sessions.begin() as session:
        for evidence_id in ("ev_1", "ev_2", "ev_3"):
            await record_evidence(session, evidence(evidence_id))

    refs = await ActivityEnvironment().run(
        activities(sessions, skills).load_evidence, ["ev_3", "ev_9", "ev_1", "ev_3"]
    )

    assert refs == [evidence("ev_3"), evidence("ev_1")]


async def test_a_finished_run_keeps_its_result(
    sessions: SessionFactory, skills: SkillRegistry
) -> None:
    chain = activities(sessions, skills)
    await begin(chain, "case-7-orchestrator-1", "orchestrator", budget=None, context_refs=[])
    plan = CasePlan(
        task_id="case-7-orchestrator-1",
        status=RunStatus.COMPLETED,
        claims=[],
        data_gaps=[],
        injection_suspected=False,
        usage=Usage(tokens=900, tool_calls=0, seconds=3.0),
        steps=[
            PlanStep(
                agent_id="verification",
                objective="Check the claims.",
                time_window=WINDOW,
                budget=STEP_BUDGET,
            )
        ],
    )

    await ActivityEnvironment().run(
        chain.finish_agent_run, "case-7-orchestrator-1", RunStatus.COMPLETED, plan, plan.usage, None
    )

    async with sessions() as session:
        run = await get_agent_run(session, "case-7-orchestrator-1")
    assert run is not None
    assert (run.status, run.result, run.tokens, run.ended_at) == (
        RunStatus.COMPLETED,
        plan,
        900,
        NOW,
    )


# --- what the plan is made from ---------------------------------------------------------------


def dcsync_enrichment() -> EnrichmentContext:
    return EnrichmentContext(
        catalog=CatalogContext(
            rules=[
                CatalogRule(
                    rule_id=DCSYNC_RULE, mode=CatalogMode.ANALYZE, attack_techniques=["T1003.006"]
                )
            ],
            log_sources=[],
        ),
        critical_asset_hits=[],
        ioc_hits=[],
        entity_resolutions=[],
    )


async def test_the_router_lists_the_candidates_of_each_plan_agent(
    sessions: SessionFactory, skills: SkillRegistry
) -> None:
    """Criterion 3: the approved skill whose technique the offense's rule carries, for
    Investigation, with the skill's budget."""
    chain = activities(sessions, skills)

    listed = await ActivityEnvironment().run(
        chain.candidate_skills, offense(7, rule_ids=(DCSYNC_RULE,)), dcsync_enrichment()
    )

    assert listed == [
        ("investigation", ref_of(skills), Budget(tokens=250000, tool_calls=24, seconds=300))
    ]
    assert (
        await ActivityEnvironment().run(
            chain.candidate_skills, offense(7, rule_ids=(100305,)), dcsync_enrichment()
        )
        == []
    )


async def test_drafts_are_candidates_in_dev(sessions: SessionFactory, tmp_path: Path) -> None:
    write_skill(tmp_path, approved=False)
    registry = load_skills(tmp_path, mode="dev")
    chain = activities(sessions, registry, mode="dev")

    listed = await ActivityEnvironment().run(
        chain.candidate_skills, offense(7, rule_ids=(DCSYNC_RULE,)), dcsync_enrichment()
    )

    assert listed == [
        ("investigation", ref_of(registry), Budget(tokens=250000, tool_calls=24, seconds=300))
    ]


async def test_the_plan_budgets_are_the_settings_and_the_manifests(
    sessions: SessionFactory, skills: SkillRegistry
) -> None:
    plan_budget, agents = await ActivityEnvironment().run(activities(sessions, skills).plan_budgets)

    assert (
        plan_budget
        == CaseSettings(
            case_url_base="https://ais0c.example.com/cases",
        ).plan_budget
    )
    assert set(agents) == {"investigation", "verification"}
    budgets = AGENTS["verification"].manifest.budgets
    assert agents["verification"] == Budget(
        tokens=budgets.tokens, tool_calls=budgets.tool_calls, seconds=budgets.wall_clock_seconds
    )


async def test_the_evaluation_window_follows_triages_rule(
    sessions: SessionFactory, skills: SkillRegistry
) -> None:
    snapshot = offense(7, start=NOW - timedelta(days=40))

    window = await ActivityEnvironment().run(
        activities(sessions, skills).evaluation_window, snapshot
    )

    assert window == evaluation_window(snapshot, NOW)
    assert window == TimeWindow(start=NOW - timedelta(days=30), end=NOW)


async def test_a_replaced_plan_is_recorded_under_the_orchestrators_run(
    sessions: SessionFactory, skills: SkillRegistry
) -> None:
    """Criterion 3 (T-41): the reason a plan was replaced, or a step dropped."""
    chain = activities(sessions, skills)
    env = ActivityEnvironment()

    await env.run(
        chain.record_plan,
        "case-7",
        "case-7-orchestrator-1",
        "not_a_plan_agent",
        1,
        "step 1: triage is not a step",
        [],
        ["verification"],
    )
    await env.run(
        chain.record_plan,
        "case-7",
        "case-7-orchestrator-2",
        None,
        None,
        None,
        ["investigation"],
        ["verification"],
    )

    [(action, details)] = await audit(sessions, "case-7-orchestrator-1")
    assert (action, details["reason"], details["step"], details["steps"]) == (
        "case.plan.replaced",
        "not_a_plan_agent",
        1,
        ["verification"],
    )
    [(action, details)] = await audit(sessions, "case-7-orchestrator-2")
    assert (action, details["dropped"]) == ("case.plan.steps_dropped", ["investigation"])
    async with sessions() as session:
        [row] = await list_audit(session, object_id="case-7-orchestrator-1")
    assert (row.actor_kind, row.actor_id, row.object_type) == (
        ActorKind.SYSTEM,
        "case-workflow",
        "agent_run",
    )
