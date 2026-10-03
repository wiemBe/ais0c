"""Field names, optionality and `extra="forbid"` against docs/impl/contracts.md."""

import inspect
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

import ais0c_contracts
from ais0c_contracts import (
    AgentResult,
    AgentTask,
    Backtest,
    Budget,
    CasePlan,
    CaseReport,
    CatalogContext,
    CatalogLogSource,
    CatalogRule,
    Claim,
    ContractModel,
    CoverageEntry,
    CriticalAssetHit,
    DataGap,
    DetectionProposal,
    Disagreement,
    EmailMessage,
    EnrichmentContext,
    EntityResolution,
    EvidenceRef,
    HuntReport,
    HuntRequest,
    HuntScope,
    HuntVersions,
    HypothesisResult,
    InvestigationHypothesis,
    InvestigationResult,
    IocHit,
    ModelRelease,
    NoteContent,
    OffenseSnapshot,
    OperatorFeedback,
    PlanStep,
    Recommendation,
    SkillRef,
    TimelineEntry,
    TimeWindow,
    ToolCoverage,
    ToolIntent,
    ToolResult,
    TriageResult,
    TuningProposal,
    UrgentEvent,
    Usage,
    VerificationResult,
)

from .payloads import VALID

# Field -> required, transcribed from the tables in docs/impl/contracts.md. Fields marked
# `?` there are optional (default None); case_id/hunt_id are optional individually and
# checked together.
REQ, OPT = True, False

AGENT_RESULT_FIELDS = {
    "task_id": REQ,
    "status": REQ,
    "claims": REQ,
    "data_gaps": REQ,
    "injection_suspected": REQ,
    "usage": REQ,
}

DOC_FIELDS: dict[type[ContractModel], dict[str, bool]] = {
    EvidenceRef: {
        "evidence_id": REQ,
        "source": REQ,
        "query_hash": REQ,
        "query_text": REQ,
        "time_start": REQ,
        "time_end": REQ,
        "identifiers": REQ,
        "excerpt": REQ,
        "retrieved_at": REQ,
    },
    Claim: {"text": REQ, "evidence_ids": REQ},
    DataGap: {"source": REQ, "period_start": REQ, "period_end": REQ, "reason": REQ},
    Recommendation: {"action_type": REQ, "target": REQ, "rationale": REQ, "evidence_ids": REQ},
    UrgentEvent: {
        "rank": REQ,
        "time": REQ,
        "log_source": REQ,
        "event_name": REQ,
        "qid": OPT,
        "source": OPT,
        "destination": OPT,
        "username": OPT,
        "reason": REQ,
        "checklist": REQ,
        "aql": OPT,
        "evidence_id": REQ,
    },
    OffenseSnapshot: {
        "offense_id": REQ,
        "description": REQ,
        "offense_type": REQ,
        "offense_source": REQ,
        "rule_ids": REQ,
        "rule_names": REQ,
        "categories": REQ,
        "magnitude": REQ,
        "start_time": REQ,
        "last_updated_time": REQ,
        "event_count": REQ,
        "log_source_ids": REQ,
        "source_ips": REQ,
        "destination_ips": REQ,
        "usernames": REQ,
    },
    CatalogContext: {"rules": REQ, "log_sources": REQ},
    CatalogRule: {"rule_id": REQ, "mode": REQ, "min_level": OPT, "context_note": OPT},
    CatalogLogSource: {
        "log_source_id": REQ,
        "description": OPT,
        "criticality": OPT,
        "context_note": OPT,
    },
    EnrichmentContext: {
        "catalog": REQ,
        "critical_asset_hits": REQ,
        "ioc_hits": REQ,
        "entity_resolutions": REQ,
        "group_id": OPT,
        "floor_level": OPT,
    },
    CriticalAssetHit: {"value": REQ, "label": REQ, "level": REQ},
    IocHit: {"value": REQ, "type": REQ, "source": REQ, "confidence": REQ},
    EntityResolution: {"ip": REQ, "time": REQ, "host": OPT, "user": OPT},
    AgentTask: {
        "task_id": REQ,
        "parent_run_id": REQ,
        "case_id": OPT,
        "hunt_id": OPT,
        "agent_id": REQ,
        "agent_version": REQ,
        "objective": REQ,
        "context_refs": REQ,
        "time_window": REQ,
        "budget": REQ,
    },
    TimeWindow: {"start": REQ, "end": REQ},
    Budget: {"tokens": REQ, "tool_calls": REQ, "seconds": REQ},
    Usage: {"tokens": REQ, "tool_calls": REQ, "seconds": REQ},
    AgentResult: AGENT_RESULT_FIELDS,
    TriageResult: AGENT_RESULT_FIELDS
    | {
        "verdict": REQ,
        "confidence": REQ,
        "ai_level": REQ,
        "rationale": REQ,
        "needs_investigation": REQ,
        "investigation_focus": REQ,
    },
    CasePlan: AGENT_RESULT_FIELDS | {"steps": REQ},
    PlanStep: {
        "agent_id": REQ,
        "skill_id": OPT,
        "skill_version": OPT,
        "objective": REQ,
        "time_window": REQ,
        "budget": REQ,
    },
    InvestigationResult: AGENT_RESULT_FIELDS
    | {
        "verdict": REQ,
        "confidence": REQ,
        "ai_level": REQ,
        "timeline": REQ,
        "hypotheses": REQ,
        "urgent_event_candidates": REQ,
    },
    TimelineEntry: {"time": REQ, "description": REQ, "evidence_ids": REQ},
    InvestigationHypothesis: {"text": REQ, "status": REQ},
    VerificationResult: AGENT_RESULT_FIELDS
    | {
        "agrees": REQ,
        "verdict": REQ,
        "confidence": REQ,
        "disagreements": REQ,
        "checked_evidence_ids": REQ,
    },
    Disagreement: {"claim_text": REQ, "reason": REQ},
    CaseReport: AGENT_RESULT_FIELDS
    | {
        "summary_tr": REQ,
        "verdict": REQ,
        "confidence": REQ,
        "notify_level": REQ,
        "urgent_events": REQ,
        "recommendations": REQ,
        "data_gaps": REQ,
    },
    NoteContent: {
        "offense_id": REQ,
        "evaluation_no": REQ,
        "run_marker": REQ,
        "verdict": REQ,
        "confidence": REQ,
        "notify_level": REQ,
        "summary_tr": REQ,
        "urgent_events": REQ,
        "recommended_actions": REQ,
        "data_gaps": REQ,
        "case_url": REQ,
        "group_id": OPT,
    },
    EmailMessage: {
        "kind": REQ,
        "recipients": REQ,
        "subject": REQ,
        "template_id": REQ,
        "fields": REQ,
        "attachments": REQ,
        "idempotency_key": REQ,
    },
    ToolIntent: {
        "run_id": REQ,
        "case_id": OPT,
        "hunt_id": OPT,
        "agent_id": REQ,
        "toolset_profile": REQ,
        "tool_id": REQ,
        "tool_schema_version": REQ,
        "arguments": REQ,
        "reason": REQ,
        "hypothesis_id": OPT,
        "expected_evidence": REQ,
        "time_window": REQ,
        "cost_class": REQ,
    },
    ToolResult: {
        "status": REQ,
        "deny_reason": OPT,
        "evidence_id": OPT,
        "data": REQ,
        "truncated": REQ,
        "coverage": REQ,
    },
    ToolCoverage: {"complete": REQ, "gaps": REQ},
    ModelRelease: {
        "alias": REQ,
        "target": REQ,
        "artifact": REQ,
        "artifact_hash": OPT,
        "quantization": OPT,
        "tokenizer": OPT,
        "engine_version": OPT,
        "tool_parser": OPT,
        "max_context": OPT,
        "inference_params": REQ,
    },
    SkillRef: {"skill_id": REQ, "version": REQ, "content_hash": REQ},
    HuntRequest: {
        "pack_id": REQ,
        "pack_version": REQ,
        "hypothesis_ids": OPT,
        "window_start": REQ,
        "window_end": REQ,
        "scope": REQ,
        "trigger": REQ,
        "requested_by": REQ,
    },
    HuntScope: {"kind": REQ, "values": REQ},
    HuntReport: {
        "hunt_id": REQ,
        "summary_tr": REQ,
        "outcome": REQ,
        "hypotheses": REQ,
        "coverage": REQ,
        "findings": REQ,
        "detection_proposals": REQ,
        "versions": REQ,
    },
    HypothesisResult: {
        "hypothesis_id": REQ,
        "outcome": REQ,
        "rationale_tr": REQ,
        "claims": REQ,
        "data_gaps": REQ,
    },
    CoverageEntry: {"month": REQ, "log_source_type": REQ, "status": REQ},
    DetectionProposal: {"title": REQ, "sigma_yaml": REQ},
    HuntVersions: {"pack": REQ, "prompts": REQ, "models": REQ},
    TuningProposal: {
        "cluster_id": REQ,
        "rule_id": REQ,
        "change_type": REQ,
        "description_tr": REQ,
        "rationale": REQ,
        "backtest": REQ,
        "risk_flag": REQ,
    },
    Backtest: {"days": REQ, "suppressed_offenses": REQ, "suppressed_tp_offenses": REQ},
    OperatorFeedback: {"case_id": REQ, "verdict": REQ, "reason": REQ, "comment": OPT},
}

MODELS = sorted(VALID, key=lambda model: model.__name__)


def _contract_models() -> set[type[ContractModel]]:
    found: set[type[ContractModel]] = set()
    pending = [ContractModel]
    while pending:
        for subclass in pending.pop().__subclasses__():
            found.add(subclass)
            pending.append(subclass)
    return found


def test_every_model_is_exported_and_covered() -> None:
    models = _contract_models()
    exported = {getattr(ais0c_contracts, name) for name in ais0c_contracts.__all__}
    assert models <= exported
    assert models == set(DOC_FIELDS) == set(VALID)


@pytest.mark.parametrize("model", MODELS, ids=lambda model: model.__name__)
def test_fields_match_doc(model: type[ContractModel]) -> None:
    expected = DOC_FIELDS[model]
    assert set(model.model_fields) == set(expected)
    for name, field in model.model_fields.items():
        assert field.is_required() is expected[name], name
        if not expected[name]:
            assert field.default is None, name


@pytest.mark.parametrize("model", MODELS, ids=lambda model: model.__name__)
def test_valid_payload_round_trips(model: type[ContractModel]) -> None:
    instance = model.model_validate(VALID[model]())
    assert model.model_validate_json(instance.model_dump_json()) == instance


@pytest.mark.parametrize("model", MODELS, ids=lambda model: model.__name__)
def test_unknown_field_is_rejected(model: type[ContractModel]) -> None:
    with pytest.raises(ValidationError) as excinfo:
        model.model_validate(VALID[model]() | {"unexpected": "x"})
    assert [error["type"] for error in excinfo.value.errors()] == ["extra_forbidden"]


def test_unknown_field_in_nested_model_is_rejected() -> None:
    payload = VALID[AgentTask]()
    payload["budget"] = {"tokens": 1, "tool_calls": 1, "seconds": 1, "cost": 5}
    with pytest.raises(ValidationError, match="extra_forbidden"):
        AgentTask.model_validate(payload)


def test_level_and_verdict_enums_reject_unknown_values() -> None:
    with pytest.raises(ValidationError, match="enum"):
        TriageResult.model_validate(VALID[TriageResult]() | {"ai_level": "severe"})
    with pytest.raises(ValidationError, match="enum"):
        TriageResult.model_validate(VALID[TriageResult]() | {"verdict": "supported"})


def test_inference_params_keep_their_json_types() -> None:
    params = {"temperature": 0.2, "max_tokens": 4096, "stream": False, "seed": "x"}
    payload = VALID[ModelRelease]() | {"inference_params": params}
    for release in (
        ModelRelease.model_validate(payload),
        ModelRelease.model_validate_json(ModelRelease.model_validate(payload).model_dump_json()),
    ):
        assert release.inference_params == params
        assert [type(value) for value in release.inference_params.values()] == [
            float,
            int,
            bool,
            str,
        ]


@pytest.mark.parametrize("value", [None, [0.2], {"value": 0.2}])
def test_inference_params_hold_only_scalars(value: object) -> None:
    payload = VALID[ModelRelease]() | {"inference_params": {"temperature": value}}
    with pytest.raises(ValidationError):
        ModelRelease.model_validate(payload)


def test_package_source_does_not_use_any() -> None:
    source_dir = Path(inspect.getfile(ais0c_contracts)).parent
    offenders = [
        path.name
        for path in source_dir.glob("*.py")
        if re.search(r"\bAny\b", path.read_text(encoding="utf-8"))
    ]
    assert offenders == []
