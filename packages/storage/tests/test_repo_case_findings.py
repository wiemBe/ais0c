"""Repository functions of `qa_items`, `urgent_events` and `recommendations` (T-026 criteria 6
and 7)."""

import pytest
import storage_payloads as payloads
from sqlalchemy.ext.asyncio import AsyncSession
from storage_payloads import CASE_ID, T1

from ais0c_contracts import CaseSource, QAReason
from ais0c_storage.enums import QAStatus
from ais0c_storage.repositories import (
    add_qa_items,
    create_case,
    list_qa_items,
    list_recommendations,
    list_urgent_events,
    replace_recommendations,
    replace_urgent_events,
)

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
async def case(session: AsyncSession) -> None:
    await create_case(
        session,
        case_id=CASE_ID,
        source=CaseSource.OFFENSE,
        offense_id=12345,
        sla_due_at=T1,
        workflow_id=CASE_ID,
        run_id="run-1",
    )


async def test_qa_items_open_one_per_reason(session: AsyncSession) -> None:
    reasons = [QAReason.LOW_CONFIDENCE, QAReason.VERIFIER_CONFLICT, QAReason.LOW_CONFIDENCE]

    added = await add_qa_items(session, CASE_ID, reasons)

    assert [(row.reason, row.status) for row in added] == [
        (QAReason.LOW_CONFIDENCE, QAStatus.OPEN),
        (QAReason.VERIFIER_CONFLICT, QAStatus.OPEN),
    ]
    listed = await list_qa_items(session, CASE_ID)
    assert {row.reason for row in listed} == {QAReason.LOW_CONFIDENCE, QAReason.VERIFIER_CONFLICT}
    assert await list_qa_items(session, CASE_ID, statuses=[QAStatus.RESOLVED]) == []
    assert await add_qa_items(session, CASE_ID, []) == []


async def test_urgent_events_of_an_evaluation_are_replaced_not_added(
    session: AsyncSession,
) -> None:
    events = [payloads.urgent_event(1), payloads.urgent_event(2)]

    await replace_urgent_events(session, CASE_ID, 1, events)
    await replace_urgent_events(session, CASE_ID, 1, events)
    await replace_urgent_events(session, CASE_ID, 2, [payloads.urgent_event(1)])

    first = await list_urgent_events(session, CASE_ID, evaluation_no=1)
    assert [(row.evaluation_no, row.rank, row.event) for row in first] == [
        (1, 1, events[0]),
        (1, 2, events[1]),
    ]
    assert [
        (row.evaluation_no, row.rank) for row in await list_urgent_events(session, CASE_ID)
    ] == [
        (1, 1),
        (1, 2),
        (2, 1),
    ]
    await replace_urgent_events(session, CASE_ID, 1, [])
    assert await list_urgent_events(session, CASE_ID, evaluation_no=1) == []


async def test_recommendations_of_an_evaluation_are_replaced_not_added(
    session: AsyncSession,
) -> None:
    item = payloads.recommendation()

    await replace_recommendations(session, CASE_ID, 1, [item])
    await replace_recommendations(session, CASE_ID, 1, [item])
    await replace_recommendations(session, CASE_ID, 2, [item, item])

    rows = await list_recommendations(session, CASE_ID)
    assert [(row.evaluation_no, row.recommendation) for row in rows] == [
        (1, item),
        (2, item),
        (2, item),
    ]
    assert len(await list_recommendations(session, CASE_ID, evaluation_no=2)) == 2
