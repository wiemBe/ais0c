"""Length limits, list limits, timezones, evidence IDs and content hashes."""

import re
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from ais0c_contracts import (
    RUN_ID_MAX_LENGTH,
    RUN_ID_PATTERN,
    SHORT_TEXT_MAX_LENGTH,
    SUMMARY_MAX_LENGTH,
    AgentTask,
    CasePlan,
    CaseReport,
    CatalogLogSource,
    CatalogRule,
    Claim,
    ContractModel,
    CriticalAssetHit,
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
    SkillRef,
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
    (CatalogLogSource, "type_name", 255, _single),
    (CatalogLogSource, "description", SHORT, _single),
    (CatalogLogSource, "context_note", SUMMARY, _single),
    (CriticalAssetHit, "label", SHORT, _single),
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


def _techniques(count: int) -> list[object]:
    return [f"T{1000 + index}" for index in range(count)]


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
    (CatalogRule, "attack_techniques", 0, 20, _techniques),
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


def test_tool_intent_needs_a_run_id() -> None:
    missing = payloads.tool_intent()
    del missing["run_id"]
    with pytest.raises(ValidationError) as excinfo:
        ToolIntent.model_validate(missing)
    assert [error["type"] for error in excinfo.value.errors()] == ["missing"]
    for empty in ("", None):
        with pytest.raises(ValidationError) as excinfo:
            ToolIntent.model_validate(payloads.tool_intent() | {"run_id": empty})
        assert len(excinfo.value.errors()) == 1


# The forms of the run IDs the platform issues (v0.4, T-37). services/mcp-gateway checks the
# same against the functions that make them.
PLATFORM_RUN_IDS = [
    # Triage runs: `<case_id>-triage-<n>`, and the retry after a model outage (D-33).
    "case-27-triage-1",
    "case-27-triage-1-retry",
    "case-12345-triage-99999",
    # The case of a group, and of a hunt finding.
    "group-G-3f2a9c1b2d4e-20261005T101500Z-triage-1",
    "case-hunt-hunt-ornek-grup-2026-01-01-2026-03-31-3f2a9c1b-1-triage-1",
    # Pseudo agent runs of platform code (intake, executor, catalog sync): UUIDv7 strings.
    "01999c3e-8f1a-7b2c-9d3e-4f5a6b7c8d9e",
    # Hunts: `hunt-<pack>-<start>-<end>-<scope hash>`, the times with or without a clock.
    "hunt-ornek-grup-2026-01-01-2026-03-31-3f2a9c1b",
    "hunt-ornek_grup-2026-01-01T00:00:00Z-2026-03-31T23:59:59.999Z-3f2a9c1b",
    "r",
    "R" * RUN_ID_MAX_LENGTH,
]


@pytest.mark.parametrize("run_id", PLATFORM_RUN_IDS)
def test_tool_intent_accepts_every_run_id_form_the_platform_issues(run_id: str) -> None:
    assert ToolIntent.model_validate(payloads.tool_intent() | {"run_id": run_id}).run_id == run_id
    assert re.fullmatch(RUN_ID_PATTERN, run_id)


@pytest.mark.parametrize(
    ("run_id", "error"),
    [
        ("", "string_too_short"),
        ("r" * (RUN_ID_MAX_LENGTH + 1), "string_too_long"),
        ("case 27-triage-1", "string_pattern_mismatch"),
        ("case-27-triage-1 ", "string_pattern_mismatch"),
        (" case-27-triage-1", "string_pattern_mismatch"),
        ("case-27\x00-triage-1", "string_pattern_mismatch"),
        ("case-27-triage-1\x00", "string_pattern_mismatch"),
        ("case-27/triage-1", "string_pattern_mismatch"),
        ("../agent_runs", "string_pattern_mismatch"),
        (".case-27-triage-1", "string_pattern_mismatch"),
        ("-case-27-triage-1", "string_pattern_mismatch"),
        ("_case-27-triage-1", "string_pattern_mismatch"),
        (":case-27-triage-1", "string_pattern_mismatch"),
        ("case-27-triage-1\n", "string_pattern_mismatch"),
        ("case-27-triage-1\r\nX-Ais0c-Run-Id: run-2", "string_pattern_mismatch"),
        ("case-27\ttriage-1", "string_pattern_mismatch"),
        ("case-27-triage-1;DROP TABLE agent_runs", "string_pattern_mismatch"),
        ("case-27-triage-1'", "string_pattern_mismatch"),
        ("vaka-\u015f", "string_pattern_mismatch"),
        ("case-\uff12\uff17", "string_pattern_mismatch"),
        ("case-27-triage-1\u200b", "string_pattern_mismatch"),
    ],
    ids=[
        "empty",
        "201 characters",
        "space",
        "trailing space",
        "leading space",
        "NUL",
        "trailing NUL",
        "slash",
        "path",
        "dot first",
        "hyphen first",
        "underscore first",
        "colon first",
        "trailing newline",
        "header injection",
        "tab",
        "semicolon",
        "quote",
        "non-ASCII letter",
        "full-width digits",
        "zero-width space",
    ],
)
def test_tool_intent_rejects_a_run_id_of_another_form(run_id: str, error: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        ToolIntent.model_validate(payloads.tool_intent() | {"run_id": run_id})
    assert [item["type"] for item in excinfo.value.errors()] == [error]
    assert [item["loc"] for item in excinfo.value.errors()] == [("run_id",)]
    assert not re.fullmatch(RUN_ID_PATTERN, run_id)


def test_the_run_id_pattern_is_in_the_json_schema() -> None:
    schema = ToolIntent.model_json_schema()["properties"]["run_id"]
    assert (schema["pattern"], schema["minLength"], schema["maxLength"]) == (
        RUN_ID_PATTERN,
        1,
        RUN_ID_MAX_LENGTH,
    )
    assert RUN_ID_MAX_LENGTH == 200


@pytest.mark.parametrize(
    "content_hash",
    [
        "sha256:" + "0123456789abcdef" * 4,
        "sha256:" + "f" * 64,
    ],
)
def test_skill_content_hash_accepts_sha256_hex(content_hash: str) -> None:
    SkillRef.model_validate(payloads.skill_ref() | {"content_hash": content_hash})


@pytest.mark.parametrize(
    "content_hash",
    [
        "",
        "sha256:",
        "0123456789abcdef" * 4,  # no prefix
        "SHA256:" + "0123456789abcdef" * 4,
        "sha512:" + "0123456789abcdef" * 4,
        "sha256:" + "0123456789ABCDEF" * 4,  # upper case
        "sha256:" + "f" * 63,
        "sha256:" + "f" * 65,
        "sha256:" + "f" * 63 + "g",
        "sha256: " + "f" * 64,
        "sha256:" + "f" * 64 + "\n",
        " sha256:" + "f" * 64,
    ],
)
def test_skill_content_hash_rejects_anything_else(content_hash: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        SkillRef.model_validate(payloads.skill_ref() | {"content_hash": content_hash})
    assert [error["type"] for error in excinfo.value.errors()] == ["string_pattern_mismatch"]


@pytest.mark.parametrize("technique", ["T1003", "T1003.006", "T0001", "T9999.999"])
def test_attack_technique_accepts_technique_and_sub_technique_ids(technique: str) -> None:
    CatalogRule.model_validate(payloads.catalog_rule() | {"attack_techniques": [technique]})


@pytest.mark.parametrize(
    "technique",
    [
        "",
        "T",
        "t1003",
        "T103",
        "T10030",
        "T1003.6",
        "T1003.0060",
        "T1003.",
        "TA0006",  # a tactic
        "1003",
        " T1003",
        "T1003 ",
        "T1003.006\n",
        "T1003,T1078",
    ],
)
def test_attack_technique_rejects_anything_else(technique: str) -> None:
    with pytest.raises(ValidationError) as excinfo:
        CatalogRule.model_validate(payloads.catalog_rule() | {"attack_techniques": [technique]})
    assert [error["type"] for error in excinfo.value.errors()] == ["string_pattern_mismatch"]


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
