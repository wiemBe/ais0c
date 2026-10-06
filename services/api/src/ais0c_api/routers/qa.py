"""The QA queue: the cases waiting for an operator's check, and resolving one
(api.md "QA kuyruğu"; T-028 criterion 5).

Resolving an item writes the item and the case's `OperatorFeedback` in one transaction, so a
resolved item always has its feedback; an item that is already resolved is a 409 and writes
nothing.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_api import audit
from ais0c_api.dependencies import OPERATOR, ReadSession, WriteSession, now
from ais0c_api.models import FeedbackAnswer, Page, QAItemSummary, QAResolveAnswer, QAResolveRequest
from ais0c_api.pagination import check_limit, paginate, uuid_cursor
from ais0c_api.problems import Problem, not_found
from ais0c_contracts import OperatorFeedback, QAReason
from ais0c_storage.enums import QAStatus
from ais0c_storage.models import CaseRow, QAItemRow
from ais0c_storage.repositories import (
    add_operator_feedback,
    get_case,
    get_qa_item,
    list_qa_queue,
    resolve_qa_item,
)

router = APIRouter(prefix="/qa", tags=["qa"])

StatusFilter = Annotated[list[QAStatus] | None, Query()]
ReasonFilter = Annotated[list[QAReason] | None, Query()]
CursorParam = Annotated[str | None, Query()]
LimitParam = Annotated[int | None, Query()]


def summary(item: QAItemRow, case: CaseRow | None) -> QAItemSummary:
    """A queue row with its case's decision fields, so the UI needs no second request."""
    return QAItemSummary(
        id=item.id,
        case_id=item.case_id,
        evaluation_no=item.evaluation_no,
        reason=item.reason,
        status=item.status,
        resolved_by=item.resolved_by,
        resolved_at=item.resolved_at,
        case_status=case.status if case is not None else None,
        case_verdict=case.verdict if case is not None else None,
        case_notify_level=case.notify_level if case is not None else None,
        case_summary_tr=case.report.summary_tr if case is not None and case.report else None,
    )


async def summaries(session: AsyncSession, items: list[QAItemRow]) -> list[QAItemSummary]:
    """The queue rows of a page, with their cases read in one query each."""
    summaries: list[QAItemSummary] = []
    for item in items:
        case = await get_case(session, item.case_id)
        summaries.append(summary(item, case))
    return summaries


@router.get("", response_model=Page[QAItemSummary])
async def get_qa_queue(
    session: ReadSession,
    _user: OPERATOR,
    status: StatusFilter = None,
    reason: ReasonFilter = None,
    cursor: CursorParam = None,
    limit: LimitParam = None,
) -> Page[QAItemSummary]:
    """The QA queue across all cases, oldest first: the longest waiting item is first."""
    page_size = check_limit(limit)
    items = await list_qa_queue(
        session,
        statuses=set(status) if status else None,
        reasons=set(reason) if reason else None,
        after=uuid_cursor(cursor),
        limit=page_size + 1,
    )
    page, next_cursor = paginate(items, page_size, lambda item: str(item.id))
    return Page(items=await summaries(session, page), next_cursor=next_cursor)


@router.post("/{item_id}/resolve", response_model=QAResolveAnswer)
async def resolve_qa_item_route(
    item_id: UUID, body: QAResolveRequest, session: WriteSession, user: OPERATOR
) -> QAResolveAnswer:
    """Resolve a QA item and record the operator's feedback for its case, in one transaction.

    An item that is already resolved is a 409 (`qa.already_resolved`) and neither the item nor
    the feedback changes.
    """
    item = await get_qa_item(session, item_id)
    if item is None:
        raise not_found("qa.item_not_found")
    if item.status is not QAStatus.OPEN:
        raise Problem(409, "qa.already_resolved")
    case = await get_case(session, item.case_id)
    if case is None:
        # The case is a foreign key's target; only a database someone edited can get here.
        raise not_found("case.not_found")
    resolved = await resolve_qa_item(session, item_id, resolved_by=user.subject, resolved_at=now())
    feedback = await add_operator_feedback(
        session,
        OperatorFeedback(
            case_id=item.case_id, verdict=body.verdict, reason=body.reason, comment=body.comment
        ),
        user_subject=user.subject,
    )
    await audit.record(
        session,
        actor_id=user.subject,
        action=audit.ACTION_QA_RESOLVE,
        object_type=audit.OBJECT_QA_ITEM,
        object_id=str(item_id),
        details={
            "case_id": item.case_id,
            "evaluation_no": item.evaluation_no,
            "qa_reason": item.reason.value,
            "verdict": body.verdict.value,
            "feedback_reason": body.reason.value,
        },
    )
    return QAResolveAnswer(
        item=summary(resolved, case),
        feedback=FeedbackAnswer(
            case_id=feedback.case_id,
            user_subject=feedback.user_subject,
            verdict=feedback.verdict,
            reason=feedback.reason,
            comment=feedback.comment,
            created_at=feedback.created_at,
        ),
    )
