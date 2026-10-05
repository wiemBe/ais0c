"""T-043 criterion 2: one rule for every evidence field of an agent's output.

check_evidence maps the aliases in `Claim.evidence_ids`, `TimelineEntry.evidence_ids`,
`UrgentEvent.evidence_id`, `Recommendation.evidence_ids` and
`VerificationResult.checked_evidence_ids` to the gateway's evidence IDs, tool aliases (`ev_<n>`)
and context aliases (`ev_c<n>`) alike, and sends an unknown alias back to the model.

The contracts' evidence fields are found from their JSON Schemas (the `EvidenceId` pattern), so a
field added later is either covered or breaks these tests.
"""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Annotated

import pytest
from pydantic import BaseModel, Field
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

import ais0c_contracts
from ais0c_agents import RunDeps, check_evidence, create_agent, evidence_fields
from ais0c_agents.toolset import build_gateway_toolset
from ais0c_contracts import (
    ActionType,
    Budget,
    CaseReport,
    CaseVerdict,
    Claim,
    Confidence,
    ContractModel,
    EvidenceId,
    InvestigationResult,
    Level,
    Recommendation,
    RunStatus,
    TimelineEntry,
    TimeWindow,
    UrgentEvent,
    Usage,
    VerificationResult,
)

from .helpers import (
    CONTEXT_EVIDENCE,
    END,
    NONCE,
    OFFENSE_EVIDENCE,
    START,
    TRIAGE_PROFILE,
    ScriptedModel,
    answer,
    call,
    context_evidence,
    gateway,
    retry_prompts,
    run_summary,
    summary_manifest,
    summary_output,
    summary_spec,
)

EVIDENCE_ID_PATTERN = r"^ev_\S+$"
TOOL_EVIDENCE = "ev_0199a1b2c3d47e8f9a0b1c2d3e4f5a70"

# Every evidence field of the contracts. Those an agent writes are citations and must be checked;
# the others carry evidence IDs into an agent or out of the gateway. A new field breaks
# test_every_evidence_field_of_the_contracts_is_known: if test_check_evidence_sees_every_evidence
# _field passes, check_evidence covers it, and it goes into one of these sets.
CITATION_FIELDS = {
    ("Claim", "evidence_ids"),
    ("TimelineEntry", "evidence_ids"),
    ("UrgentEvent", "evidence_id"),
    ("Recommendation", "evidence_ids"),
    ("VerificationResult", "checked_evidence_ids"),
}
OTHER_FIELDS = {
    ("EvidenceRef", "evidence_id"),  # the evidence itself, issued by the gateway
    ("AgentTask", "context_refs"),  # an agent's input
    ("ToolResult", "evidence_id"),  # the gateway's answer to a tool call
}


def contract_models() -> list[type[ContractModel]]:
    found: list[type[ContractModel]] = []
    pending: list[type[ContractModel]] = [ContractModel]
    while pending:
        model = pending.pop()
        for subclass in model.__subclasses__():
            if subclass not in found and subclass.__module__.startswith("ais0c_contracts"):
                found.append(subclass)
                pending.append(subclass)
    return found


def schema_evidence_fields(model: type[BaseModel]) -> frozenset[str]:
    """The model's own properties whose JSON Schema holds the EvidenceId pattern."""
    properties: Mapping[str, object] = model.model_json_schema().get("properties", {})
    return frozenset(name for name, schema in properties.items() if _has_pattern(schema))


def _has_pattern(schema: object) -> bool:
    if isinstance(schema, list):
        return any(_has_pattern(item) for item in schema)
    if not isinstance(schema, dict):
        return False
    if schema.get("pattern") == EVIDENCE_ID_PATTERN:
        return True
    return any(
        _has_pattern(schema.get(key))
        for key in ("items", "prefixItems", "additionalProperties", "anyOf", "oneOf", "allOf")
    )


def test_the_contracts_have_evidence_fields() -> None:
    models = contract_models()
    assert ais0c_contracts.InvestigationResult in models
    assert len(models) > 30


@pytest.mark.parametrize("model", contract_models(), ids=lambda model: model.__name__)
def test_check_evidence_sees_every_evidence_field(model: type[ContractModel]) -> None:
    assert evidence_fields(model) == schema_evidence_fields(model)


def test_every_evidence_field_of_the_contracts_is_known() -> None:
    found = {
        (model.__name__, name)
        for model in contract_models()
        for name in schema_evidence_fields(model)
        if name not in _inherited(model)
    }

    assert found == CITATION_FIELDS | OTHER_FIELDS


def _inherited(model: type[BaseModel]) -> set[str]:
    return {name for base in model.__mro__[1:] for name in getattr(base, "model_fields", {})}


# --- every field is mapped ----------------------------------------------------------------------


def context(
    *, context_ids: Sequence[str] = CONTEXT_EVIDENCE, tool: bool = False
) -> RunContext[RunDeps]:
    """The run context check_evidence sees: context evidence and, with `tool`, one tool result
    with evidence (alias ev_1)."""
    messages: list[ModelMessage] = []
    if tool:
        record = {
            "tool_id": "create_ariel_search",
            "status": "ok",
            "evidence_id": TOOL_EVIDENCE,
            "evidence_alias": "ev_1",
        }
        part = ToolReturnPart("create_ariel_search", "rows", metadata=record)
        messages.append(ModelRequest(parts=[part]))
    deps = RunDeps(
        run_id="case-4711-verification-1",
        case_id="case-4711",
        hunt_id=None,
        time_window=TimeWindow(start=START, end=END),
        nonce=NONCE,
        context_evidence=tuple(context_ids),
    )
    return RunContext(deps=deps, model=TestModel(), usage=RunUsage(), messages=messages)


def claim(*evidence_ids: str) -> Claim:
    return Claim(text="svc_backup_7731 replicated the directory.", evidence_ids=list(evidence_ids))


def urgent(evidence_id: str) -> UrgentEvent:
    return UrgentEvent(
        rank=1,
        time=datetime(2026, 10, 2, 13, 5, tzinfo=UTC),
        log_source="DC-01",
        event_name="4662",
        reason="Replication by a user account.",
        checklist=[],
        evidence_id=evidence_id,
    )


def common(*evidence_ids: str) -> dict[str, object]:
    return {
        "task_id": "task-4711-2",
        "status": RunStatus.COMPLETED,
        "claims": [claim(*evidence_ids)],
        "data_gaps": [],
        "injection_suspected": False,
        "usage": Usage(tokens=1, tool_calls=0, seconds=1.0),
        "verdict": CaseVerdict.TP,
        "confidence": Confidence.HIGH,
    }


def investigation(alias: str) -> InvestigationResult:
    return InvestigationResult.model_validate(
        common("ev_c1")
        | {
            "ai_level": Level.HIGH,
            "timeline": [
                TimelineEntry(time=START, description="First replication.", evidence_ids=[alias])
            ],
            "hypotheses": [],
            "urgent_event_candidates": [urgent("ev_c2")],
        }
    )


def verification(alias: str) -> VerificationResult:
    return VerificationResult.model_validate(
        common("ev_c1")
        | {"agrees": True, "disagreements": [], "checked_evidence_ids": ["ev_c2", alias]}
    )


def report(alias: str) -> CaseReport:
    return CaseReport.model_validate(
        common("ev_c1")
        | {
            "summary_tr": "svc_backup_7731 dizini çoğalttı.",
            "notify_level": Level.HIGH,
            "urgent_events": [urgent(alias)],
            "recommendations": [
                Recommendation(
                    action_type=ActionType.RESET_CREDENTIALS_MANUAL,
                    target="svc_backup_7731",
                    rationale="Hesap DC değil.",
                    evidence_ids=["ev_c1", "ev_c2"],
                )
            ],
        }
    )


def aliases_in(model: BaseModel) -> list[str]:
    return [value for value in _strings(model.model_dump(mode="json")) if value.startswith("ev_c")]


def _strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def test_every_evidence_field_of_an_investigation_result_is_mapped() -> None:
    checked = check_evidence(context(), investigation("ev_c2"))

    assert aliases_in(checked) == []
    assert checked.claims[0].evidence_ids == [CONTEXT_EVIDENCE[0]]
    assert checked.timeline[0].evidence_ids == [CONTEXT_EVIDENCE[1]]
    assert checked.urgent_event_candidates[0].evidence_id == CONTEXT_EVIDENCE[1]


def test_every_evidence_field_of_a_verification_result_is_mapped() -> None:
    checked = check_evidence(context(tool=True), verification("ev_1"))

    assert aliases_in(checked) == []
    assert checked.checked_evidence_ids == [CONTEXT_EVIDENCE[1], TOOL_EVIDENCE]


def test_every_evidence_field_of_a_case_report_is_mapped() -> None:
    checked = check_evidence(context(), report("ev_c1"))

    assert aliases_in(checked) == []
    assert checked.urgent_events[0].evidence_id == CONTEXT_EVIDENCE[0]
    assert checked.recommendations[0].evidence_ids == list(CONTEXT_EVIDENCE)


def test_the_output_keeps_everything_but_the_citations() -> None:
    output = investigation("ev_c2")

    checked = check_evidence(context(), output)

    assert checked.model_dump(exclude={"claims", "timeline", "urgent_event_candidates"}) == (
        output.model_dump(exclude={"claims", "timeline", "urgent_event_candidates"})
    )
    assert checked.timeline[0].description == output.timeline[0].description
    assert output.claims[0].evidence_ids == ["ev_c1"]  # the model's output is not changed


@pytest.mark.parametrize(
    ("output", "noun"),
    [
        pytest.param(investigation("ev_c7"), "timeline entry", id="TimelineEntry.evidence_ids"),
        pytest.param(
            verification("ev_c7"), "ID from checked_evidence_ids", id="checked_evidence_ids"
        ),
        pytest.param(report("ev_c7"), "urgent event", id="UrgentEvent.evidence_id"),
        pytest.param(
            report("ev_c1").model_copy(
                update={
                    "recommendations": [
                        Recommendation(
                            action_type=ActionType.RESET_CREDENTIALS_MANUAL,
                            target="svc_backup_7731",
                            rationale="Hesap DC değil.",
                            evidence_ids=["ev_c7"],
                        )
                    ]
                }
            ),
            "recommendation",
            id="Recommendation.evidence_ids",
        ),
        pytest.param(
            investigation("ev_c1").model_copy(update={"claims": [claim("ev_c7")]}),
            "claim",
            id="Claim.evidence_ids",
        ),
    ],
)
def test_an_unknown_alias_in_any_evidence_field_is_rejected(output: BaseModel, noun: str) -> None:
    with pytest.raises(ModelRetry) as rejected:
        check_evidence(context(tool=True), output)

    message = rejected.value.message
    assert message == (
        "These evidence_ids are neither context evidence nor returned by your tool calls in this "
        f"run: ev_c7. You can cite only ev_1, ev_c1, ev_c2. Cite one of them or remove the {noun}."
    )
    for evidence_id in (*CONTEXT_EVIDENCE, TOOL_EVIDENCE):
        assert evidence_id not in message


def test_the_message_names_every_kind_of_rejected_citation() -> None:
    output = investigation("ev_9").model_copy(update={"claims": [claim("ev_c3")]})

    with pytest.raises(ModelRetry, match=r"ev_9, ev_c3\. .* remove the claim or timeline entry\."):
        check_evidence(context(), output)


# --- tool and context aliases in one run --------------------------------------------------------


def test_a_run_with_tools_cites_both_kinds_of_alias() -> None:
    script = ScriptedModel(
        call("get_offense", offense_id=4711),
        answer(summary_output("ev_9", "ev_c1")),
        answer(summary_output("ev_1", "ev_c2")),
    )
    toolset = build_gateway_toolset(TRIAGE_PROFILE, gateway(), agent_id="summary")

    run = run_summary(
        script.model,
        evidence=context_evidence(),
        manifest=summary_manifest(toolset_profile=TRIAGE_PROFILE.name),
        budget=Budget(tokens=60000, tool_calls=4, seconds=120),
        toolsets=[toolset],
    )

    [retry] = retry_prompts(run.messages)
    assert "You can cite only ev_1, ev_c1, ev_c2." in retry.model_response()
    assert run.status is RunStatus.COMPLETED
    assert run.result is not None
    assert run.result.claims[0].evidence_ids == [OFFENSE_EVIDENCE, CONTEXT_EVIDENCE[1]]


# --- shapes check_evidence does not cover -------------------------------------------------------


class ById(BaseModel):
    evidence: dict[str, EvidenceId]


class AsSet(BaseModel):
    evidence_ids: set[EvidenceId]


class Nested(BaseModel):
    groups: list[list[EvidenceId]]


class Wrapper(BaseModel):
    claims: list[Claim]
    inner: Annotated[list[ById], Field(max_length=3)]


class Covered(BaseModel):
    single: EvidenceId
    optional: EvidenceId | None = None
    many: tuple[EvidenceId, ...] = ()
    claims: dict[str, Claim] = {}


@pytest.mark.parametrize("output_type", [ById, AsSet, Nested, Wrapper], ids=lambda t: t.__name__)
def test_an_output_with_an_uncovered_evidence_field_stops_the_build(
    output_type: type[BaseModel],
) -> None:
    spec = replace(summary_spec(), output_type=output_type)

    with pytest.raises(TypeError, match="holds an EvidenceId in a shape check_evidence does not"):
        create_agent(spec, manifest=summary_manifest(), model=TestModel(), toolsets=[], aql=None)


def test_optional_tuple_and_dict_nested_evidence_are_covered() -> None:
    output = Covered(
        single="ev_c1", optional="ev_c2", many=("ev_c2", "ev_c1"), claims={"a": claim("ev_c1")}
    )

    checked = check_evidence(context(), output)

    assert evidence_fields(Covered) == {"single", "optional", "many"}
    assert checked == Covered(
        single=CONTEXT_EVIDENCE[0],
        optional=CONTEXT_EVIDENCE[1],
        many=(CONTEXT_EVIDENCE[1], CONTEXT_EVIDENCE[0]),
        claims={"a": claim(CONTEXT_EVIDENCE[0])},
    )
    assert check_evidence(context(), Covered(single="ev_c1")).optional is None
