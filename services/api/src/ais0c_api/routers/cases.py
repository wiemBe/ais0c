"""Cases: the offense queue, the case detail, the agent steps and the operator feedback
(api.md "Vakalar"; T-028 criteria 3 and 4).

Only reading the platform's own tables, and writing the one thing an operator posts. Nothing here
closes a case, changes a rule or runs an action (D-02, D-19).
"""

from collections.abc import Callable, Collection, Sequence
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_api import audit
from ais0c_api.dependencies import (
    OPERATOR,
    FromParam,
    ReadSession,
    ToParam,
    WriteSession,
    aware,
    now,
    offense_url_of,
)
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
from ais0c_api.pagination import DEFAULT_LIMIT, CursorParam, LimitParam, paginate, timestamp_cursor
from ais0c_api.problems import invalid_request, not_found
from ais0c_api.run_ids import evaluation_of
from ais0c_contracts import (
    CaseReport,
    CaseSource,
    CaseVerdict,
    DataGap,
    Level,
    OperatorFeedback,
    Recommendation,
    UrgentEvent,
    VerificationResult,
)
from ais0c_storage.enums import CaseStatus
from ais0c_storage.models import AgentRunRow, CaseRow, ToolCallRow
from ais0c_storage.repositories import (
    add_operator_feedback,
    get_case,
    list_agent_runs,
    list_cases,
    list_evidence_by_ids,
    list_notes,
    list_notifications,
    list_offenses_by_ids,
    list_operator_feedback,
    list_recommendations,
    list_tool_calls_of_runs,
    list_tools_for_evidence,
    list_urgent_events,
)
from ais0c_storage.repositories.cases import CaseCursor

router = APIRouter(prefix="/cases", tags=["cases"])

# The manifest IDs of the two chain agents the detail reads a result from.
VERIFICATION_AGENT = "verification"
REPORTING_AGENT = "reporting"

# The statuses whose evaluation has no decision of its own (yet): the SLA clock still matters.
UNDECIDED_STATUSES = frozenset({CaseStatus.RUNNING, CaseStatus.NO_AI_DECISION})

# A filter given more than once is a set; `None` means "no filter".
StatusFilter = Annotated[list[CaseStatus] | None, Query()]
NotifyLevelFilter = Annotated[list[Level] | None, Query()]
VerdictFilter = Annotated[list[CaseVerdict] | None, Query()]
SourceFilter = Annotated[list[CaseSource] | None, Query()]
RuleFilter = Annotated[list[int] | None, Query()]


def case_cursor(cursor: str | None) -> CaseCursor | None:
    """The `?cursor=` value as a case cursor; anything unreadable is a 400."""
    key = timestamp_cursor(cursor, field="cursor")
    if key is None:
        return None
    created_at, case_id = key
    return CaseCursor(created_at=created_at, case_id=case_id)


async def summaries(
    session: AsyncSession,
    rows: list[CaseRow],
    *,
    at: datetime,
    offense_url: Callable[[int | None], str | None],
) -> list[CaseSummary]:
    """The queue rows, each with its offense's description and rule IDs in one extra query for
    the page."""
    offenses = await list_offenses_by_ids(
        session, [row.offense_id for row in rows if row.offense_id is not None]
    )
    summaries: list[CaseSummary] = []
    for row in rows:
        offense = offenses.get(row.offense_id) if row.offense_id is not None else None
        summaries.append(
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
                offense_description=None if offense is None else offense.description,
                hunt_id=row.hunt_id,
                group_id=row.group_id,
                rule_ids=[] if offense is None else list(offense.rule_ids),
                # A re-evaluation keeps the previous decision's `decided_at` until it records its
                # own, so the status, not `decided_at`, says whether the clock still runs.
                sla_overdue=row.status in UNDECIDED_STATUSES and row.sla_due_at < at,
                qradar_offense_url=offense_url(row.offense_id),
            )
        )
    return summaries


@router.get("", response_model=Page[CaseSummary])
async def get_cases(
    request: Request,
    _user: OPERATOR,
    session: ReadSession,
    status: StatusFilter = None,
    notify_level: NotifyLevelFilter = None,
    verdict: VerdictFilter = None,
    source: SourceFilter = None,
    rule_id: RuleFilter = None,
    from_: FromParam = None,
    to: ToParam = None,
    cursor: CursorParam = None,
    limit: LimitParam = DEFAULT_LIMIT,
) -> Page[CaseSummary]:
    """The offense queue, newest first (architecture §24).

    `from` and `to` bound `created_at`, `from` inclusive and `to` exclusive.
    """
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
        limit=limit + 1,
    )
    # A cursor holds the last row's sort key as plain strings, which is what it decodes to.
    page, next_cursor = paginate(rows, limit, lambda row: [row.created_at.isoformat(), row.case_id])
    items = await summaries(
        session, page, at=now(), offense_url=lambda offense_id: offense_url_of(request, offense_id)
    )
    return Page(items=items, next_cursor=next_cursor)


def decided_evaluation(row: CaseRow, runs: Sequence[AgentRunRow]) -> int:
    """The evaluation whose decision the case holds.

    A re-evaluation keeps the previous decision and report until it records its own
    (`begin_case_reevaluation`), and keeps them for good when it ends without a decision. Then
    the report is the result of an earlier Reporting run, and that run's ID names its evaluation.
    Without a report to match, the case's own number is the best there is.
    """
    if row.status is CaseStatus.DECIDED or row.report is None:
        return row.evaluation_no
    for run in reversed(runs):
        if run.agent_id == REPORTING_AGENT and run.result == row.report:
            found = evaluation_of(run.run_id, case_id=row.case_id, agent_id=run.agent_id)
            if found is not None:
                return found
    return row.evaluation_no


def runs_of(case_id: str, runs: Sequence[AgentRunRow], evaluation_no: int) -> list[AgentRunRow]:
    """The chain runs of one evaluation, in start order (a retry after the run it retries)."""
    return [
        run
        for run in runs
        if evaluation_of(run.run_id, case_id=case_id, agent_id=run.agent_id) == evaluation_no
    ]


def verification_of(runs: Sequence[AgentRunRow]) -> VerificationResult | None:
    """The result of the evaluation's last Verification run that gave one."""
    for run in reversed(runs):
        if run.agent_id == VERIFICATION_AGENT and isinstance(run.result, VerificationResult):
            return run.result
    return None


def merged_data_gaps(
    report: CaseReport | None, verification: VerificationResult | None
) -> list[DataGap]:
    """The report's data gaps and then Verification's, each once.

    The chain hands Reporting the decision's gaps and Verification's together
    (`ais0c_workflows.chain.case_data_gaps`), so the report usually holds Verification's already;
    a gap the report left out (its limit, T-047) still shows.
    """
    merged: list[DataGap] = []
    for gap in [
        *(report.data_gaps if report is not None else ()),
        *(verification.data_gaps if verification is not None else ()),
    ]:
        if gap not in merged:
            merged.append(gap)
    return merged


def cited_evidence(
    report: CaseReport | None,
    urgent: Sequence[UrgentEvent],
    recommendations: Sequence[Recommendation],
    verification: VerificationResult | None,
) -> list[str]:
    """The evidence IDs the decision cites, each once, in the order they are cited."""
    cited: list[str] = [event.evidence_id for event in urgent]
    for item in recommendations:
        cited.extend(item.evidence_ids)
    if report is not None:
        for claim in report.claims:
            cited.extend(claim.evidence_ids)
    if verification is not None:
        cited.extend(verification.checked_evidence_ids)
    return list(dict.fromkeys(cited))


async def evidence_items(
    session: AsyncSession, calls: Sequence[ToolCallRow], cited: Collection[str]
) -> list[EvidenceItem]:
    """The evaluation's evidence: what its tool calls collected and what the decision cites.

    The tool comes from the call that recorded the evidence; a cited evidence no call of the
    evaluation recorded is looked up in every call, and its tool is empty when none has it.
    """
    tools: dict[str, str] = {}
    for call in calls:
        if call.evidence_id is not None:
            tools.setdefault(call.evidence_id, call.intent.tool_id)
    others = [evidence_id for evidence_id in cited if evidence_id not in tools]
    tools.update(await list_tools_for_evidence(session, others))
    wanted = set(tools) | set(cited)
    return [
        EvidenceItem(
            evidence_id=item.evidence_id,
            source=item.source,
            tool_id=tools.get(item.evidence_id, ""),
            cited=item.evidence_id in cited,
            query_hash=item.query_hash,
            query_text=item.query_text,
            identifiers=dict(item.identifiers),
            time_start=item.time_start,
            time_end=item.time_end,
            retrieved_at=item.retrieved_at,
        )
        for item in await list_evidence_by_ids(session, wanted)
    ]


@router.get("/{case_id}", response_model=CaseDetail)
async def get_case_detail(
    request: Request, case_id: str, _user: OPERATOR, session: ReadSession
) -> CaseDetail:
    """The whole case: the report, the urgent events by rank, the recommendations, the
    Verification, the data gaps, the evidence, the notes written and the e-mails sent.

    The report, the Verification and the evidence are those of the evaluation whose decision the
    case holds (`evaluation_no` of the answer). It is the case's own number unless a
    re-evaluation is running or ended without a decision. A case with no report answers with
    what it has; an unknown `case_id` is a 404.
    """
    row = await get_case(session, case_id)
    if row is None:
        raise not_found("case.not_found")
    report = row.report
    runs = await list_agent_runs(session, case_id=case_id)
    evaluation_no = decided_evaluation(row, runs)
    evaluation_runs = runs_of(case_id, runs, evaluation_no)

    # The report holds the evaluation's events and recommendations in its own order; the
    # `urgent_events` and `recommendations` rows are the same set. Without a report only that
    # evaluation's rows are read: the rows of an earlier evaluation stay in the tables and are not
    # this decision's.
    urgent = (
        list(report.urgent_events)
        if report is not None
        else [
            item.event
            for item in await list_urgent_events(session, case_id, evaluation_no=evaluation_no)
        ]
    )
    recommendations = (
        list(report.recommendations)
        if report is not None
        else [
            item.recommendation
            for item in await list_recommendations(session, case_id, evaluation_no=evaluation_no)
        ]
    )
    verification = verification_of(evaluation_runs)
    calls = await list_tool_calls_of_runs(session, [run.run_id for run in evaluation_runs])

    return CaseDetail(
        case=(
            await summaries(
                session,
                [row],
                at=now(),
                offense_url=lambda offense_id: offense_url_of(request, offense_id),
            )
        )[0],
        evaluation_no=evaluation_no,
        report=report,
        urgent_events=urgent,
        recommendations=recommendations,
        verification=verification,
        data_gaps=merged_data_gaps(report, verification),
        evidence=await evidence_items(
            session, calls, cited_evidence(report, urgent, recommendations, verification)
        ),
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
async def get_case_steps(case_id: str, _user: OPERATOR, session: ReadSession) -> list[CaseStep]:
    """The case's agent runs in start order (so evaluation by evaluation), each with its
    evaluation number and its tool calls.

    An unknown `case_id` is a 404.
    """
    if await get_case(session, case_id) is None:
        raise not_found("case.not_found")
    runs = await list_agent_runs(session, case_id=case_id)
    calls: dict[str, list[ToolCallRow]] = {}
    for call in await list_tool_calls_of_runs(session, [run.run_id for run in runs]):
        calls.setdefault(call.run_id, []).append(call)
    return [
        CaseStep(
            step=AgentStep(
                run_id=run.run_id,
                agent_id=run.agent_id,
                evaluation_no=evaluation_of(run.run_id, case_id=case_id, agent_id=run.agent_id),
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
                for call in calls.get(run.run_id, [])
            ],
        )
        for run in runs
    ]


@router.post("/{case_id}/feedback", response_model=FeedbackAnswer, status_code=201)
async def post_feedback(
    case_id: str, body: OperatorFeedback, user: OPERATOR, session: WriteSession
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
async def get_feedback(case_id: str, _user: OPERATOR, session: ReadSession) -> list[FeedbackAnswer]:
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
