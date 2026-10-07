"""Criterion 4: the cases endpoints (api.md "Vakalar"; architecture §24).

- `GET /cases` filters on `status`, `notify_level`, `verdict`, `source`, `rule_id`, `from` and `to`,
  and lists newest first;
- `GET /cases/{case_id}` holds the case row, the `CaseReport`, the urgent events by rank, the
  recommendations, the Verification of the evaluation the case's decision comes from, the data
  gaps (each once), the evidence (ID, source, tool, `query_hash`, time window; what the
  evaluation collected and whether the decision cites it), the notes written and the e-mails
  (status, level, recipients, error). A case without a report answers with what it has; an
  unknown ID is a 404;
- `GET /cases/{case_id}/steps` holds the agent runs in evaluation and start order with each run's
  evaluation number and tool calls;
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
    agent_task,
    case_report,
    data_gap,
    decided_case,
    evidence_ref,
    open_case,
    tool_intent,
    verification_result,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ais0c_contracts import (
    CaseSource,
    CaseVerdict,
    Confidence,
    Level,
    RunStatus,
    ToolStatus,
)
from ais0c_storage.enums import CaseStatus, NoteStatus, NotificationStatus, PolicyDecision
from ais0c_storage.models import AgentRunResult
from ais0c_storage.repositories import (
    begin_case_reevaluation,
    finish_agent_run,
    record_case_decision,
    record_evidence,
    record_tool_call,
    start_agent_run,
)

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
    assert rows[0]["offense_description"] == "Excessive Firewall Accepts"
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


async def test_a_re_evaluation_past_its_deadline_is_overdue(api: Harness) -> None:
    """A re-evaluation keeps the previous decision's `decided_at`; the clock runs all the same."""
    await decided_case(api.sessions, sla_due_at=T0)
    async with api.sessions.begin() as session:
        await begin_case_reevaluation(session, CASE_ID, sla_due_at=T1)

    (row,) = (await api.get("/cases")).json()["items"]

    assert (row["status"], row["evaluation_no"], row["sla_overdue"]) == ("running", 2, True)
    assert row["decided_at"] is not None


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
    assert evidence[EVIDENCE_ID]["cited"] is True
    assert evidence[EVIDENCE_ID_2]["cited"] is True
    assert body["evaluation_no"] == 1
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


EVIDENCE_ID_3 = "ev_01JB3K7Q9Z"


async def add_run(
    sessions: async_sessionmaker[AsyncSession],
    run_id: str,
    agent_id: str,
    *,
    minute: int,
    result: AgentRunResult | None = None,
    evidence_id: str | None = None,
    case_id: str = CASE_ID,
) -> None:
    """A finished agent run, with one tool call when `evidence_id` is given."""
    async with sessions.begin() as session:
        await start_agent_run(
            session,
            run_id=run_id,
            task=agent_task(case_id, agent_id),
            prompt_version="v1",
            model_alias="soc-reasoning",
            model_target="lab-model",
            toolset_profile="qradar-investigate-read",
            started_at=T0 + timedelta(minutes=minute),
        )
        if evidence_id is not None:
            await record_tool_call(
                session,
                run_id=run_id,
                intent=tool_intent(run_id, case_id),
                policy_decision=PolicyDecision.ALLOW,
                status=ToolStatus.OK,
                latency_ms=80,
                evidence_id=evidence_id,
            )
        await finish_agent_run(
            session,
            run_id,
            status=RunStatus.COMPLETED,
            result=result,
            tokens=100,
            tool_calls=0 if evidence_id is None else 1,
            ended_at=T0 + timedelta(minutes=minute, seconds=30),
        )


async def test_the_detail_shows_the_verification_of_the_evaluation_it_decided(
    api: Harness,
) -> None:
    """While a re-evaluation runs, the case still holds evaluation 1's decision and report; the
    Verification shown is evaluation 1's, not the newer one of the evaluation still going."""
    await decided_case(api.sessions, with_runs=True)
    # The Reporting run of evaluation 1, whose result is the report the case holds.
    await add_run(
        api.sessions,
        f"{CASE_ID}-reporting-1",
        "reporting",
        minute=2,
        result=case_report(verdict=CaseVerdict.SUSPICIOUS, notify_level=Level.HIGH),
    )
    async with api.sessions.begin() as session:
        await begin_case_reevaluation(session, CASE_ID, sla_due_at=T1)
    disagreeing = verification_result(gap=False).model_copy(update={"agrees": False})
    await add_run(
        api.sessions, f"{CASE_ID}-verification-2", "verification", minute=10, result=disagreeing
    )

    running = (await api.get(f"/cases/{CASE_ID}")).json()

    assert (running["case"]["evaluation_no"], running["evaluation_no"]) == (2, 1)
    assert running["verification"]["agrees"] is True

    # Evaluation 2 records its decision: now its own Verification is the one shown.
    async with api.sessions.begin() as session:
        await record_case_decision(
            session,
            CASE_ID,
            verdict=CaseVerdict.SUSPICIOUS,
            confidence=Confidence.MEDIUM,
            ai_level=Level.HIGH,
            notify_level=Level.HIGH,
            floor_level=Level.HIGH,
            decided_at=T1,
        )

    decided = (await api.get(f"/cases/{CASE_ID}")).json()

    assert decided["evaluation_no"] == 2
    assert decided["verification"]["agrees"] is False


async def test_a_decision_without_a_report_shows_no_events_of_an_earlier_one(
    api: Harness,
) -> None:
    """Evaluation 1's urgent events and recommendations stay in their tables; evaluation 2,
    decided without a report, must not show them as its own."""
    await decided_case(api.sessions)
    async with api.sessions.begin() as session:
        await begin_case_reevaluation(session, CASE_ID, sla_due_at=T1)
        await record_case_decision(
            session,
            CASE_ID,
            verdict=CaseVerdict.SUSPICIOUS,
            confidence=Confidence.MEDIUM,
            ai_level=Level.HIGH,
            notify_level=Level.HIGH,
            floor_level=Level.HIGH,
            decided_at=T1,
            report=None,
        )

    body = (await api.get(f"/cases/{CASE_ID}")).json()

    assert (body["evaluation_no"], body["report"]) == (2, None)
    assert body["urgent_events"] == []
    assert body["recommendations"] == []
    assert len(await api.rows("SELECT * FROM urgent_events WHERE evaluation_no = 1")) == 2


async def test_a_data_gap_the_report_already_holds_is_listed_once(api: Harness) -> None:
    """The chain hands Reporting Verification's gaps; the detail does not add them again."""
    await decided_case(api.sessions, with_runs=True)
    report = case_report().model_copy(
        update={"data_gaps": [data_gap("FW-DMZ-01"), data_gap("DC-LAB-01")]}
    )
    async with api.sessions.begin() as session:
        await record_case_decision(
            session,
            CASE_ID,
            verdict=report.verdict,
            confidence=report.confidence,
            ai_level=Level.HIGH,
            notify_level=report.notify_level,
            floor_level=Level.HIGH,
            decided_at=T1,
            report=report,
        )

    body = (await api.get(f"/cases/{CASE_ID}")).json()

    assert [gap["source"] for gap in body["data_gaps"]] == ["FW-DMZ-01", "DC-LAB-01"]


async def test_the_evidence_holds_what_the_evaluation_collected_and_did_not_cite(
    api: Harness,
) -> None:
    await decided_case(api.sessions, with_runs=True)
    async with api.sessions.begin() as session:
        await record_evidence(session, evidence_ref(EVIDENCE_ID_3))
    # Investigation found something nothing cites; a run of another case does not count.
    await add_run(
        api.sessions,
        f"{CASE_ID}-investigation-1",
        "investigation",
        minute=1,
        evidence_id=EVIDENCE_ID_3,
    )

    body = (await api.get(f"/cases/{CASE_ID}")).json()

    evidence = {item["evidence_id"]: item for item in body["evidence"]}
    assert set(evidence) == {EVIDENCE_ID, EVIDENCE_ID_2, EVIDENCE_ID_3}
    assert evidence[EVIDENCE_ID_3]["cited"] is False
    assert evidence[EVIDENCE_ID_3]["tool_id"] == "qradar.ariel_search"


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
    assert triage["step"]["evaluation_no"] == 1
    assert verification["step"]["evaluation_no"] == 1
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


async def test_each_step_names_its_evaluation(api: Harness) -> None:
    """The evaluation comes from the run ID (T-29); the platform's own runs have none."""
    await open_case(api.sessions, verdict=None)
    await add_run(api.sessions, f"{CASE_ID}-triage-1", "triage", minute=0)
    await add_run(api.sessions, f"{CASE_ID}-investigation-2", "investigation", minute=5)
    await add_run(api.sessions, f"{CASE_ID}-investigation-2-retry", "investigation", minute=9)
    await add_run(api.sessions, "executor-note-7f3a9c", "executor", minute=12)

    steps = (await api.get(f"/cases/{CASE_ID}/steps")).json()

    assert [(step["step"]["run_id"], step["step"]["evaluation_no"]) for step in steps] == [
        (f"{CASE_ID}-triage-1", 1),
        (f"{CASE_ID}-investigation-2", 2),
        (f"{CASE_ID}-investigation-2-retry", 2),
        ("executor-note-7f3a9c", None),
    ]


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
