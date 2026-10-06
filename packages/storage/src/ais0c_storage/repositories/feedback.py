"""`operator_feedback`: what an operator thought of a case's decision.

An analyst's feedback is written when the operator posts it on the case or resolves a QA item
(T-028); `user_subject` is the session's OIDC subject. Nothing ever changes a row: feedback is
a record of what the operator said, not state.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_contracts import OperatorFeedback
from ais0c_storage.columns import revalidate
from ais0c_storage.models import OperatorFeedbackRow
from ais0c_storage.repositories._common import fetch_all, insert_row


async def add_operator_feedback(
    session: AsyncSession, feedback: OperatorFeedback, *, user_subject: str
) -> OperatorFeedbackRow:
    """Record `feedback` for its case, as the user `user_subject`.

    Raises `ValueError` for an empty `user_subject`.
    """
    if not user_subject.strip():
        raise ValueError("user_subject is required to record operator feedback")
    return await insert_row(
        session,
        OperatorFeedbackRow,
        dict(
            case_id=revalidate(OperatorFeedback, feedback).case_id,
            user_subject=user_subject,
            verdict=feedback.verdict,
            reason=feedback.reason,
            comment=feedback.comment,
        ),
    )


async def list_operator_feedback(session: AsyncSession, case_id: str) -> list[OperatorFeedbackRow]:
    """A case's feedback, oldest first."""
    statement = (
        select(OperatorFeedbackRow)
        .where(OperatorFeedbackRow.case_id == case_id)
        .order_by(OperatorFeedbackRow.created_at, OperatorFeedbackRow.id)
    )
    return await fetch_all(session, statement)
