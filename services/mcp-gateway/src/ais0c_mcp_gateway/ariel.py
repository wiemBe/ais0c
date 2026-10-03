"""Ariel search ownership (architecture §11.1, §13.3; T-011 criterion 6).

A search can be read and deleted only in the case or hunt that started it through the gateway,
and only under the profile that started it. A profile with an output filter therefore never
reads a search whose query it could not have run. The proof of ownership is the gateway's own
record: a successful `create` call in a run of that case or hunt whose evidence names the
search. A search started any other way, by an analyst or another platform, is not found.

This check sits on top of the fork's own one (T-006), which deletes only searches the fork
created; neither replaces the other.
"""

from collections.abc import Collection
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_contracts import EvidenceRef, ToolStatus
from ais0c_storage.models import AgentRunRow, EvidenceRow, ToolCallRow
from ais0c_storage.repositories import to_evidence_ref

SEARCH_ID: Final = "search_id"
# Search states after which QRadar no longer runs the search (Ariel API `status`).
FINAL_STATUSES: Final = frozenset({"COMPLETED", "CANCELED", "ERROR"})


async def find_owned_search(
    session: AsyncSession,
    *,
    search_id: str,
    case_id: str | None,
    hunt_id: str | None,
    profile: str,
    create_tools: Collection[str],
) -> EvidenceRef | None:
    """The evidence of the call that started `search_id` in this context, or None.

    The context is the case when `case_id` is set, otherwise the hunt.
    """
    statement = (
        select(EvidenceRow)
        .join(ToolCallRow, ToolCallRow.evidence_id == EvidenceRow.evidence_id)
        .join(AgentRunRow, AgentRunRow.run_id == ToolCallRow.run_id)
        .where(AgentRunRow.toolset_profile == profile)
        .where(ToolCallRow.status == ToolStatus.OK)
        .where(EvidenceRow.identifiers["tool"].astext.in_(list(create_tools)))
        .where(EvidenceRow.identifiers[SEARCH_ID].astext == search_id)
        .order_by(EvidenceRow.retrieved_at)
        .limit(1)
    )
    if case_id:
        statement = statement.where(AgentRunRow.case_id == case_id)
    elif hunt_id:
        statement = statement.where(AgentRunRow.hunt_id == hunt_id, AgentRunRow.case_id.is_(None))
    else:
        return None
    row = await session.scalar(statement)
    return None if row is None else to_evidence_ref(row)
