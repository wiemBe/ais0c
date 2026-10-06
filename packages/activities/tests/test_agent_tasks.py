"""The chain agents' tasks (T-026 criterion 4, decision T-45): what each agent's task is built
from, and how an input is fit to the agent's limits.

The inputs here are plain dataclasses with the attributes of the workflows package's input
models (`ais0c_workflows.agent_runtime`); services/worker runs the real ones.
"""

import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from activity_payloads import offense

from ais0c_activities import (
    investigation_task,
    orchestrator_task,
    reporting_task,
    verification_task,
)
from ais0c_agents import TriageDecision
from ais0c_contracts import (
    AgentTask,
    Budget,
    CaseVerdict,
    CatalogContext,
    Claim,
    Confidence,
    DataGap,
    DataGapReason,
    EnrichmentContext,
    EvidenceRef,
    Level,
    OffenseSnapshot,
    SkillRef,
    TimeWindow,
    UrgentEvent,
)
from ais0c_knowledge.skills import SkillRegistry, load_skills

REPO_ROOT = Path(__file__).resolve().parents[3]
NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
WINDOW = TimeWindow(start=NOW - timedelta(hours=2), end=NOW)
OFFENSE = offense(7)
ENRICHMENT = EnrichmentContext(
    catalog=CatalogContext(rules=[], log_sources=[]),
    critical_asset_hits=[],
    ioc_hits=[],
    entity_resolutions=[],
)
GAP = DataGap(
    source="Microsoft Windows Security Event Log",
    period_start=WINDOW.start,
    period_end=WINDOW.end,
    reason=DataGapReason.NO_DATA,
)


def task(agent_id: str) -> AgentTask:
    return AgentTask(
        task_id=f"case-7-{agent_id}-1",
        parent_run_id="case-run-1",
        case_id="case-7",
        agent_id=agent_id,
        agent_version="1.0.0",
        objective="Planned step.",
        context_refs=[],
        time_window=WINDOW,
        budget=Budget(tokens=20000, tool_calls=6, seconds=120),
    )


def claim(number: int, *evidence: int) -> Claim:
    return Claim(text=f"Claim {number}.", evidence_ids=[f"ev_{n}" for n in evidence])


def ref(number: int, *, window: TimeWindow = WINDOW) -> EvidenceRef:
    return EvidenceRef.model_validate(
        {
            "evidence_id": f"ev_{number}",
            "source": "qradar",
            "query_hash": "sha256:5d41402abc4b2a76",
            "query_text": "SELECT username FROM events",
            "time_start": window.start,
            "time_end": window.end,
            "identifiers": {"qid": "5000849"},
            "excerpt": f"Event {number}",
            "retrieved_at": NOW,
        }
    )


def urgent(evidence: int) -> UrgentEvent:
    return UrgentEvent(
        rank=1,
        time=NOW,
        log_source="DC-01",
        event_name="Directory Service Access",
        reason="Replication.",
        checklist=[],
        evidence_id=f"ev_{evidence}",
    )


@dataclass(frozen=True)
class Candidate:
    agent_id: str
    skill: SkillRef


@dataclass(frozen=True)
class Inputs:
    """The union of the four input models' attributes."""

    offense: OffenseSnapshot = field(default_factory=lambda: OFFENSE)
    enrichment: EnrichmentContext = field(default_factory=lambda: ENRICHMENT)
    verdict: CaseVerdict = CaseVerdict.SUSPICIOUS
    confidence: Confidence = Confidence.MEDIUM
    ai_level: Level = Level.MEDIUM
    notify_level: Level = Level.HIGH
    needs_investigation: bool = True
    investigation_focus: tuple[str, ...] = ("Replication rights",)
    claims: tuple[Claim, ...] = ()
    data_gaps: tuple[DataGap, ...] = (GAP,)
    injection_suspected: bool = False
    critical: bool = True
    candidates: tuple[Candidate, ...] = ()
    agents: dict[str, Budget] = field(
        default_factory=lambda: {
            "verification": Budget(tokens=80000, tool_calls=12, seconds=180),
            "investigation": Budget(tokens=150000, tool_calls=24, seconds=300),
        }
    )
    plan_budget: Budget = field(
        default_factory=lambda: Budget(tokens=250000, tool_calls=40, seconds=480)
    )
    urgent_event_candidates: tuple[UrgentEvent, ...] = ()


@pytest.fixture
def skills(tmp_path: Path) -> SkillRegistry:
    """The repository's draft skills, loaded as dev does."""
    shutil.copytree(REPO_ROOT / "skills", tmp_path / "skills")
    return load_skills(tmp_path / "skills", mode="dev")


def dcsync(skills: SkillRegistry) -> SkillRef:
    skill = skills.get("windows-dcsync", "1.0.0")
    assert skill is not None
    return skill.ref


def test_the_orchestrator_gets_triages_decision_candidates_and_budgets(
    skills: SkillRegistry,
) -> None:
    inputs = Inputs(
        claims=(claim(1, 1),),
        candidates=(Candidate("investigation", dcsync(skills)),),
        injection_suspected=True,
    )

    built = orchestrator_task(task("orchestrator"), inputs, skills)

    assert built.triage == TriageDecision(
        verdict=CaseVerdict.SUSPICIOUS,
        confidence=Confidence.MEDIUM,
        ai_level=Level.MEDIUM,
        needs_investigation=True,
        investigation_focus=("Replication rights",),
        data_gaps=(GAP,),
        injection_suspected=True,
    )
    [candidate] = built.candidates
    assert (candidate.ref, candidate.agent_role) == (dcsync(skills), "investigation")
    assert candidate.required_evidence[0].id == "replication-events"
    assert [(agent.agent_id, agent.budgets.wall_clock_seconds) for agent in built.agents] == [
        ("investigation", 300),
        ("verification", 180),
    ]
    assert built.plan_budget == inputs.plan_budget


def test_a_candidate_this_worker_did_not_load_stops_the_run(skills: SkillRegistry) -> None:
    other = dcsync(skills).model_copy(update={"content_hash": "sha256:" + "1" * 64})

    with pytest.raises(RuntimeError, match="not the one this worker loaded"):
        orchestrator_task(
            task("orchestrator"), Inputs(candidates=(Candidate("investigation", other),)), skills
        )


def test_investigation_gets_triages_claims_their_evidence_and_the_skill(
    skills: SkillRegistry,
) -> None:
    inputs = Inputs(claims=(claim(1, 1, 2), claim(2, 2)))

    built = investigation_task(
        task("investigation"), inputs, [ref(1), ref(2)], skills, dcsync(skills)
    )

    assert built.triage.claims == [claim(1, 1, 2), claim(2, 2)]
    assert (built.triage.verdict, built.triage.investigation_focus) == (
        CaseVerdict.SUSPICIOUS,
        ["Replication rights"],
    )
    assert built.triage.data_gaps == [GAP]
    assert [item.evidence_id for item in built.context_evidence] == ["ev_1", "ev_2"]
    loaded = skills.get("windows-dcsync", "1.0.0")
    assert built.skill is not None
    assert loaded is not None
    assert (built.skill.ref, built.skill.instructions) == (loaded.ref, loaded.instructions)
    assert investigation_task(task("investigation"), inputs, [], skills, None).skill is None


def test_a_claim_whose_evidence_storage_lacks_stays_out(skills: SkillRegistry) -> None:
    inputs = Inputs(claims=(claim(1, 1), claim(2, 9)))

    built = investigation_task(task("investigation"), inputs, [ref(1)], skills, None)

    assert built.triage.claims == [claim(1, 1)]


def test_investigation_keeps_to_its_evidence_limit(skills: SkillRegistry) -> None:
    claims = tuple(claim(n, n) for n in range(1, 36))
    evidence = [ref(n) for n in range(1, 36)]

    built = investigation_task(task("investigation"), Inputs(claims=claims), evidence, skills, None)

    assert len(built.context_evidence) == 30
    assert built.triage.claims == list(claims[:30])


def test_verification_gets_the_decision_and_critical_claims() -> None:
    inputs = Inputs(verdict=CaseVerdict.TP, ai_level=Level.HIGH, claims=(claim(1, 1), claim(2, 9)))

    built = verification_task(task("verification"), inputs, [ref(1)])

    assert (built.reviewed.verdict, built.reviewed.ai_level) == (CaseVerdict.TP, Level.HIGH)
    # A claim without its evidence stays: the agent's code contests it.
    assert [(item.claim, item.critical) for item in built.claims] == [
        (claim(1, 1), True),
        (claim(2, 9), True),
    ]
    assert [item.evidence_id for item in built.evidence] == ["ev_1"]


def test_verification_keeps_to_its_claim_limit() -> None:
    claims = tuple(claim(n, 1) for n in range(1, 26))

    built = verification_task(task("verification"), Inputs(claims=claims), [ref(1)])

    assert len(built.claims) == 20


def test_verification_unions_and_clips_the_claims_evidence_windows() -> None:
    first = TimeWindow(
        start=WINDOW.start - timedelta(hours=1), end=WINDOW.start + timedelta(minutes=20)
    )
    second = TimeWindow(
        start=WINDOW.end - timedelta(minutes=30), end=WINDOW.end + timedelta(hours=1)
    )

    built = verification_task(
        task("verification"),
        Inputs(claims=(claim(1, 1), claim(2, 2))),
        [ref(1, window=first), ref(2, window=second)],
    )

    assert built.task.time_window == WINDOW


def test_verification_uses_the_evidence_window_when_it_is_inside_the_case() -> None:
    evidence_window = TimeWindow(
        start=WINDOW.start + timedelta(minutes=15), end=WINDOW.end - timedelta(minutes=20)
    )

    built = verification_task(
        task("verification"), Inputs(claims=(claim(1, 1),)), [ref(1, window=evidence_window)]
    )

    assert built.task.time_window == evidence_window


def test_verification_uses_the_case_window_when_claim_evidence_has_no_window() -> None:
    built = verification_task(
        task("verification"), Inputs(claims=(claim(1, 1), claim(2, 9))), [ref(1)]
    )

    assert built.task.time_window == WINDOW


def test_reporting_gets_the_decision_claims_candidates_and_their_evidence() -> None:
    inputs = Inputs(
        claims=(claim(1, 1), claim(2, 9)),
        urgent_event_candidates=(urgent(3), urgent(8)),
        notify_level=Level.CRITICAL,
    )

    built = reporting_task(task("reporting"), inputs, [ref(1), ref(3)])

    assert (built.decision.verdict, built.decision.notify_level) == (
        CaseVerdict.SUSPICIOUS,
        Level.CRITICAL,
    )
    assert built.claims == [claim(1, 1)]
    assert built.urgent_event_candidates == [urgent(3)]
    assert [item.evidence_id for item in built.evidence] == ["ev_1", "ev_3"]
    assert built.data_gaps == [GAP]


def test_reporting_keeps_candidates_first_within_its_evidence_limit() -> None:
    """15 candidates take 15 of the 50 evidence pieces; claims of two pieces each fill 34
    more, and the 18th would pass the limit."""
    candidates = tuple(urgent(n) for n in range(100, 115))
    claims = tuple(claim(n, 2 * n - 1, 2 * n) for n in range(1, 21))
    evidence = [ref(n) for n in range(1, 41)] + [ref(n) for n in range(100, 115)]

    built = reporting_task(
        task("reporting"),
        Inputs(claims=claims, urgent_event_candidates=candidates),
        evidence,
    )

    assert built.urgent_event_candidates == list(candidates)
    assert built.claims == list(claims[:17])
    assert len(built.evidence) == 49


def test_reporting_keeps_to_its_claim_limit() -> None:
    claims = tuple(claim(n, 1) for n in range(1, 41))

    built = reporting_task(task("reporting"), Inputs(claims=claims), [ref(1)])

    assert built.claims == list(claims[:30])


def test_no_task_carries_free_text_of_an_earlier_agent(skills: SkillRegistry) -> None:
    """T-45: the inputs have no rationale, summary or hypotheses, and the tasks add none."""
    inputs = Inputs(claims=(claim(1, 1),), urgent_event_candidates=(urgent(1),))
    tasks = [
        orchestrator_task(task("orchestrator"), inputs, skills),
        investigation_task(task("investigation"), inputs, [ref(1)], skills, None),
        verification_task(task("verification"), inputs, [ref(1)]),
        reporting_task(task("reporting"), inputs, [ref(1)]),
    ]

    for built in tasks:
        dumped = built.model_dump_json()
        for name in ("rationale", "summary_tr", "hypotheses", "timeline"):
            assert f'"{name}"' not in dumped, (type(built).__name__, name)
