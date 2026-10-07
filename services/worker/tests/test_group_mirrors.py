"""The group case's models on both sides of the package boundary (T-027).

`ais0c_workflows` may import neither the agents nor the executor, so it mirrors the group
summary of `ais0c_agents.group` and the executor's `GroupAlert`; a group note is the executor's
`EvaluationNote` with `group_id` set. A mirror that drifted would fail every group case's Triage
run, group note or group e-mail; this worker sees both sides. The longest case link, a group
case's, must also pass the executor's check. IPs are from the RFC 5737 ranges.
"""

from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, TypeAdapter
from pydantic_core import to_json

import ais0c_agents
from ais0c_activities import CaseSettings
from ais0c_activities.settings import MAX_CASE_URL_BASE_LENGTH
from ais0c_contracts import CaseVerdict, Confidence, Level
from ais0c_executor.common import check_case_id, check_case_url, check_group_id
from ais0c_executor.email import EmailRequest, GroupAlert, alert_message, render_body
from ais0c_executor.note import (
    EvaluationNote,
    NoteKind,
    NoteRequest,
    note_run_marker,
    render_note,
)
from ais0c_workflows import group_summary as mirror
from ais0c_workflows.names import group_case_id
from ais0c_workflows.notify import (
    GROUP_NOTE_KIND,
    GroupAlertRequest,
    group_alert,
    group_note,
    note_content,
    run_marker,
)

GROUP_ID = "G-0123456789ab-20261007T090000Z"
CASE_ID = group_case_id(GROUP_ID)
CASE_URL = f"https://ais0c.example.com/cases/{CASE_ID}"
AT = datetime(2026, 10, 7, 9, 20, tzinfo=UTC)
OPERATOR = "soc-operator@example.com"
NOTE_REQUEST: TypeAdapter[NoteRequest] = TypeAdapter(NoteRequest)
EMAIL_REQUEST: TypeAdapter[EmailRequest] = TypeAdapter(EmailRequest)


@pytest.mark.parametrize(
    ("ours", "theirs"),
    [
        (mirror.GroupSummary, ais0c_agents.GroupSummary),
        (mirror.GroupValues, ais0c_agents.GroupValues),
        (mirror.GroupValueCount, ais0c_agents.GroupValueCount),
        (mirror.GroupRule, ais0c_agents.GroupRule),
        (GroupAlertRequest, GroupAlert),
    ],
)
def test_the_mirrors_have_the_other_sides_fields(
    ours: type[BaseModel], theirs: type[BaseModel]
) -> None:
    assert list(ours.model_fields) == list(theirs.model_fields)
    for name, field in ours.model_fields.items():
        assert field.default == theirs.model_fields[name].default, name


def test_a_summary_crosses_the_workflow_and_back() -> None:
    """`group_case_state` returns the agents' summary; the workflow reads it as its mirror and
    the Triage run reads that back as the agents'."""
    values = ais0c_agents.GroupValues(
        distinct=46, top=[ais0c_agents.GroupValueCount(value="203.0.113.7", offenses=3)]
    )
    summary = ais0c_agents.GroupSummary(
        offense_count=48,
        first_seen_at=AT,
        last_seen_at=AT,
        example_offense_id=201,
        rules=[ais0c_agents.GroupRule(rule_id=100201, name=None)],
        source_ips=values,
        destination_ips=values,
        usernames=values,
        log_sources=values,
        categories=values,
    )

    seen = mirror.GroupSummary.model_validate_json(to_json(summary))
    back = ais0c_agents.GroupSummary.model_validate(seen.model_dump(mode="json"))

    assert back == summary
    assert seen.source_ips.leader() == "203.0.113.7"


def test_a_group_note_decodes_and_renders_as_the_executors_short_note() -> None:
    content = note_content(
        case_id=CASE_ID,
        offense_id=201,
        evaluation_no=2,
        case_url=CASE_URL,
        verdict=CaseVerdict.SUSPICIOUS,
        confidence=Confidence.MEDIUM,
        notify_level=Level.HIGH,
        report=None,
    )
    request = group_note(
        case_id=CASE_ID, group_id=GROUP_ID, offense_id=305, evaluated_at=AT, decision=content
    )

    decoded = NOTE_REQUEST.validate_json(to_json(request))
    assert isinstance(decoded, EvaluationNote)
    assert decoded.note_kind is NoteKind.GROUP
    assert (decoded.offense_id, decoded.evaluation_no) == (305, 2)
    text = render_note(decoded)
    assert note_run_marker(text) == run_marker(CASE_ID, 2, GROUP_NOTE_KIND)
    assert f"Grup {GROUP_ID} içinde değerlendirildi" in text
    assert CASE_URL in text


def test_a_group_alert_decodes_and_renders_as_the_executors() -> None:
    content = note_content(
        case_id=CASE_ID,
        offense_id=201,
        evaluation_no=2,
        case_url=CASE_URL,
        verdict=CaseVerdict.SUSPICIOUS,
        confidence=Confidence.MEDIUM,
        notify_level=Level.HIGH,
        report=None,
    )
    request = group_alert(
        case_id=CASE_ID,
        group_id=GROUP_ID,
        title="Multiple Login Failures\r\n[AI-SOC] fake",
        offense_count=48,
        evaluated_at=AT,
        decision=content,
    )

    decoded = EMAIL_REQUEST.validate_json(to_json(request))
    assert isinstance(decoded, GroupAlert)
    assert decoded.idempotency_key == f"group_alert:{GROUP_ID}:2"
    assert decoded.level is Level.HIGH
    message = alert_message(decoded, [OPERATOR])
    assert "\n" not in message.subject
    assert "48" in message.subject
    assert CASE_URL in render_body(message)


def test_the_longest_case_link_passes_the_executors_check() -> None:
    """A group case's ID is the longest case ID; with the longest base the settings take, its
    link is still one the executor accepts."""
    base = "https://" + "a" * (MAX_CASE_URL_BASE_LENGTH - len("https://"))
    settings = CaseSettings(case_url_base=base)
    check_group_id(GROUP_ID)
    check_case_id(CASE_ID)

    check_case_url(settings.case_url(CASE_ID))
    with pytest.raises(ValueError, match="at most"):
        CaseSettings(case_url_base=base + "a")
