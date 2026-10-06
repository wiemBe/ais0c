"""Criteria 5 and 6: the QA queue and the offense groups (api.md).

QA:
- `GET /qa` filters on `status` and `reason`;
- `POST /qa/{id}/resolve` takes `{ verdict, reason, comment? }`, marks the item `resolved` with
  `resolved_by` and `resolved_at`, and writes the case's `OperatorFeedback` in the same
  transaction;
- a resolved item is a 409.

Groups:
- `GET /groups` filters on `status`;
- `GET /groups/{group_id}` returns the group row, the offenses in it and the group's case decision.
"""

import uuid
from datetime import timedelta

import pytest
from api_support import (
    CASE_ID,
    GROUP_CASE_ID,
    GROUP_ID,
    T0,
    T1,
    Harness,
    add_group,
    decided_case,
    open_case,
    open_qa_items,
)

from ais0c_contracts import CaseSource, CaseVerdict, QAReason
from ais0c_storage.enums import GroupStatus, QAStatus

pytestmark = pytest.mark.anyio


# --- the QA queue ---------------------------------------------------------------------------------


async def test_the_queue_lists_the_open_items_of_every_case(api: Harness) -> None:
    await decided_case(api.sessions, case_id="case-1")
    await decided_case(api.sessions, case_id="case-2")
    first = await open_qa_items(api.sessions, "case-1", [QAReason.LOW_CONFIDENCE])
    second = await open_qa_items(api.sessions, "case-2", [QAReason.VERIFIER_CONFLICT])

    rows = (await api.get("/qa")).json()["items"]

    assert {row["id"] for row in rows} == {str(first[0]), str(second[0])}
    row = rows[0]
    assert row["status"] == QAStatus.OPEN.value
    assert row["evaluation_no"] == 1
    assert row["resolved_by"] is None
    assert row["resolved_at"] is None
    # The case's own decision comes with the row, so the queue needs no second request.
    assert row["case_status"] == "decided"
    assert row["case_verdict"] == CaseVerdict.SUSPICIOUS.value
    assert row["case_notify_level"] == "high"
    assert row["case_summary_tr"].startswith("Dış adresten")


async def test_the_queue_filters_on_status_and_reason(api: Harness) -> None:
    await decided_case(api.sessions, case_id="case-1")
    low = (await open_qa_items(api.sessions, "case-1", [QAReason.LOW_CONFIDENCE]))[0]
    await open_qa_items(api.sessions, "case-1", [QAReason.INJECTION_SUSPECTED])
    await api.post(f"/qa/{low}/resolve", {"verdict": "fp", "reason": "correct"})

    everything = (await api.get("/qa")).json()["items"]
    assert len(everything) == 2
    assert len((await api.get("/qa", status="resolved")).json()["items"]) == 1
    assert len((await api.get("/qa", status="open")).json()["items"]) == 1
    assert [
        row["reason"]
        for row in (await api.get("/qa", reason="injection_suspected")).json()["items"]
    ] == ["injection_suspected"]
    assert (await api.get("/qa", status="open", reason="low_confidence")).json()["items"] == []


async def test_resolving_an_item_writes_the_item_and_the_feedback(api: Harness) -> None:
    await decided_case(api.sessions)
    item = (await open_qa_items(api.sessions, reasons=[QAReason.LOW_CONFIDENCE]))[0]

    response = await api.post(
        f"/qa/{item}/resolve",
        {"verdict": "fp", "reason": "was_tp_not_fp", "comment": "Tarama kaynaklı."},
        as_role="admin",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["item"]["id"] == str(item)
    assert body["item"]["status"] == QAStatus.RESOLVED.value
    assert body["item"]["resolved_by"] == "synthetic-admin"
    assert body["item"]["resolved_at"] is not None
    assert body["feedback"] == {
        "case_id": CASE_ID,
        "user_subject": "synthetic-admin",
        "verdict": "fp",
        "reason": "was_tp_not_fp",
        "comment": "Tarama kaynaklı.",
        "created_at": body["feedback"]["created_at"],
    }
    # The rows are in the database, once each.
    assert len(await api.rows("SELECT * FROM qa_items")) == 1
    assert len(await api.rows("SELECT * FROM operator_feedback")) == 1


async def test_resolving_an_item_leaves_the_case_alone(api: Harness) -> None:
    """The queue answers an operator's question; it does not change the decision."""
    await decided_case(api.sessions)
    item = (await open_qa_items(api.sessions, reasons=[QAReason.LOW_CONFIDENCE]))[0]

    await api.post(f"/qa/{item}/resolve", {"verdict": "fp", "reason": "correct"})

    case = await api.one(
        "SELECT verdict, status, evaluation_no FROM cases WHERE case_id = :case_id",
        {"case_id": CASE_ID},
    )
    assert case == {"verdict": "suspicious", "status": "decided", "evaluation_no": 1}


async def test_resolving_an_item_twice_is_a_409(api: Harness) -> None:
    await decided_case(api.sessions)
    item = (await open_qa_items(api.sessions, reasons=[QAReason.LOW_CONFIDENCE]))[0]
    await api.post(f"/qa/{item}/resolve", {"verdict": "fp", "reason": "correct"})

    second = await api.post(f"/qa/{item}/resolve", {"verdict": "tp", "reason": "was_fp_not_tp"})

    assert second.status_code == 409
    assert second.json()["title"] == "qa.already_resolved"
    # Neither the item nor the feedback changed.
    assert len(await api.rows("SELECT * FROM qa_items WHERE status = 'resolved'")) == 1
    assert len(await api.rows("SELECT * FROM operator_feedback")) == 1
    rows = await api.rows("SELECT verdict FROM operator_feedback")
    assert [row["verdict"] for row in rows] == ["fp"]


async def test_resolving_an_unknown_item_is_a_404(api: Harness) -> None:
    response = await api.post(f"/qa/{uuid.uuid4()}/resolve", {"verdict": "fp", "reason": "correct"})

    assert response.status_code == 404
    assert response.json()["title"] == "qa.item_not_found"


async def test_resolving_with_a_body_that_is_not_the_contract_is_a_422(api: Harness) -> None:
    await decided_case(api.sessions)
    item = (await open_qa_items(api.sessions, reasons=[QAReason.LOW_CONFIDENCE]))[0]

    response = await api.post(f"/qa/{item}/resolve", {"verdict": "fp"})

    assert response.status_code == 422
    assert response.json()["title"] == "request.invalid"
    row = await api.one("SELECT status FROM qa_items WHERE id = :id", {"id": str(item)})
    assert row is not None
    assert row["status"] == "open"


async def test_a_resolved_item_leaves_the_open_queue(api: Harness) -> None:
    await decided_case(api.sessions)
    item = (await open_qa_items(api.sessions, reasons=[QAReason.LOW_CONFIDENCE]))[0]
    await api.post(f"/qa/{item}/resolve", {"verdict": "fp", "reason": "correct"})

    assert (await api.get("/qa", status="open")).json()["items"] == []
    assert len((await api.get("/qa", status="resolved")).json()["items"]) == 1


# --- the groups -----------------------------------------------------------------------------------


async def test_the_groups_are_listed_with_their_own_row(api: Harness) -> None:
    await add_group(api.sessions, group_id="g-open", window_start=T0, offense_ids=(5001, 5002))
    await add_group(
        api.sessions,
        group_id="g-storm",
        status=GroupStatus.STORM,
        window_start=T1,
        offense_ids=(5101,),
    )
    await add_group(
        api.sessions,
        group_id="g-closed",
        status=GroupStatus.CLOSED,
        window_start=T0 - timedelta(hours=1),
        offense_ids=(5201,),
    )

    rows = (await api.get("/groups")).json()["items"]

    # Newest window first.
    assert [row["group_id"] for row in rows] == ["g-storm", "g-open", "g-closed"]
    assert rows[0]["status"] == "storm"
    assert rows[0]["offense_count"] == 1
    assert rows[0]["rule_set_hash"] == "hash-100201"
    assert rows[0]["window_start"] == "2026-10-02T11:00:00Z"
    assert rows[0]["case_id"] is None


async def test_the_groups_filter_on_status(api: Harness) -> None:
    await add_group(api.sessions, group_id="g-open", offense_ids=(5001,))
    await add_group(api.sessions, group_id="g-storm", status=GroupStatus.STORM, offense_ids=(5101,))

    assert [
        row["group_id"] for row in (await api.get("/groups", status="open")).json()["items"]
    ] == ["g-open"]
    assert [
        row["group_id"] for row in (await api.get("/groups", status="storm")).json()["items"]
    ] == ["g-storm"]
    assert (await api.get("/groups", status="closed")).json()["items"] == []


async def test_a_group_detail_holds_its_offenses(api: Harness) -> None:
    await add_group(api.sessions, group_id=GROUP_ID, offense_ids=(5002, 5001))

    body = (await api.get(f"/groups/{GROUP_ID}")).json()

    assert body["group"]["group_id"] == GROUP_ID
    assert [offense["offense_id"] for offense in body["offenses"]] == [5001, 5002]
    assert body["offenses"][0]["status"] == "grouped"
    assert body["offenses"][0]["description"].startswith("Excessive Firewall Accepts")
    # No case yet: the group is still being filled.
    assert body["case_id"] is None
    assert body["case_status"] is None
    assert body["verdict"] is None
    assert body["report"] is None


async def test_a_group_detail_holds_its_case_decision(api: Harness) -> None:
    await add_group(api.sessions, group_id=GROUP_ID, offense_ids=(5001,), case_id=GROUP_CASE_ID)
    await decided_case(
        api.sessions,
        case_id=GROUP_CASE_ID,
        source=CaseSource.GROUP,
        offense_id=None,
        group_id=GROUP_ID,
        verdict=CaseVerdict.FP,
        with_report=False,
        with_notes=False,
        with_notification=False,
    )

    body = (await api.get(f"/groups/{GROUP_ID}")).json()

    assert body["group"]["case_id"] == GROUP_CASE_ID
    assert body["case_id"] == GROUP_CASE_ID
    assert body["case_status"] == "decided"
    assert body["verdict"] == CaseVerdict.FP.value
    assert body["notify_level"] == "high"


async def test_an_unknown_group_is_a_404(api: Harness) -> None:
    response = await api.get("/groups/g-none")

    assert response.status_code == 404
    assert response.json()["title"] == "group.not_found"


async def test_a_group_of_a_case_that_is_still_running_has_no_verdict(api: Harness) -> None:
    await add_group(api.sessions, group_id=GROUP_ID, offense_ids=(5001,), case_id=GROUP_CASE_ID)
    await open_case(
        api.sessions,
        case_id=GROUP_CASE_ID,
        source=CaseSource.GROUP,
        offense_id=None,
        group_id=GROUP_ID,
        verdict=None,
    )

    body = (await api.get(f"/groups/{GROUP_ID}")).json()

    assert body["case_status"] == "running"
    assert body["verdict"] is None
    assert body["report"] is None
