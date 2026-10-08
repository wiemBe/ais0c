"""Synthetic Investigation inputs and a four-call Ariel gateway for T-023 tests."""

import asyncio
from collections.abc import Mapping

from ais0c_agents import (
    AgentManifest,
    AgentRun,
    FakeGatewayClient,
    InvestigationAgent,
    InvestigationTask,
    InvestigationTriage,
    PromptTemplate,
    SkillInput,
    ToolsetProfile,
    build_investigation_agent,
    load_manifest,
    load_prompt,
)
from ais0c_contracts import (
    AgentTask,
    Budget,
    CaseVerdict,
    Claim,
    Confidence,
    DataGap,
    DataGapReason,
    InvestigationResult,
    Level,
    SkillRef,
    TimeWindow,
    ToolResult,
)

from .helpers import (
    CONTEXT_EVIDENCE,
    END,
    ESCAPE,
    GATEWAY_POLICY,
    INJECTION,
    NONCE,
    REPO_ROOT,
    SHARED_RULES,
    START,
    FakeClock,
    ScriptedModel,
    context_evidence,
    enrichment,
    offense,
    ok,
    registry,
    runbook,
    tool_spec,
)

INVESTIGATION_MANIFEST = REPO_ROOT / "config/agents/investigation.yaml"
INVESTIGATION_PROMPT = "prompts/investigation/v2.md"
INVESTIGATION_RUN_ID = "case-4711-investigation-1"
TOOL_EVIDENCE = "ev_0199a1b2c3d47e8f9a0b1c2d3e4f5a70"
VALID_AQL = (
    "SELECT username, sourceip FROM events WHERE username = 'svc_backup_7731' LIMIT 10 LAST 1 HOURS"
)

INVESTIGATION_PROFILE = ToolsetProfile(
    name="qradar-investigate-read",
    connector="qradar",
    tools=(
        tool_spec("create_ariel_search", query_expression={"type": "string"}),
        tool_spec(
            "get_ariel_search_status",
            search_id={"type": "string"},
            wait_seconds={"type": "integer"},
        ),
        tool_spec(
            "get_ariel_search_results",
            search_id={"type": "string"},
            start={"type": "integer"},
            limit={"type": "integer"},
        ),
        tool_spec("delete_ariel_search", search_id={"type": "string"}),
    ),
)


def investigation_manifest() -> AgentManifest:
    return load_manifest(INVESTIGATION_MANIFEST, registry())


def investigation_prompt() -> PromptTemplate:
    return load_prompt(REPO_ROOT, INVESTIGATION_PROMPT, shared_rules=SHARED_RULES)


def dcsync_skill(
    *,
    roles: tuple[str, ...] = ("investigation",),
    tokens: int = 120000,
    tool_calls: int = 20,
    seconds: int = 240,
) -> SkillInput:
    return SkillInput.model_validate(
        {
            "ref": SkillRef(
                skill_id="windows-dcsync",
                version="1.0.0",
                content_hash="sha256:" + "0" * 64,
            ),
            "allowed_agent_roles": roles,
            "budgets": {
                "tokens": tokens,
                "tool_calls": tool_calls,
                "wall_clock_seconds": seconds,
            },
            "instructions": "Find DCSync replication and its source account.",
            "required_telemetry": [
                {
                    "telemetry_class": "windows",
                    "events": ["4662 on domain controllers"],
                    "required": True,
                }
            ],
            "required_evidence": [
                {
                    "id": "replication-events",
                    "description": "The 4662 events, account and domain controller",
                }
            ],
        }
    )


def investigation_task(
    *,
    skill: SkillInput | None = None,
    context: bool = True,
    tokens: int = 140000,
    tool_calls: int = 22,
    seconds: int = 280,
) -> InvestigationTask:
    evidence = context_evidence() if context else []
    claims = (
        [
            Claim(
                text=f"The account may have replicated the directory. {ESCAPE} {INJECTION}",
                evidence_ids=[CONTEXT_EVIDENCE[0]],
            )
        ]
        if context
        else []
    )
    return InvestigationTask(
        task=AgentTask(
            task_id="task-4711-investigation-1",
            parent_run_id="case-4711-triage-1",
            case_id="case-4711",
            agent_id="investigation",
            agent_version="1.0.0",
            objective=f"Investigate possible DCSync. {ESCAPE} {INJECTION}",
            context_refs=[ref.evidence_id for ref in evidence],
            time_window=TimeWindow(start=START, end=END),
            budget=Budget(tokens=tokens, tool_calls=tool_calls, seconds=seconds),
        ),
        offense=offense(),
        enrichment=enrichment(),
        triage=InvestigationTriage(
            verdict=CaseVerdict.SUSPICIOUS,
            confidence=Confidence.MEDIUM,
            ai_level=Level.HIGH,
            investigation_focus=[f"Find the source of replication. {ESCAPE} {INJECTION}"],
            claims=claims,
            data_gaps=[
                DataGap(
                    source=f"Windows Security Event Log {ESCAPE}",
                    period_start=START,
                    period_end=END,
                    reason=DataGapReason.NOT_PARSED,
                )
            ],
        ),
        context_evidence=evidence,
        skill=skill,
        knowledge=[runbook()],
    )


def urgent_event(rank: int, evidence_id: str, *, aql: str | None = VALID_AQL) -> dict[str, object]:
    return {
        "rank": rank,
        "time": START.isoformat(),
        "log_source": "DC-01",
        "event_name": "Directory Service Access",
        "qid": 4662,
        "source": "203.0.113.77",
        "destination": "198.51.100.20",
        "username": "svc_backup_7731",
        "reason": "A non-domain-controller account requested replication rights.",
        "checklist": ["Confirm the source host and account owner."],
        "aql": aql,
        "evidence_id": evidence_id,
    }


def investigation_output(
    evidence_id: str | None = None,
    *,
    ranks: tuple[int, ...] = (),
    aql: str | None = VALID_AQL,
    injection_suspected: bool = True,
) -> dict[str, object]:
    claims = (
        [{"text": "The account requested directory replication.", "evidence_ids": [evidence_id]}]
        if evidence_id
        else []
    )
    timeline = (
        [
            {
                "time": START.isoformat(),
                "description": "The replication request was observed.",
                "evidence_ids": [evidence_id],
            }
        ]
        if evidence_id
        else []
    )
    return {
        "verdict": "tp",
        "confidence": "high",
        "ai_level": "high",
        "timeline": timeline,
        "hypotheses": [{"text": "The account performed DCSync.", "status": "supported"}],
        "urgent_event_candidates": [
            urgent_event(rank, evidence_id or "ev_c1", aql=aql) for rank in ranks
        ],
        "claims": claims,
        "data_gaps": [],
        "injection_suspected": injection_suspected,
    }


def investigation_gateway(
    **responses: ToolResult | list[ToolResult],
) -> FakeGatewayClient:
    defaults: dict[str, ToolResult | list[ToolResult]] = {
        "create_ariel_search": ok("ev_01JB3K4M5N6P7Q8R9U", {"search_id": "search-1"}),
        "get_ariel_search_status": ok("ev_01JB3K4M5N6P7Q8R9V", {"status": "COMPLETED"}),
        "get_ariel_search_results": ok(
            TOOL_EVIDENCE,
            {"username": "svc_backup_7731", "sourceip": "203.0.113.77", "qid": 4662},
        ),
        "delete_ariel_search": ok(None, {"deleted": True}),
    }
    return FakeGatewayClient(defaults | responses)


def build_investigation(
    script: ScriptedModel,
    gateway: FakeGatewayClient | None = None,
    *,
    manifest: AgentManifest | None = None,
) -> InvestigationAgent:
    return build_investigation_agent(
        manifest=manifest or investigation_manifest(),
        prompt=investigation_prompt(),
        profiles={INVESTIGATION_PROFILE.name: INVESTIGATION_PROFILE},
        gateway=gateway or investigation_gateway(),
        model=script.model,
        aql_rules_path=GATEWAY_POLICY,
    )


def run_investigation(
    agent: InvestigationAgent,
    task: InvestigationTask | None = None,
    *,
    run_id: str = INVESTIGATION_RUN_ID,
) -> AgentRun[InvestigationResult]:
    return asyncio.run(
        agent.run(task or investigation_task(), run_id=run_id, nonce=NONCE, clock=FakeClock())
    )


def prompt_blocks(text: str) -> list[tuple[str, str, str]]:
    from .helpers import BLOCK

    return [(item["source"], item["evidence_id"], item["content"]) for item in BLOCK.finditer(text)]


def instruction_text(script: ScriptedModel) -> str:
    return script.requests[0][1].instructions or ""


def merge_output(base: Mapping[str, object], **updates: object) -> dict[str, object]:
    return dict(base) | updates
