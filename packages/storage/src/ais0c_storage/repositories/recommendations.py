"""`recommendations`: the actions the AI proposes to the operator, per evaluation (architecture
§9, "Önerilen aksiyonlar"). The AI never takes them itself."""

from collections.abc import Sequence

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_contracts import Recommendation
from ais0c_storage.columns import revalidate
from ais0c_storage.models import RecommendationRow
from ais0c_storage.repositories._common import fetch_all, insert_row


async def replace_recommendations(
    session: AsyncSession,
    case_id: str,
    evaluation_no: int,
    recommendations: Sequence[Recommendation],
) -> list[RecommendationRow]:
    """Store the evaluation's recommendations in place of any it has, so storing them twice
    leaves one set."""
    checked = [revalidate(Recommendation, item) for item in recommendations]
    await session.execute(
        delete(RecommendationRow).where(
            RecommendationRow.case_id == case_id,
            RecommendationRow.evaluation_no == evaluation_no,
        )
    )
    return [
        await insert_row(
            session,
            RecommendationRow,
            dict(case_id=case_id, evaluation_no=evaluation_no, recommendation=item),
        )
        for item in checked
    ]


async def list_recommendations(
    session: AsyncSession, case_id: str, *, evaluation_no: int | None = None
) -> list[RecommendationRow]:
    """The case's recommendations by evaluation.

    The table has no position column: rows stored in the same millisecond come back in any
    order. The report in `cases.report` keeps the evaluation's order.
    """
    statement = select(RecommendationRow).where(RecommendationRow.case_id == case_id)
    if evaluation_no is not None:
        statement = statement.where(RecommendationRow.evaluation_no == evaluation_no)
    statement = statement.order_by(RecommendationRow.evaluation_no, RecommendationRow.id)
    return await fetch_all(session, statement)
