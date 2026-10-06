"""Criterion 4: the cases endpoints (api.md "Vakalar"; architecture §24).

- `GET /cases` filters on `status`, `notify_level`, `verdict`, `source`, `rule_id`, `from` and `to`,
  and lists newest first;
- `GET /cases/{case_id}` holds the case row, the `CaseReport`, the urgent events by rank, the
  recommendations, the last Verification, the data gaps, the evidence (ID, source, tool,
  `query_hash`, time window), the notes written and the e-mails (status, level, recipients, error).
  A case without a report answers with what it has; an unknown ID is a 404;
- `GET /cases/{case_id}/steps` holds the agent runs in evaluation and start order with each run's
  tool calls;
- `POST /cases/{case_id}/feedback` writes `operator_feedback` with the session's subject, and the
  body's `case_id` must be the one in the path.
"""

import json
import uuid
from datetime import timedelta

import pytest
from api_support import (
    CASE_ID,
    EVIDENCE_ID,
    EVIDENCE_ID_2,
    HUNT_CASE_ID,
    OFFENSE_ID,
    T0,
    T1,
    Harness,
    add_offense,
    decided_case,
    open_case,
)

from ais0c_contracts import CaseSource, CaseVerdict, Level
from ais0c_storage.enums import CaseStatus, NoteStatus, NotificationStatus

pytestmark = pytest.mark.anyio


def case_ids(response: object) -> list[str]:
    return [item["case_id"] for item in response.json()["items"]]  # type: ignore[attr-defined]


# --- the queue and its filters -------------------------------------------------------------------


async def test_the_queue_lists_newest_first_with_the_offenses_rule_ids(api: Harness) -> None:
    await add_offense(api.sessions, offense_id=OFFENSE_ID, rule_ids=[100201, 100202])
    await decided_case(api.sessions, case_id="case-1", sla_due_at=T1)
    await decided_case(api.sessions, case_id="case-2", sla_due_at=T1)

    response = await api.get("/cases")

    assert response.status_code == 200
    rows = response.json()["items"]
    assert [row["case_id"] for row in rows] == ["case-2", "case-1"]
    assert rows[0]["rule_ids"] == [100201, 100202]
    assert rows[0]["source"] == CaseSource.OFFENSE
    assert rows[0]["verdict"] == CaseVerdict.SUSPICIOUS
    assert rows[0]["notify_level"] == Level.HIGH
    assert rows[0]["floor_level"] == Level.HIGH
    # The queue row is a summary: it carries no report and no events.
    assert "report" not in rows[0]
    assert "urgent_events" not in rows[0]


async def test_the_queue_filters(api: Harness) -> None:
    # A report has to agree with the decision, so a case with another verdict has no report.
    await decided_case(api.sessions, case_id="case-high", notify_level=Level.HIGH)
    await decided_case(api.sessions, case_id="case-low", notify_level=Level.LOW)
    await decided_case(
        api.sessions, case_id="case-fp", verdict=CaseVerdict.FP, notify_level=Level.MEDIUM
    )
    await decided_case(
        api.sessions,
        case_id=HUNT_CASE_ID,
        source=CaseSource.HUNT,
        offense_id=None,
        hunt_id="hunt-1",
        notify_level=Level.CRITICAL,
    )
    await open_case(api.sessions, case_id="case-running", verdict=None)

    assert set(case_ids(await api.get("/cases", status="decided"))) == {
        "case-high",
        "case-low",
        "case-fp",
        HUNT_CASE_ID,
    }
    assert case_ids(await api.get("/cases", status="running")) == ["case-running"]
    assert set(case_ids(await api.get("/cases", notify_level="low"))) == {"case-low"}
    # A filter given twice is a set, as `?notify_level=low&notify_level=high` is.
    assert set(case_ids(await api.get("/cases", notify_level=["low", "medium", "high"]))) == {
        "case-low",
        "case-fp",
        "case-high",
    }
    assert case_ids(await api.get("/cases", notify_level="critical")) == [HUNT_CASE_ID]
    assert case_ids(await api.get("/cases", verdict="fp")) == ["case-fp"]
    assert case_ids(await api.get("/cases", source="hunt")) == [HUNT_CASE_ID]
    # Two filters combine: a hunt case whose verdict is not `fp` is not kept.
    assert case_ids(await api.get("/cases", source="hunt", verdict="fp")) == []


async def test_the_queue_filters_by_the_rule_of_the_offense(api: Harness) -> None:
    await add_offense(api.sessions, offense_id=OFFENSE_ID, rule_ids=[100201])
    await decided_case(api.sessions, case_id="case-1")
    await decided_case(api.sessions, case_id="case-2", offense_id=OFFENSE_ID + 1)
    await add_offense(
        api.sessions, offense_id=OFFENSE_ID + 1, rule_ids=[100305], description="DCSync"
    )
    await decided_case(api.sessions, case_id="case-3", offense_id=OFFENSE_ID + 2)
    await add_offense(
        api.sessions, offense_id=OFFENSE_ID + 2, rule_ids=[100305], description="DCSync again"
    )

    assert set(case_ids(await api.get("/cases", rule_id=100201))) == {"case-1"}
    assert set(case_ids(await api.get("/cases", rule_id=100305))) == {"case-2", "case-3"}
    assert set(case_ids(await api.get("/cases", rule_id=[100201, 100305]))) == {
        "case-1",
        "case-2",
        "case-3",
    }


async def test_a_case_past_its_sla_is_marked_overdue(api: Harness) -> None:
    await decided_case(api.sessions, case_id="case-late", sla_due_at=T0)
    await open_case(api.sessions, case_id="case-open", sla_due_at=T0, verdict=None)
    await decided_case(api.sessions, case_id="case-ok", sla_due_at=T1 + timedelta(days=365))

    rows = {row["case_id"]: row for row in (await api.get("/cases")).json()["items"]}

    assert rows["case-late"]["sla_overdue"] is False  # it decided, so the clock no longer matters
    assert rows["case-open"]["sla_overdue"] is True
    assert rows["case-ok"]["sla_overdue"] is False


async def test_an_unknown_case_is_a_404(api: Harness) -> None:
    response = await api.get("/cases/case-404")

    assert response.status_code == 404
    assert response.json()["title"] == "case.not_found"


# --- the detail ----------------------------------------------------------------------------------


async def test_the_detail_holds_every_part_of_the_case(api: Harness) -> None:
    await decided_case(api.sessions, with_runs=True)

    body = (await api.get(f"/cases/{CASE_ID}")).json()

    # The row, the report and the decision.
    assert body["case"]["case_id"] == CASE_ID
    assert body["report"]["summary_tr"].startswith("Dış adresten")
    assert body["report"]["verdict"] == "suspicious"
    # The urgent events in rank order.
    assert [event["rank"] for event in body["urgent_events"]] == [1, 2]
    # The recommendations.
    assert [item["action_type"] for item in body["recommendations"]] == ["block_ioc_manual"]
    # The last Verification of the current evaluation.
    assert body["verification"]["agrees"] is True
    assert body["verification"]["checked_evidence_ids"] == [EVIDENCE_ID_2]
    # The report's data gaps and the verification's.
    assert [gap["source"] for gap in body["data_gaps"]] == ["FW-DMZ-01", "DC-LAB-01"]
    # The evidence: ID, source, the tool that issued it, the query hash and the window.
    evidence = {item["evidence_id"]: item for item in body["evidence"]}
    assert set(evidence) == {EVIDENCE_ID, EVIDENCE_ID_2}
    assert evidence[EVIDENCE_ID]["source"] == "qradar"
    assert evidence[EVIDENCE_ID]["tool_id"] == "qradar.ariel_search"
    assert evidence[EVIDENCE_ID]["query_hash"] == "sha256:5d41402abc4b2a76"
    assert evidence[EVIDENCE_ID]["time_start"] == "2026-10-02T10:00:00Z"
    assert evidence[EVIDENCE_ID]["time_end"] == "2026-10-02T11:00:00Z"
    # An evidence only the verification checked has no tool call behind it.
    assert evidence[EVIDENCE_ID_2]["tool_id"] == ""
    # The note written and the alert e-mail.
    assert [note["status"] for note in body["notes"]] == [NoteStatus.WRITTEN.value]
    assert body["notifications"][0]["status"] == NotificationStatus.SENT.value
    assert body["notifications"][0]["level"] == "high"
    assert body["notifications"][0]["recipients"] == ["soc-operators@example.com"]
    assert body["notifications"][0]["error"] is None


async def test_the_detail_reports_a_failed_note_and_a_failed_e_mail(api: Harness) -> None:
    await decided_case(
        api.sessions,
        note_status=NoteStatus.FAILED,
        notification_status=NotificationStatus.FAILED,
    )

    body = (await api.get(f"/cases/{CASE_ID}")).json()

    assert body["notes"][0]["status"] == "failed"
    assert body["notes"][0]["error"] == "gateway unavailable"
    assert body["notifications"][0]["status"] == "failed"
    assert body["notifications"][0]["error"] == "no recipient group is routed to this level"
    assert body["notifications"][0]["sent_at"] is None


async def test_a_case_without_a_report_answers_with_what_it_has(api: Harness) -> None:
    """Reporting produced nothing: the report is null and the list parts are empty."""
    await decided_case(api.sessions, with_report=False, with_notes=False, with_notification=False)

    body = (await api.get(f"/cases/{CASE_ID}")).json()

    assert body["report"] is None
    assert body["urgent_events"] == []
    assert body["recommendations"] == []
    assert body["data_gaps"] == []
    assert body["evidence"] == []
    assert body["notes"] == []
    assert body["notifications"] == []
    # The decision itself is still there.
    assert body["case"]["verdict"] == "suspicious"
    assert body["case"]["evaluation_no"] == 1


async def test_a_running_case_has_no_decision_yet(api: Harness) -> None:
    await open_case(api.sessions, verdict=None)

    body = (await api.get(f"/cases/{CASE_ID}")).json()

    assert body["report"] is None
    assert body["verification"] is None
    assert body["case"]["verdict"] is None
    assert body["case"]["status"] == "running"
    assert body["case"]["decided_at"] is None


async def test_a_case_without_a_verification_run_still_answers(api: Harness) -> None:
    await decided_case(api.sessions, with_runs=False)

    body = (await api.get(f"/cases/{CASE_ID}")).json()

    assert body["verification"] is None
    assert body["report"] is not None


# --- the steps -----------------------------------------------------------------------------------


async def test_the_steps_hold_every_agent_run_with_its_tool_calls(api: Harness) -> None:
    await decided_case(api.sessions, with_runs=True)

    steps = (await api.get(f"/cases/{CASE_ID}/steps")).json()

    assert [step["step"]["run_id"] for step in steps] == [
        f"{CASE_ID}-triage-1",
        f"{CASE_ID}-verification-1",
    ]
    triage, verification = steps
    assert triage["step"]["agent_id"] == "triage"
    assert triage["step"]["model_alias"] == "soc-fast"
    assert triage["step"]["status"] == "completed"
    assert triage["step"]["tokens"] == 1500
    assert triage["step"]["tool_call_count"] == 1
    assert triage["step"]["duration_seconds"] == 30.0
    assert triage["step"]["error"] is None
    assert triage["tool_calls"] == [
        {
            "tool_id": "qradar.ariel_search",
            "policy_decision": "allow",
            "status": "ok",
            "deny_reason": None,
            "evidence_id": EVIDENCE_ID,
            "latency_ms": 120,
        }
    ]
    assert verification["step"]["agent_id"] == "verification"
    assert verification["tool_calls"] == []


async def test_a_run_that_failed_carries_its_reason_and_no_duration(api: Harness) -> None:
    """A failed run has an `error` and no `ended_at`, so its duration is not known."""
    await open_case(api.sessions, verdict=None)
    task = json.dumps(
        {
            "task_id": "t",
            "parent_run_id": "r",
            "case_id": CASE_ID,
            "hunt_id": None,
            "agent_id": "triage",
            "agent_version": "1",
            "objective": "Decide.",
            "context_refs": [],
            "time_window": {"start": "2026-10-02T10:00:00Z", "end": "2026-10-02T11:00:00Z"},
            "budget": {"tokens": 20000, "tool_calls": 10, "seconds": 120},
        }
    )
    await api.rows(
        """
        INSERT INTO agent_runs (run_id, case_id, agent_id, agent_version, prompt_version,
                                model_alias, model_target, toolset_profile, status, task, result,
                                error, tokens, tool_calls, started_at, ended_at)
        VALUES (:run_id, :case_id, 'triage', '1', 'v1', 'soc-fast', 'lab-model',
                'qradar-triage-read', 'failed', CAST(:task AS jsonb), NULL,
                'model unreachable', 0, 0, '2026-10-02T10:00:00Z', NULL)
        RETURNING run_id
        """,
        {"run_id": f"{CASE_ID}-triage-1", "case_id": CASE_ID, "task": task},
    )

    steps = (await api.get(f"/cases/{CASE_ID}/steps")).json()

    assert steps[0]["step"]["status"] == "failed"
    assert steps[0]["step"]["error"] == "model unreachable"
    assert steps[0]["step"]["duration_seconds"] is None
    assert steps[0]["step"]["ended_at"] is None


async def test_the_steps_of_an_unknown_case_are_a_404(api: Harness) -> None:
    response = await api.get("/cases/case-404/steps")

    assert response.status_code == 404
    assert response.json()["title"] == "case.not_found"


# --- the feedback --------------------------------------------------------------------------------


async def test_the_feedback_is_written_with_the_sessions_subject(api: Harness) -> None:
    await decided_case(api.sessions)

    response = await api.post(
        f"/cases/{CASE_ID}/feedback",
        {
            "case_id": CASE_ID,
            "verdict": "tp",
            "reason": "was_fp_not_tp",
            "comment": "Gerçek saldırı.",
        },
    )

    assert response.status_code == 201
    assert response.json() == {
        "case_id": CASE_ID,
        "user_subject": "synthetic-operator",
        "verdict": "tp",
        "reason": "was_fp_not_tp",
        "comment": "Gerçek saldırı.",
        "created_at": response.json()["created_at"],
    }
    rows = await api.rows("SELECT * FROM operator_feedback ORDER BY created_at")
    assert len(rows) == 1
    assert rows[0]["user_subject"] == "synthetic-operator"


async def test_the_feedback_of_an_admin_names_the_admin(api: Harness) -> None:
    await decided_case(api.sessions)

    await api.post(
        f"/cases/{CASE_ID}/feedback",
        {"case_id": CASE_ID, "verdict": "fp", "reason": "correct"},
        as_role="admin",
    )

    rows = await api.rows("SELECT user_subject FROM operator_feedback")
    assert [row["user_subject"] for row in rows] == ["synthetic-admin"]


async def test_a_case_id_that_is_not_the_one_in_the_path_is_422(api: Harness) -> None:
    await decided_case(api.sessions)

    response = await api.post(
        f"/cases/{CASE_ID}/feedback",
        {"case_id": "case-other", "verdict": "tp", "reason": "was_fp_not_tp"},
    )

    assert response.status_code == 422
    assert response.json()["title"] == "case.feedback_case_mismatch"
    assert await api.rows("SELECT * FROM operator_feedback") == []
    assert await api.rows("SELECT * FROM audit_log") == []


async def test_the_feedback_of_an_unknown_case_is_a_404(api: Harness) -> None:
    response = await api.post(
        "/cases/case-404/feedback",
        {"case_id": "case-404", "verdict": "tp", "reason": "was_fp_not_tp"},
    )

    assert response.status_code == 404
    assert response.json()["title"] == "case.not_found"


async def test_the_feedback_is_listed_newest_last(api: Harness) -> None:
    await decided_case(api.sessions)
    for verdict, reason in (("fp", "correct"), ("tp", "was_fp_not_tp")):
        await api.post(
            f"/cases/{CASE_ID}/feedback", {"case_id": CASE_ID, "verdict": verdict, "reason": reason}
        )

    listed = (await api.get(f"/cases/{CASE_ID}/feedback")).json()

    assert [item["verdict"] for item in listed] == ["fp", "tp"]
    assert len(await api.rows("SELECT * FROM operator_feedback")) == 2


async def test_the_feedback_accepts_several_comments_for_one_case(api: Harness) -> None:
    """Feedback is a record, not state: nothing is overwritten and every row is kept."""
    await decided_case(api.sessions)

    for _ in range(2):
        response = await api.post(
            f"/cases/{CASE_ID}/feedback",
            {"case_id": CASE_ID, "verdict": "fp", "reason": "correct"},
        )
        assert response.status_code == 201

    rows = await api.rows("SELECT id, verdict FROM operator_feedback ORDER BY created_at, id")
    assert len(rows) == 2
    # The rows carry UUIDv7 identifiers of their own.
    assert len({str(uuid.UUID(str(row["id"]))) for row in rows}) == 2


async def test_a_case_of_a_hunt_has_no_rule_ids(api: Harness) -> None:
    await decided_case(
        api.sessions,
        case_id=HUNT_CASE_ID,
        source=CaseSource.HUNT,
        offense_id=None,
        hunt_id="hunt-1",
    )

    rows = (await api.get("/cases", source="hunt")).json()["items"]

    assert rows[0]["rule_ids"] == []
    assert rows[0]["hunt_id"] == "hunt-1"
    assert rows[0]["offense_id"] is None


async def test_a_closed_case_is_still_listed(api: Harness) -> None:
    await decided_case(api.sessions)
    await api.seed("UPDATE cases SET status = 'closed'")

    rows = (await api.get("/cases", status="closed")).json()["items"]

    assert [row["case_id"] for row in rows] == [CASE_ID]
    assert rows[0]["status"] == CaseStatus.CLOSED.value
