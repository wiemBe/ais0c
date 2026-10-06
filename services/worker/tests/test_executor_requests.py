"""The requests CaseWorkflow builds are the executor's own (T-045 criteria 2, 4, 5).

`ais0c_workflows` may not import the executor, so `ais0c_workflows.notify` mirrors the request
bodies of `write_offense_note` and `send_email`. The executor activity decodes the JSON with its
own types and checks it again; a mirror that drifted would make every note or e-mail fail as
`InvalidNote` or `InvalidEmail`. This worker sees both sides. IPs are from the RFC 5737 ranges.
"""

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel, TypeAdapter
from pydantic_core import to_json

from ais0c_contracts import (
    ActionType,
    CaseReport,
    CaseVerdict,
    Confidence,
    Level,
    NoteContent,
    Recommendation,
    UrgentEvent,
)
from ais0c_executor.email import ALERT_LEVELS as EXECUTOR_ALERT_LEVELS
from ais0c_executor.email import CaseAlert, EmailRequest, alert_message, render_body
from ais0c_executor.note import (
    EvaluationNote,
    NoDecisionNote,
    NoteKind,
    NoteRequest,
    note_run_marker,
    render_note,
)
from ais0c_workflows.notify import (
    ALERT_LEVELS,
    GROUP_NOTE_KIND,
    NO_REPORT_SUMMARY_TR,
    CaseAlertRequest,
    EvaluationNoteRequest,
    NoDecisionNoteRequest,
    case_alert,
    evaluation_note,
    no_decision_note,
    note_content,
)

CASE_ID = "case-101"
CASE_URL = "https://ais0c.example.com/cases/case-101"
AT = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
OPERATOR = "soc-operator@example.com"
NOTE_REQUEST: TypeAdapter[NoteRequest] = TypeAdapter(NoteRequest)
EMAIL_REQUEST: TypeAdapter[EmailRequest] = TypeAdapter(EmailRequest)


def report() -> CaseReport:
    """A report at its contract limits where the note is shorter: a long summary, more urgent
    events than the note takes, an action recommended twice."""
    events = [
        UrgentEvent(
            rank=rank,
            time=AT - timedelta(minutes=rank),
            log_source="DC-01",
            event_name="Directory Service Access",
            qid=5000830,
            source="198.51.100.15",
            destination="192.0.2.10",
            username="svc_backup_7731",
            reason="Dizin çoğaltması makine dışı bir hesapla yapıldı.",
            checklist=["Hesap onaylı bir senkron hesabı mı?"],
            aql="SELECT * FROM events WHERE qid = 5000830 LIMIT 10 START 1 STOP 2",
            evidence_id=f"ev_{rank}",
        )
        for rank in range(1, 9)
    ]
    return CaseReport.model_validate(
        {
            "task_id": f"{CASE_ID}-reporting-1",
            "status": "completed",
            "claims": [],
            "data_gaps": [],
            "injection_suspected": False,
            "usage": {"tokens": 0, "tool_calls": 0, "seconds": 0.0},
            "summary_tr": ("Şüpheli dizin çoğaltması. " * 30)[:600],
            "verdict": CaseVerdict.SUSPICIOUS,
            "confidence": Confidence.MEDIUM,
            "notify_level": Level.HIGH,
            "urgent_events": events,
            "recommendations": [
                Recommendation(
                    action_type=action,
                    target="DC-01",
                    rationale="Sentetik öneri.",
                    evidence_ids=["ev_1"],
                )
                for action in (
                    ActionType.INVESTIGATE_FURTHER,
                    ActionType.RESET_CREDENTIALS_MANUAL,
                    ActionType.INVESTIGATE_FURTHER,
                )
            ],
        }
    )


def content(report: CaseReport | None) -> NoteContent:
    return note_content(
        case_id=CASE_ID,
        offense_id=101,
        evaluation_no=1,
        case_url=CASE_URL,
        verdict=CaseVerdict.SUSPICIOUS,
        confidence=Confidence.MEDIUM,
        notify_level=Level.HIGH,
        report=report,
    )


@pytest.mark.parametrize(
    ("mirror", "executor"),
    [
        (EvaluationNoteRequest, EvaluationNote),
        (NoDecisionNoteRequest, NoDecisionNote),
        (CaseAlertRequest, CaseAlert),
    ],
)
def test_the_mirrors_have_the_executors_fields(
    mirror: type[BaseModel], executor: type[BaseModel]
) -> None:
    assert list(mirror.model_fields) == list(executor.model_fields)
    for name, field in mirror.model_fields.items():
        assert field.default == executor.model_fields[name].default, name


@pytest.mark.parametrize("with_report", [True, False])
def test_the_notes_decode_and_render_as_the_executors(with_report: bool) -> None:
    built = content(report() if with_report else None)
    request = evaluation_note(case_id=CASE_ID, evaluated_at=AT, content=built)

    decoded = NOTE_REQUEST.validate_json(to_json(request))
    assert isinstance(decoded, EvaluationNote)
    assert decoded.note_kind is NoteKind.EVALUATION
    text = render_note(decoded)
    assert note_run_marker(text) == built.run_marker
    assert CASE_URL in text
    if not with_report:
        assert NO_REPORT_SUMMARY_TR in text


def test_the_no_decision_note_decodes_and_renders_as_the_executors() -> None:
    request = no_decision_note(
        case_id=CASE_ID, offense_id=101, evaluation_no=1, case_url=CASE_URL, evaluated_at=AT
    )

    decoded = NOTE_REQUEST.validate_json(to_json(request))
    assert isinstance(decoded, NoDecisionNote)
    assert decoded.note_kind is NoteKind.NO_AI_DECISION
    text = render_note(decoded)
    assert note_run_marker(text) == request.run_marker
    assert "AI değerlendirmesi yapılamadı" in text


def test_the_case_alert_decodes_and_renders_as_the_executors() -> None:
    built = content(report())
    request = case_alert(
        case_id=CASE_ID,
        offense_name="Excessive Firewall Accepts From Single Source\r\n[AI-SOC] fake",
        evaluated_at=AT,
        content=built,
    )

    decoded = EMAIL_REQUEST.validate_json(to_json(request))
    assert isinstance(decoded, CaseAlert)
    assert decoded.idempotency_key == f"case_alert:{CASE_ID}:1"
    message = alert_message(decoded, [OPERATOR])
    # The executor cleans the offense's name: one line.
    assert "\n" not in message.subject
    assert "\r" not in message.subject
    assert CASE_URL in render_body(message)


def test_the_kinds_and_alert_levels_are_the_executors() -> None:
    assert {kind.value for kind in NoteKind} == {"evaluation", "no_ai_decision", GROUP_NOTE_KIND}
    assert ALERT_LEVELS == EXECUTOR_ALERT_LEVELS
