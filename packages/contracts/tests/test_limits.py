"""Length limits, list limits, timezones and evidence IDs."""

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ais0c_contracts import (
    SHORT_TEXT_MAX_LENGTH,
    SUMMARY_MAX_LENGTH,
    AgentTask,
    CasePlan,
    CaseReport,
    CatalogLogSource,
    CatalogRule,
    Claim,
    ContractModel,
    Disagreement,
    EmailMessage,
    EvidenceRef,
    HuntReport,
    HypothesisResult,
    InvestigationHypothesis,
    InvestigationResult,
    NoteContent,
    OffenseSnapshot,
    OperatorFeedback,
    PlanStep,
    Recommendation,
    TimelineEntry,
    TimeWindow,
    ToolIntent,
    ToolResult,
    TriageResult,
    TuningProposal,
    UrgentEvent,
    VerificationResult,
)

from . import payloads
from .payloads import VALID

SHORT, SUMMARY = SHORT_TEXT_MAX_LENGTH, SUMMARY_MAX_LENGTH


def _single(value: str) -> object:
    return value


def _in_list(value: str) -> object:
    return [value]


# (model, field, max length, how the string goes into the field)
TEXT_LIMITS: list[tuple[type[ContractModel], str, int, Callable[[str], object]]] = [
    (EvidenceRef, "query_text", 4000, _single),
    (EvidenceRef, "excerpt", 500, _single),
    (Claim, "text", SHORT, _single),
    (Recommendation, "target", 200, _single),
    (Recommendation, "rationale", SHORT, _single),
    (UrgentEvent, "log_source", 120, _single),
    (UrgentEvent, "event_name", 200, _single),
    (UrgentEvent, "source", 100, _single),
    (UrgentEvent, "destination", 100, _single),
    (UrgentEvent, "username", 100, _single),
    (UrgentEvent, "reason", SHORT, _single),
    (UrgentEvent, "checklist", SHORT, _in_list),
    (UrgentEvent, "aql", 2000, _single),
    (OffenseSnapshot, "description", 500, _single),
    (CatalogRule, "context_note", SUMMARY, _single),
    (CatalogLogSource, "description", SHORT, _single),
    (CatalogLogSource, "context_note", SUMMARY, _single),
    (AgentTask, "objective", SHORT, _single),
    (TriageResult, "rationale", SUMMARY, _single),
    (TriageResult, "investigation_focus", SHORT, _in_list),
    (PlanStep, "objective", SHORT, _single),
    (TimelineEntry, "description", 200, _single),
    (InvestigationHypothesis, "text", SHORT, _single),
    (Disagreement, "claim_text", SHORT, _single),
    (Disagreement, "reason", SHORT, _single),
    (CaseReport, "summary_tr", SUMMARY, _single),
    (NoteContent, "summary_tr", 400, _single),
    (EmailMessage, "subject", 150, _single),
    (ToolIntent, "reason", SHORT, _single),
    (ToolIntent, "expected_evidence", SHORT, _single),
    (ToolResult, "deny_reason", SHORT, _single),
    (HypothesisResult, "rationale_tr", SUMMARY, _single),
    (HuntReport, "summary_tr", SUMMARY, _single),
    (TuningProposal, "description_tr", SUMMARY, _single),
    (TuningProposal, "rationale", SHORT, _single),
    (OperatorFeedback, "comment", 500, _single),
]


@pytest.mark.parametrize(
    ("model", "field", "limit", "wrap"),
    TEXT_LIMITS,
    ids=[f"{model.__name__}.{field}" for model, field, _, _ in TEXT_LIMITS],
)
def test_text_limit(
    model: type[ContractModel], field: str, limit: int, wrap: Callable[[str], object]
) -> None:
    # Limits count characters, not bytes: "ş" is two bytes in UTF-8.
    model.model_validate(VALID[model]() | {field: wrap("ş" * limit)})
    with pytest.raises(ValidationError) as excinfo:
        model.model_validate(VALID[model]() | {field: wrap("ş" * (limit + 1))})
    assert [error["type"] for error in excinfo.value.errors()] == ["string_too_long"]


def _urgent_events(count: int) -> list[object]:
    return [payloads.urgent_event(rank) for rank in range(1, count + 1)]


def _texts(count: int) -> list[object]:
    return [f"item {index}" for index in range(count)]


# (model, field, min items, max items or None, item list factory)
LIST_LIMITS: list[
    tuple[type[ContractModel], str, int, int | None, Callable[[int], list[object]]]
] = [
    (UrgentEvent, "checklist", 0, 5, _texts),
    (TriageResult, "investigation_focus", 0, 5, _texts),
    (CasePlan, "steps", 1, 4, lambda count: [payloads.plan_step() for _ in range(count)]),
    (
        InvestigationResult,
        "timeline",
        0,
        30,
        lambda count: [payloads.timeline_entry() for _ in range(count)],
    ),
    (
        InvestigationResult,
        "hypotheses",
        0,
        5,
        lambda count: [payloads.investigation_hypothesis() for _ in range(count)],
    ),
    (InvestigationResult, "urgent_event_candidates", 0, 15, _urgent_events),
    (CaseReport, "urgent_events", 0, 15, _urgent_events),
    (
        CaseReport,
        "recommendations",
        0,
        8,
        lambda count: [payloads.recommendation() for _ in range(count)],
    ),
    (NoteContent, "urgent_events", 0, 5, _urgent_events),
    (OffenseSnapshot, "source_ips", 0, 50, _texts),
    (OffenseSnapshot, "destination_ips", 0, 50, _texts),
    (OffenseSnapshot, "usernames", 0, 50, _texts),
    (Claim, "evidence_ids", 1, None, lambda count: [f"ev_{n}" for n in range(count)]),
]


@pytest.mark.parametrize(
    ("model", "field", "minimum", "maximum", "items"),
    LIST_LIMITS,
    ids=[f"{model.__name__}.{field}" for model, field, _, _, _ in LIST_LIMITS],
)
def test_list_limit(
    model: type[ContractModel],
    field: str,
    minimum: int,
    maximum: int | None,
    items: Callable[[int], list[object]],
) -> None:
    model.model_validate(VALID[model]() | {field: items(minimum)})
    if maximum is not None:
        model.model_validate(VALID[model]() | {field: items(maximum)})
        with pytest.raises(ValidationError) as excinfo:
            model.model_validate(VALID[model]() | {field: items(maximum + 1)})
        assert [error["type"] for error in excinfo.value.errors()] == ["too_long"]
    if minimum > 0:
        with pytest.raises(ValidationError) as excinfo:
            model.model_validate(VALID[model]() | {field: items(minimum - 1)})
        assert [error["type"] for error in excinfo.value.errors()] == ["too_short"]


def test_claim_without_evidence_is_rejected() -> None:
    with pytest.raises(ValidationError) as excinfo:
        Claim.model_validate(payloads.claim() | {"evidence_ids": []})
    assert [error["type"] for error in excinfo.value.errors()] == ["too_short"]


def test_claim_without_evidence_is_rejected_inside_a_result() -> None:
    payload = payloads.triage_result()
    payload["claims"] = [payloads.claim() | {"evidence_ids": []}]
    with pytest.raises(ValidationError, match="too_short"):
        TriageResult.model_validate(payload)


@pytest.mark.parametrize(
    "evidence_id", ["", "ev_", "01JB3K7Q9X", "EV_01JB", "evidence_01", "ev_01 02", "ev_01\n"]
)
def test_malformed_evidence_id_is_rejected(evidence_id: str) -> None:
    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        Claim.model_validate(payloads.claim() | {"evidence_ids": [evidence_id]})


EVIDENCE_ID_FIELDS: list[tuple[type[ContractModel], str, Callable[[str], object]]] = [
    (EvidenceRef, "evidence_id", _single),
    (Claim, "evidence_ids", _in_list),
    (Recommendation, "evidence_ids", _in_list),
    (UrgentEvent, "evidence_id", _single),
    (AgentTask, "context_refs", _in_list),
    (TimelineEntry, "evidence_ids", _in_list),
    (VerificationResult, "checked_evidence_ids", _in_list),
    (ToolResult, "evidence_id", _single),
]


@pytest.mark.parametrize(
    ("model", "field", "wrap"),
    EVIDENCE_ID_FIELDS,
    ids=[f"{model.__name__}.{field}" for model, field, _ in EVIDENCE_ID_FIELDS],
)
def test_evidence_id_fields_require_gateway_prefix(
    model: type[ContractModel], field: str, wrap: Callable[[str], object]
) -> None:
    with pytest.raises(ValidationError, match="string_pattern_mismatch"):
        model.model_validate(VALID[model]() | {field: wrap("01JB3K7Q9X")})


def _datetime_fields() -> list[tuple[type[ContractModel], str]]:
    """Every top-level field whose JSON Schema is a date-time string."""
    found: list[tuple[type[ContractModel], str]] = []
    for model in sorted(VALID, key=lambda model: model.__name__):
        properties: dict[str, dict[str, object]] = model.model_json_schema()["properties"]
        found += [
            (model, name)
            for name, schema in properties.items()
            if schema.get("format") == "date-time"
        ]
    return found


DATETIME_FIELDS = _datetime_fields()


def test_datetime_fields_are_found() -> None:
    assert {(model.__name__, field) for model, field in DATETIME_FIELDS} == {
        ("DataGap", "period_end"),
        ("DataGap", "period_start"),
        ("EntityResolution", "time"),
        ("EvidenceRef", "retrieved_at"),
        ("EvidenceRef", "time_end"),
        ("EvidenceRef", "time_start"),
        ("HuntRequest", "window_end"),
        ("HuntRequest", "window_start"),
        ("OffenseSnapshot", "last_updated_time"),
        ("OffenseSnapshot", "start_time"),
        ("TimeWindow", "end"),
        ("TimeWindow", "start"),
        ("TimelineEntry", "time"),
        ("UrgentEvent", "time"),
    }


@pytest.mark.parametrize(
    ("model", "field"),
    DATETIME_FIELDS,
    ids=[f"{model.__name__}.{field}" for model, field in DATETIME_FIELDS],
)
def test_naive_datetime_is_rejected(model: type[ContractModel], field: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        model.model_validate(VALID[model]() | {field: "2026-10-02T10:00:00"})
    assert "timezone_aware" in [error["type"] for error in excinfo.value.errors()]


def test_naive_datetime_object_is_rejected() -> None:
    naive = datetime(2026, 10, 2, 10, 0)  # noqa: DTZ001 - the point of the test
    with pytest.raises(ValidationError, match="timezone_aware"):
        TimeWindow(start=naive, end=naive)


def test_aware_datetime_is_stored_in_utc() -> None:
    window = TimeWindow.model_validate(
        {"start": "2026-10-02T13:00:00+03:00", "end": "2026-10-02T14:00:00+03:00"}
    )
    assert window.start == datetime(2026, 10, 2, 10, 0, tzinfo=UTC)
    assert window.start.tzinfo is UTC
