"""Cases: the offense queue, the case detail, the agent steps and the operator feedback
(api.md "Vakalar"; T-028 criteria 3 and 4).

Only reading the platform's own tables, and writing the one thing an operator posts. Nothing here
closes a case, changes a rule or runs an action (D-02, D-19).
"""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_api import audit
from ais0c_api.dependencies import OPERATOR, ReadSession, WriteSession, aware, now
from ais0c_api.models import (
    AgentStep,
    CaseDetail,
    CaseStep,
    CaseSummary,
    EvidenceItem,
    FeedbackAnswer,
    NoteItem,
    NotificationItem,
    Page,
    ToolCallItem,
)
from ais0c_api.pagination import check_limit, paginate, timestamp_cursor
from ais0c_api.problems import invalid_request, not_found
from ais0c_contracts import CaseSource, CaseVerdict, Level, OperatorFeedback, VerificationResult
from ais0c_storage.enums import CaseStatus
from ais0c_storage.models import CaseRow
from ais0c_storage.repositories import (
    add_operator_feedback,
    get_case,
    get_last_agent_run,
    list_agent_runs,
    list_cases,
    list_evidence_by_ids,
    list_notes,
    list_notifications,
    list_offenses_by_ids,
    list_operator_feedback,
    list_recommendations,
    list_tool_calls,
    list_tools_for_evidence,
    list_urgent_events,
)
from ais0c_storage.repositories.cases import CaseCursor

router = APIRouter(prefix="/cases", tags=["cases"])

# The last Verification run of the case is the one the detail screen shows.
VERIFICATION_AGENT = "verification"

# A filter given more than once is a set; `None` means "no filter".
StatusFilter = Annotated[list[CaseStatus] | None, Query()]
NotifyLevelFilter = Annotated[list[Level] | None, Query()]
VerdictFilter = Annotated[list[CaseVerdict] | None, Query()]
SourceFilter = Annotated[list[CaseSource] | None, Query()]
RuleFilter = Annotated[list[int] | None, Query()]
FromParam = Annotated[datetime | None, Query(alias="from")]
ToParam = Annotated[datetime | None, Query()]
CursorParam = Annotated[str | None, Query()]
LimitParam = Annotated[int | None, Query()]


def case_cursor(cursor: str | None) -> CaseCursor | None:
    """The `?cursor=` value as a case cursor; anything unreadable is a 400."""
    key = timestamp_cursor(cursor, field="cursor")
    if key is None:
        return None
    created_at, case_id = key
    return CaseCursor(created_at=created_at, case_id=case_id)


async def summaries(
    session: AsyncSession, rows: list[CaseRow], *, at: datetime
) -> list[CaseSummary]:
    """The queue rows, each with its offense's rule IDs in one extra query for the page."""
    offenses = await list_offenses_by_ids(
        session, [row.offense_id for row in rows if row.offense_id is not None]
    )
    return [
        CaseSummary(
            case_id=row.case_id,
            source=row.source,
            status=row.status,
            verdict=row.verdict,
            confidence=row.confidence,
            ai_level=row.ai_level,
            floor_level=row.floor_level,
            notify_level=row.notify_level,
            evaluation_no=row.evaluation_no,
            sla_due_at=row.sla_due_at,
            decided_at=row.decided_at,
            created_at=row.created_at,
            offense_id=row.offense_id,
            hunt_id=row.hunt_id,
            group_id=row.group_id,
            rule_ids=list(offenses[row.offense_id].rule_ids) if row.offense_id in offenses else [],
            sla_overdue=row.decided_at is None and row.sla_due_at < at,
        )
        for row in rows
    ]


@router.get("", response_model=Page[CaseSummary])
async def get_cases(
    session: ReadSession,
    _user: OPERATOR,
    status: StatusFilter = None,
    notify_level: NotifyLevelFilter = None,
    verdict: VerdictFilter = None,
    source: SourceFilter = None,
    rule_id: RuleFilter = None,
    from_: FromParam = None,
    to: ToParam = None,
    cursor: CursorParam = None,
    limit: LimitParam = None,
) -> Page[CaseSummary]:
    """The offense queue, newest first (architecture §24).

    `from` and `to` bound `created_at`, `from` inclusive and `to` exclusive.
    """
    page_size = check_limit(limit)
    rows = await list_cases(
        session,
        statuses=set(status) if status else None,
        sources=set(source) if source else None,
        verdicts=set(verdict) if verdict else None,
        notify_levels=set(notify_level) if notify_level else None,
        rule_ids=set(rule_id) if rule_id else None,
        created_from=aware(from_, "from"),
        created_to=aware(to, "to"),
        after=case_cursor(cursor),
        limit=page_size + 1,
    )
    # A cursor holds the last row's sort key as plain strings, which is what it decodes to.
    page, next_cursor = paginate(
        rows, page_size, lambda row: [row.created_at.isoformat(), row.case_id]
    )
    return Page(items=await summaries(session, page, at=now()), next_cursor=next_cursor)


@router.get("/{case_id}", response_model=CaseDetail)
async def get_case_detail(case_id: str, session: ReadSession, _user: OPERATOR) -> CaseDetail:
    """The whole case: the report, the urgent events by rank, the recommendations, the last
    Verification, the data gaps, the evidence, the notes written and the e-mails sent.

    A case with no report answers with what it has; an unknown `case_id` is a 404.
    """
    row = await get_case(session, case_id)
    if row is None:
        raise not_found("case.not_found")
    report = row.report

    # The report holds the current evaluation's events and recommendations in its own order; the
    # `urgent_events` and `recommendations` rows are the same set, and are what a case decided
    # without a report has.
    urgent = (
        list(report.urgent_events)
        if report is not None
        else [item.event for item in await list_urgent_events(session, case_id)]
    )
    recommendations = (
        list(report.recommendations)
        if report is not None
        else [item.recommendation for item in await list_recommendations(session, case_id)]
    )

    verification_run = await get_last_agent_run(session, case_id, agent_id=VERIFICATION_AGENT)
    verification = (
        verification_run.result
        if verification_run is not None and isinstance(verification_run.result, VerificationResult)
        else None
    )

    data_gaps = list(report.data_gaps) if report is not None else []
    if verification is not None:
        data_gaps.extend(verification.data_gaps)

    evidence_ids = {event.evidence_id for event in urgent}
    for item in recommendations:
        evidence_ids.update(item.evidence_ids)
    if report is not None:
        for claim in report.claims:
            evidence_ids.update(claim.evidence_ids)
    if verification is not None:
        evidence_ids.update(verification.checked_evidence_ids)
    evidence_rows = await list_evidence_by_ids(session, evidence_ids)
    tools = await list_tools_for_evidence(session, evidence_ids)

    return CaseDetail(
        case=(await summaries(session, [row], at=now()))[0],
        report=report,
        urgent_events=urgent,
        recommendations=recommendations,
        verification=verification,
        data_gaps=data_gaps,
        evidence=[
            EvidenceItem(
                evidence_id=item.evidence_id,
                source=item.source,
                tool_id=tools.get(item.evidence_id, ""),
                query_hash=item.query_hash,
                query_text=item.query_text,
                identifiers=dict(item.identifiers),
                time_start=item.time_start,
                time_end=item.time_end,
                retrieved_at=item.retrieved_at,
            )
            for item in evidence_rows
        ],
        notes=[
            NoteItem(
                offense_id=note.offense_id,
                evaluation_no=note.evaluation_no,
                run_marker=note.run_marker,
                status=note.status,
                error=note.error,
                written_at=note.written_at,
            )
            for note in await list_notes(session, case_id)
        ],
        notifications=[
            NotificationItem(
                kind=item.kind,
                level=item.level,
                recipients=list(item.recipients),
                subject=item.subject,
                status=item.status,
                error=item.error,
                sent_at=item.sent_at,
            )
            for item in await list_notifications(session, case_id=case_id)
        ],
    )


@router.get("/{case_id}/steps", response_model=list[CaseStep])
async def get_case_steps(case_id: str, session: ReadSession, _user: OPERATOR) -> list[CaseStep]:
    """The case's agent runs, evaluation by evaluation and in start order, with their tool calls.

    An unknown `case_id` is a 404.
    """
    if await get_case(session, case_id) is None:
        raise not_found("case.not_found")
    steps: list[CaseStep] = []
    for run in await list_agent_runs(session, case_id=case_id):
        calls = await list_tool_calls(session, run.run_id)
        steps.append(
            CaseStep(
                step=AgentStep(
                    run_id=run.run_id,
                    agent_id=run.agent_id,
                    agent_version=run.agent_version,
                    prompt_version=run.prompt_version,
                    model_alias=run.model_alias,
                    status=run.status,
                    tokens=run.tokens,
                    tool_call_count=run.tool_calls,
                    started_at=run.started_at,
                    ended_at=run.ended_at,
                    duration_seconds=(
                        (run.ended_at - run.started_at).total_seconds()
                        if run.ended_at is not None
                        else None
                    ),
                    error=run.error,
                ),
                tool_calls=[
                    ToolCallItem(
                        tool_id=call.intent.tool_id,
                        policy_decision=call.policy_decision,
                        status=call.status,
                        deny_reason=call.deny_reason,
                        evidence_id=call.evidence_id,
                        latency_ms=call.latency_ms,
                    )
                    for call in calls
                ],
            )
        )
    return steps


@router.post("/{case_id}/feedback", response_model=FeedbackAnswer, status_code=201)
async def post_feedback(
    case_id: str, body: OperatorFeedback, session: WriteSession, user: OPERATOR
) -> FeedbackAnswer:
    """Record the operator's verdict on the case, with the session's subject as its author.

    The body's `case_id` must be the one in the path, otherwise 422 and nothing is written.
    """
    if body.case_id != case_id:
        raise invalid_request(
            "the body's case_id must be the case_id in the path",
            title="case.feedback_case_mismatch",
        )
    if await get_case(session, case_id) is None:
        raise not_found("case.not_found")
    row = await add_operator_feedback(session, body, user_subject=user.subject)
    await audit.record(
        session,
        actor_id=user.subject,
        action=audit.ACTION_CASE_FEEDBACK,
        object_type=audit.OBJECT_CASE,
        object_id=case_id,
        details={
            "verdict": row.verdict.value,
            "reason": row.reason.value,
            "comment": row.comment,
        },
    )
    return FeedbackAnswer(
        case_id=row.case_id,
        user_subject=row.user_subject,
        verdict=row.verdict,
        reason=row.reason,
        comment=row.comment,
        created_at=row.created_at,
    )


@router.get("/{case_id}/feedback", response_model=list[FeedbackAnswer])
async def get_feedback(case_id: str, session: ReadSession, _user: OPERATOR) -> list[FeedbackAnswer]:
    """The case's feedback, oldest first."""
    if await get_case(session, case_id) is None:
        raise not_found("case.not_found")
    return [
        FeedbackAnswer(
            case_id=row.case_id,
            user_subject=row.user_subject,
            verdict=row.verdict,
            reason=row.reason,
            comment=row.comment,
            created_at=row.created_at,
        )
        for row in await list_operator_feedback(session, case_id)
    ]
