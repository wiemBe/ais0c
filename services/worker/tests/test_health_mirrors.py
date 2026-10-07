"""The health alarm and given-up call models the workflows spell are the activities' own
(T-032).

`ais0c_workflows` may not import the executor or the activities, so `ais0c_workflows.notify`
mirrors `ais0c_executor.email.HealthAlarm` (the e-mail and syslog request) and
`ais0c_activities.AbandonedCall`. The executor and the activities decode the JSON the workflow
sends with their own types; a mirror that drifted would fail every alarm or every record. This
worker sees all sides. The alarm kinds also have to be the ones the table of alarms holds.
"""

import typing
from datetime import UTC, datetime

import pytest
from pydantic import TypeAdapter
from pydantic_core import to_json

from ais0c_activities import AbandonedCall as ActivityAbandonedCall
from ais0c_contracts import CaseVerdict, Confidence, Level, NoteContent
from ais0c_executor.email import CaseAlert, EmailRequest, GroupAlert, HealthAlarm
from ais0c_executor.email.request import HealthAlarmKindName, HealthAlarmState
from ais0c_storage.enums import HealthAlarmKind
from ais0c_workflows import notify
from ais0c_workflows.notify import (
    AbandonedCall,
    CaseAlertRequest,
    EvaluationNoteRequest,
    GroupAlertRequest,
    HealthAlarmNotice,
    NoDecisionNoteRequest,
    abandoned_call,
)

EMAIL_REQUEST: TypeAdapter[EmailRequest] = TypeAdapter(EmailRequest)
AT = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


def a_notice(**changes: object) -> HealthAlarmNotice:
    values: dict[str, object] = {
        "alarm_id": "0193a5c2-7b4e-7d1a-8c3f-5e2b9a1d4f60",
        "alarm_kind": "log_source_silent",
        "subject": "17",
        "subject_name": "FW-DMZ-01",
        "status": "reminder",
        "notification_no": 2,
        "opened_at": AT,
        "counts": {"silent_minutes": 75, "threshold": 60},
    }
    return HealthAlarmNotice.model_validate(values | changes)


def test_the_notice_is_the_executors_health_alarm_field_for_field() -> None:
    assert set(HealthAlarmNotice.model_fields) == set(HealthAlarm.model_fields)
    for name, field in HealthAlarmNotice.model_fields.items():
        assert HealthAlarm.model_fields[name].is_required() == field.is_required(), name


@pytest.mark.parametrize("kind", typing.get_args(HealthAlarmKindName.__value__))
@pytest.mark.parametrize("state", typing.get_args(HealthAlarmState.__value__))
def test_the_executor_takes_what_the_workflow_sends(kind: str, state: str) -> None:
    sent = a_notice(alarm_kind=kind, status=state)

    request = EMAIL_REQUEST.validate_json(to_json(sent))

    assert isinstance(request, HealthAlarm)
    assert request.model_dump(mode="json") == sent.model_dump(mode="json")
    assert request.idempotency_key == f"health_alarm:{sent.alarm_id}:{state}:2"


def test_the_notice_comes_back_from_the_executors_json() -> None:
    """The check activities return the executor's model; the workflow reads it as the notice."""
    alarm = HealthAlarm.model_validate(a_notice().model_dump(mode="json"))

    assert HealthAlarmNotice.model_validate_json(to_json(alarm)) == a_notice()


def test_the_alarm_kinds_and_states_are_the_ones_the_table_holds() -> None:
    kinds = set(typing.get_args(HealthAlarmKindName.__value__))
    assert kinds == {kind.value for kind in HealthAlarmKind}
    assert set(typing.get_args(notify.HealthAlarmKindName.__value__)) == kinds
    assert set(typing.get_args(notify.HealthAlarmState.__value__)) == set(
        typing.get_args(HealthAlarmState.__value__)
    )


def test_the_abandoned_call_is_the_activities_field_for_field() -> None:
    assert set(AbandonedCall.model_fields) == set(ActivityAbandonedCall.model_fields)
    for name, field in AbandonedCall.model_fields.items():
        assert ActivityAbandonedCall.model_fields[name].is_required() == field.is_required(), name


def note() -> NoteContent:
    return NoteContent.model_validate(
        {
            "offense_id": 101,
            "evaluation_no": 3,
            "run_marker": "0123456789ab",
            "verdict": "suspicious",
            "confidence": "medium",
            "notify_level": "critical",
            "summary_tr": "Özet.",
            "urgent_events": [],
            "recommended_actions": [],
            "data_gaps": [],
            "case_url": "https://ais0c.example.com/cases/case-101",
        }
    )


def requests() -> list[
    EvaluationNoteRequest | NoDecisionNoteRequest | CaseAlertRequest | GroupAlertRequest
]:
    return [
        EvaluationNoteRequest(case_id="case-101", evaluated_at=AT, content=note()),
        NoDecisionNoteRequest(
            case_id="case-101",
            offense_id=101,
            evaluation_no=3,
            run_marker="fedcba987654",
            evaluated_at=AT,
            case_url="https://ais0c.example.com/cases/case-101",
        ),
        CaseAlertRequest(
            case_id="case-101", offense_name="Offense", evaluated_at=AT, content=note()
        ),
        GroupAlertRequest(
            case_id="group-G-0123456789ab-20261007T090000Z",
            group_id="G-0123456789ab-20261007T090000Z",
            title="Group",
            offense_count=6,
            evaluation_no=2,
            evaluated_at=AT,
            verdict=CaseVerdict.SUSPICIOUS,
            confidence=Confidence.MEDIUM,
            notify_level=Level.HIGH,
            summary_tr="Özet.",
            urgent_events=[],
            recommended_actions=[],
            case_url="https://ais0c.example.com/cases/group-G-0123456789ab-20261007T090000Z",
        ),
    ]


@pytest.mark.parametrize("request_", requests(), ids=lambda item: type(item).__name__)
def test_every_given_up_request_makes_a_call_the_activity_accepts(
    request_: EvaluationNoteRequest | NoDecisionNoteRequest | CaseAlertRequest | GroupAlertRequest,
) -> None:
    call = abandoned_call(request_)

    accepted = ActivityAbandonedCall.model_validate_json(to_json(call))

    assert accepted.model_dump() == call.model_dump()
    assert accepted.case_id == request_.case_id


def test_the_email_key_is_the_one_the_executor_uses() -> None:
    case_alert, group_alert = requests()[2], requests()[3]
    assert isinstance(case_alert, CaseAlertRequest)
    assert isinstance(group_alert, GroupAlertRequest)
    executor_case = CaseAlert.model_validate_json(to_json(case_alert))
    executor_group = GroupAlert.model_validate_json(to_json(group_alert))

    assert abandoned_call(case_alert).idempotency_key == executor_case.idempotency_key
    assert abandoned_call(group_alert).idempotency_key == executor_group.idempotency_key
