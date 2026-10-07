"""Test support for the analyst API: synthetic data, a harness and a stand-in Temporal.

Everything is synthetic: IPs come from RFC 5737, hosts from `example.com` and the users from
`synthetic-*`. The dev users file holds only the sha256 of a token, as the real one does, so a
test never has a token in a file.

The PostgreSQL server is the storage tests' test server (packages/storage/tests/storage_postgres.py)
and the harness builds the real app around it, so the routes, the authentication, the transactions
and the audit rows under test are the ones that run in a deployment.
"""

import json
import secrets
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypedDict, Unpack

import httpx2
from fastapi.routing import APIRoute
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ais0c_api.app import build_app
from ais0c_api.auth import DevAuthenticator, token_sha256
from ais0c_api.temporal import ScheduleNotFound, TemporalUnavailable
from ais0c_contracts import (
    ActionType,
    AgentTask,
    CaseReport,
    CaseSource,
    CaseVerdict,
    CatalogMode,
    Confidence,
    DataGap,
    DataGapReason,
    EmailMessage,
    EvidenceRef,
    FeedbackReason,
    Level,
    NoteContent,
    OperatorFeedback,
    QAReason,
    Recommendation,
    RunStatus,
    ToolIntent,
    ToolStatus,
    VerificationResult,
)
from ais0c_storage.enums import (
    CaseStatus,
    GroupStatus,
    NoteStatus,
    NotificationStatus,
    OffenseStatus,
    PolicyDecision,
)
from ais0c_storage.repositories import (
    SyncedLogSource,
    SyncedRule,
    add_offense_seen,
    add_qa_items,
    create_case,
    create_offense_group,
    finish_agent_run,
    record_case_decision,
    record_evidence,
    record_note,
    record_notification,
    record_tool_call,
    replace_recommendations,
    replace_urgent_events,
    start_agent_run,
    sync_catalog_log_sources,
    sync_catalog_rules,
    update_offense_group,
)

# --- the synthetic world ------------------------------------------------------------------------

T0 = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)
T1 = T0 + timedelta(hours=1)
EVIDENCE_ID = "ev_01JB3K7Q9X"
EVIDENCE_ID_2 = "ev_01JB3K7Q9Y"
OFFENSE_ID = 12345
CASE_ID = "case-12345"
HUNT_CASE_ID = "case-hunt-hunt-1-1"
GROUP_ID = "group-g1"
GROUP_CASE_ID = "group-g1"

# What a query parameter may carry; the harness passes these straight to httpx.
type QueryValue = str | int | bool | list[str | int] | None

# Tokens of the dev users, and the users behind them. Only the hash ever reaches a file.
OPERATOR_TOKEN = "synthetic-operator-token-" + secrets.token_hex(16)
HUNTER_TOKEN = "synthetic-hunter-token-" + secrets.token_hex(16)
ADMIN_TOKEN = "synthetic-admin-token-" + secrets.token_hex(16)
# A second admin: double control needs one (D-36).
ADMIN2_TOKEN = "synthetic-admin2-token-" + secrets.token_hex(16)
OPERATOR_SUBJECT = "synthetic-operator"
HUNTER_SUBJECT = "synthetic-hunter"
ADMIN_SUBJECT = "synthetic-admin"
ADMIN2_SUBJECT = "synthetic-admin-2"

# 0007 seeds the routing table and no domain is allowed, so an address test needs one first.
ALLOW_EXAMPLE_DOMAIN = "INSERT INTO allowed_email_domains (domain) VALUES ('example.com')"


def claim(evidence_id: str = EVIDENCE_ID) -> dict[str, Any]:
    return {
        "text": "203.0.113.7 reached an internal host over SMB.",
        "evidence_ids": [evidence_id],
    }


def data_gap(source: str = "FW-DMZ-01") -> DataGap:
    return DataGap(source=source, period_start=T0, period_end=T1, reason=DataGapReason.NOT_PARSED)


def evidence_ref(evidence_id: str = EVIDENCE_ID) -> EvidenceRef:
    return EvidenceRef.model_validate(
        {
            "evidence_id": evidence_id,
            "source": "qradar",
            "query_hash": "sha256:5d41402abc4b2a76",
            "query_text": "SELECT sourceip, destinationip FROM events LAST 1 HOURS",
            "time_start": T0,
            "time_end": T1,
            "identifiers": {"starttime": "1790935930000", "logsourceid": "112", "qid": "5000123"},
            "excerpt": "Firewall Permit 203.0.113.7 -> 198.51.100.15:445",
            "retrieved_at": T1,
        }
    )


def urgent_event(rank: int = 1, evidence_id: str = EVIDENCE_ID) -> dict[str, Any]:
    return {
        "rank": rank,
        "time": T0.isoformat(),
        "log_source": "FW-DMZ-01",
        "event_name": "Firewall Permit",
        "qid": 5000123,
        "source": "203.0.113.7",
        "destination": "198.51.100.15",
        "username": "user01",
        "reason": "First inbound SMB connection from this address.",
        "checklist": ["Is there a VPN login for this user at the same time?"],
        "evidence_id": evidence_id,
    }


def recommendation(evidence_id: str = EVIDENCE_ID) -> Recommendation:
    return Recommendation(
        action_type=ActionType.BLOCK_IOC_MANUAL,
        target="203.0.113.7",
        rationale="External address with an IOC match.",
        evidence_ids=[evidence_id],
    )


def case_report(
    *, verdict: CaseVerdict = CaseVerdict.SUSPICIOUS, notify_level: Level = Level.HIGH
) -> CaseReport:
    """The Reporting agent's output, as `record_case_decision` stores it.

    The report has to agree with the decision the workflow records, so its verdict and notify level
    are given here rather than fixed.
    """
    return CaseReport.model_validate(
        {
            "task_id": "task-report",
            "status": "completed",
            "claims": [claim()],
            "data_gaps": [data_gap().model_dump(mode="json")],
            "injection_suspected": False,
            "usage": {"tokens": 1500, "tool_calls": 2, "seconds": 3.5},
            "summary_tr": "Dış adresten iç sunucuya SMB bağlantısı kabul edilmiş.",
            "verdict": verdict,
            "confidence": "medium",
            "notify_level": notify_level,
            "urgent_events": [urgent_event(1), urgent_event(2)],
            "recommendations": [recommendation()],
        }
    )


def verification_result(*, gap: bool = True) -> VerificationResult:
    return VerificationResult.model_validate(
        {
            "task_id": "task-verify",
            "status": "completed",
            "claims": [claim()],
            "data_gaps": [data_gap("DC-LAB-01").model_dump(mode="json")] if gap else [],
            "injection_suspected": False,
            "usage": {"tokens": 900, "tool_calls": 1, "seconds": 2.0},
            "agrees": True,
            "verdict": "suspicious",
            "confidence": "medium",
            "disagreements": [],
            "checked_evidence_ids": [EVIDENCE_ID_2],
        }
    )


def agent_task(case_id: str, agent_id: str) -> AgentTask:
    return AgentTask.model_validate(
        {
            "task_id": f"task-{agent_id}",
            "parent_run_id": "run-0",
            "case_id": case_id,
            "hunt_id": None,
            "agent_id": agent_id,
            "agent_version": "1",
            "objective": "Decide whether the offense is a true positive.",
            "context_refs": [],
            "time_window": {"start": T0, "end": T1},
            "budget": {"tokens": 20000, "tool_calls": 10, "seconds": 120},
        }
    )


def tool_intent(run_id: str, case_id: str) -> ToolIntent:
    return ToolIntent.model_validate(
        {
            "run_id": run_id,
            "case_id": case_id,
            "hunt_id": None,
            "agent_id": "triage",
            "toolset_profile": "qradar-triage-read",
            "tool_id": "qradar.ariel_search",
            "tool_schema_version": "1",
            "arguments": {"aql": "SELECT sourceip FROM events LAST 1 HOURS", "limit": 100},
            "reason": "Check other connections from the source.",
            "expected_evidence": "Connections from 203.0.113.7 to other hosts.",
            "time_window": {"start": T0, "end": T1},
            "cost_class": "low",
        }
    )


def email_message(case_id: str = CASE_ID) -> EmailMessage:
    return EmailMessage.model_validate(
        {
            "kind": "case_alert",
            "recipients": ["soc-operators@example.com"],
            "subject": "[high] Şüpheli: Excessive Firewall Accepts",
            "template_id": "case_alert_v1",
            "fields": {"case_id": case_id},
            "attachments": [],
            "idempotency_key": f"{case_id}:1",
        }
    )


def operator_feedback(
    case_id: str = CASE_ID,
    verdict: CaseVerdict = CaseVerdict.TP,
    reason: FeedbackReason = FeedbackReason.WAS_FP_NOT_TP,
) -> OperatorFeedback:
    return OperatorFeedback(case_id=case_id, verdict=verdict, reason=reason)


# --- the dev users file -------------------------------------------------------------------------


@dataclass(frozen=True)
class DevUsersFile:
    """A dev users file on disk and the tokens behind it.

    Only `token_sha256` is written, never the token (T-63 (1)).
    """

    path: Path
    operator: str
    hunter: str
    admin: str
    admin2: str

    @classmethod
    def write(
        cls,
        directory: Path,
        *,
        users: Collection[tuple[str, str, str]] | None = None,
    ) -> "DevUsersFile":
        """Write the synthetic users, or the given (token, subject, role) triples."""
        entries = (
            list(users)
            if users is not None
            else [
                (OPERATOR_TOKEN, OPERATOR_SUBJECT, "operator"),
                (HUNTER_TOKEN, HUNTER_SUBJECT, "hunter"),
                (ADMIN_TOKEN, ADMIN_SUBJECT, "admin"),
                (ADMIN2_TOKEN, ADMIN2_SUBJECT, "admin"),
            ]
        )
        path = directory / "dev-users.json"
        path.write_text(
            json.dumps(
                {
                    "users": [
                        {
                            "token_sha256": token_sha256(token),
                            "subject": subject,
                            "display_name": subject.replace("-", " ").title(),
                            "roles": [role],
                        }
                        for token, subject, role in entries
                    ]
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return cls(
            path=path,
            operator=OPERATOR_TOKEN,
            hunter=HUNTER_TOKEN,
            admin=ADMIN_TOKEN,
            admin2=ADMIN2_TOKEN,
        )


# --- the stand-in Temporal ----------------------------------------------------------------------


@dataclass
class FakeScheduleTrigger:
    """A `ScheduleTrigger` that records what it was asked to trigger.

    `unavailable` and `missing` make it fail, so the 503 and 409 paths run without a Temporal
    server.
    """

    triggered: list[str] = field(default_factory=list)
    unavailable: bool = False
    # Temporal answers, but the batch worker never created the Schedule.
    missing: bool = False

    async def trigger(self, schedule_id: str) -> None:
        if self.unavailable:
            raise TemporalUnavailable("cannot reach Temporal at 127.0.0.1:7233")
        if self.missing:
            raise ScheduleNotFound(f"Temporal has no {schedule_id} Schedule")
        self.triggered.append(schedule_id)


# --- the harness --------------------------------------------------------------------------------


@dataclass
class Harness:
    """The app under test: an HTTP client, the sessions and the recorded Temporal triggers."""

    client: httpx2.AsyncClient
    sessions: async_sessionmaker[AsyncSession]
    authenticator: DevAuthenticator
    trigger: FakeScheduleTrigger
    users_file: DevUsersFile

    def headers(self, as_role: str) -> dict[str, str]:
        token = {
            "operator": self.users_file.operator,
            "hunter": self.users_file.hunter,
            "admin": self.users_file.admin,
            "admin2": self.users_file.admin2,
        }[as_role]
        return {"Authorization": f"Bearer {token}"}

    async def get(
        self, path: str, *, as_role: str = "operator", **params: QueryValue
    ) -> httpx2.Response:
        # A `None` parameter is an absent one, as in a query string that leaves it out.
        given = {name: value for name, value in params.items() if value is not None}
        return await self.client.get(f"/api/v1{path}", headers=self.headers(as_role), params=given)

    async def post(
        self, path: str, body: object = None, *, as_role: str = "operator"
    ) -> httpx2.Response:
        return await self.client.post(f"/api/v1{path}", json=body, headers=self.headers(as_role))

    async def put(
        self, path: str, body: object = None, *, as_role: str = "admin"
    ) -> httpx2.Response:
        return await self.client.put(f"/api/v1{path}", json=body, headers=self.headers(as_role))

    async def delete(self, path: str, *, as_role: str = "admin") -> httpx2.Response:
        return await self.client.delete(f"/api/v1{path}", headers=self.headers(as_role))

    async def approve(
        self, accepted: httpx2.Response, *, as_role: str = "admin2"
    ) -> httpx2.Response:
        """A second admin approves the change a 202 answer named (D-36)."""
        assert accepted.status_code == 202, accepted.text
        change_id = accepted.json()["change_id"]
        return await self.post(f"/changes/{change_id}/approve", as_role=as_role)

    async def raw(
        self, method: str, path: str, *, headers: dict[str, str] | None = None
    ) -> httpx2.Response:
        """A request as given: no role header is added and the path is used as it is."""
        return await self.client.request(method, path, headers=headers)

    async def seed(self, *statements: str) -> None:
        """Run raw SQL, for the rows no repository function creates."""
        if not statements:
            return
        async with self.sessions.begin() as session:
            for statement in statements:
                await session.execute(text(statement))

    async def rows(
        self, statement: str, parameters: Mapping[str, object] | None = None
    ) -> list[dict[str, Any]]:
        """The rows a statement returns as dictionaries; `parameters` binds its `:name`s.

        A `SELECT` or an `INSERT ... RETURNING`: an INSERT is committed, since the statement runs
        in its own transaction.
        """
        async with self.sessions.begin() as session:
            result = await session.execute(text(statement), parameters or {})
            return [dict(row) for row in result.mappings()]

    async def one(
        self, statement: str, parameters: Mapping[str, object] | None = None
    ) -> dict[str, Any] | None:
        rows = await self.rows(statement, parameters)
        return rows[0] if rows else None


def build_harness(
    sessions: async_sessionmaker[AsyncSession],
    users_file: DevUsersFile,
    *,
    offense_url_template: str | None = None,
) -> Harness:
    """The app over `sessions`, with the dev users of `users_file` and a fake Temporal."""
    trigger = FakeScheduleTrigger()
    app = build_app(
        sessions=sessions,
        authenticator=DevAuthenticator.from_file(users_file.path),
        schedule_trigger=trigger,
        offense_url_template=offense_url_template,
    )
    return Harness(
        client=httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url="http://api.test"
        ),
        sessions=sessions,
        authenticator=app.state.authenticator,
        trigger=trigger,
        users_file=users_file,
    )


# --- the routes ---------------------------------------------------------------------------------


def api_routes() -> list[tuple[str, APIRoute]]:
    """Every endpoint of the API as (full path, route).

    FastAPI 0.142 keeps an included router as one entry of `app.routes`, so a test that looks
    for `APIRoute`s there finds none. The routers `ais0c_api.routers` includes are read instead,
    and `test_service_and_problems.py` checks that they hold every operation of the schema.
    """
    from ais0c_api.routers import (
        API_PREFIX,
        administration,
        cases,
        catalog,
        changes,
        groups,
        monitoring,
        qa,
    )

    return [
        (API_PREFIX + route.path, route)
        for module in (cases, qa, groups, catalog, changes, administration, monitoring)
        for route in module.router.routes
        if isinstance(route, APIRoute)
    ]


# --- seeding through the repository functions ----------------------------------------------------


async def add_offense(
    sessions: async_sessionmaker[AsyncSession],
    *,
    offense_id: int = OFFENSE_ID,
    rule_ids: Sequence[int] = (100201,),
    description: str = "Excessive Firewall Accepts",
    group_id: str | None = None,
    case_id: str | None = None,
    status: OffenseStatus = OffenseStatus.DONE,
    first_seen_at: datetime = T0,
) -> None:
    """An `offenses_seen` row."""
    async with sessions.begin() as session:
        await add_offense_seen(
            session,
            offense_id=offense_id,
            first_seen_at=first_seen_at,
            last_updated_at=first_seen_at,
            description=description,
            rule_ids=list(rule_ids),
            catalog_mode=CatalogMode.ANALYZE,
            pre_priority=0,
            status=status,
            group_id=group_id,
            case_id=case_id,
        )


async def add_evidence(sessions: async_sessionmaker[AsyncSession]) -> None:
    """The evidence the report and the verification cite.

    Recording the same evidence ID twice is a `DuplicateError`, so a helper that is called twice
    for one database simply leaves the rows it already wrote.
    """
    from ais0c_storage.errors import DuplicateError

    async with sessions.begin() as session:
        for evidence_id in (EVIDENCE_ID, EVIDENCE_ID_2):
            try:
                await record_evidence(session, evidence_ref(evidence_id))
            except DuplicateError:
                pass


async def add_case_writes(
    sessions: async_sessionmaker[AsyncSession],
    *,
    case_id: str = CASE_ID,
    offense_id: int | None = OFFENSE_ID,
    with_notes: bool = True,
    with_notification: bool = True,
) -> None:
    """A QRadar note and an alert e-mail, as the executor records them (T-045)."""
    async with sessions.begin() as session:
        if with_notes and offense_id is not None:
            await record_note(
                session,
                NoteContent.model_validate(
                    {
                        "offense_id": offense_id,
                        "evaluation_no": 1,
                        "run_marker": "7f3a9c",
                        "verdict": "suspicious",
                        "confidence": "medium",
                        "notify_level": "high",
                        "summary_tr": "Dış adresten iç sunucuya SMB bağlantısı kabul edilmiş.",
                        "urgent_events": [urgent_event()],
                        "recommended_actions": ["investigate_further"],
                        "data_gaps": [],
                        "case_url": "https://soc.example.com/cases/case-12345",
                    }
                ),
                case_id=case_id,
                status=NoteStatus.WRITTEN,
                written_at=T1,
            )
        if with_notification:
            await record_notification(
                session,
                email_message(case_id),
                status=NotificationStatus.SENT,
                level=Level.HIGH,
                case_id=case_id,
                sent_at=T1,
            )


class CaseOptions(TypedDict, total=False):
    """The keywords of `open_case`, so `decided_case` forwards them with their types."""

    case_id: str
    source: CaseSource
    offense_id: int | None
    hunt_id: str | None
    group_id: str | None
    sla_due_at: datetime
    decided_at: datetime | None
    verdict: CaseVerdict | None
    floor_level: Level | None
    notify_level: Level | None
    status: CaseStatus
    with_report: bool


async def open_case(
    sessions: async_sessionmaker[AsyncSession],
    *,
    case_id: str = CASE_ID,
    source: CaseSource = CaseSource.OFFENSE,
    offense_id: int | None = OFFENSE_ID,
    hunt_id: str | None = None,
    group_id: str | None = None,
    sla_due_at: datetime = T1,
    decided_at: datetime | None = None,
    verdict: CaseVerdict | None = CaseVerdict.SUSPICIOUS,
    floor_level: Level | None = Level.HIGH,
    notify_level: Level | None = Level.HIGH,
    status: CaseStatus = CaseStatus.RUNNING,
    with_report: bool = True,
) -> None:
    """A case, optionally decided, with the events and recommendations of that decision.

    Deciding with `with_report=False` stores a decision whose Reporting produced nothing, which is
    what a case without a report looks like.
    """
    # A report has to agree with the decision, so it is built from it.
    level = notify_level if notify_level is not None else Level.LOW
    report = (
        case_report(verdict=verdict, notify_level=level)
        if with_report and verdict is not None
        else None
    )
    async with sessions.begin() as session:
        await create_case(
            session,
            case_id=case_id,
            source=source,
            offense_id=offense_id,
            hunt_id=hunt_id,
            group_id=group_id,
            sla_due_at=sla_due_at,
            workflow_id=case_id,
            run_id=f"run-{case_id}",
        )
        if verdict is not None:
            await record_case_decision(
                session,
                case_id,
                verdict=verdict,
                confidence=Confidence.MEDIUM,
                ai_level=Level.HIGH,
                notify_level=level,
                floor_level=floor_level,
                decided_at=decided_at if decided_at is not None else T1,
                report=report,
            )
        if report is not None:
            await replace_urgent_events(session, case_id, 1, list(report.urgent_events))
            await replace_recommendations(session, case_id, 1, list(report.recommendations))


async def decided_case(
    sessions: async_sessionmaker[AsyncSession],
    *,
    note_status: NoteStatus = NoteStatus.WRITTEN,
    notification_status: NotificationStatus = NotificationStatus.SENT,
    with_notes: bool = True,
    with_notification: bool = True,
    with_runs: bool = False,
    **case: Unpack[CaseOptions],
) -> None:
    """A case with a decision, a report, its evidence and the records the executor left.

    Every other keyword goes to `open_case`; the four named ones shape the note and the e-mail, and
    `with_runs` adds the agent runs the steps endpoint reads.
    """
    case_id = case.get("case_id", CASE_ID)
    offense_id = case.get("offense_id", OFFENSE_ID)
    await open_case(sessions, **case)
    await add_evidence(sessions)
    async with sessions.begin() as session:
        if with_notes and isinstance(offense_id, int):
            # (offense_id, run_marker) is unique, so each case gets its own marker.
            await record_note(
                session,
                NoteContent.model_validate(
                    {
                        "offense_id": offense_id,
                        "evaluation_no": 1,
                        "run_marker": f"7f3a9c-{case_id}",
                        "verdict": "suspicious",
                        "confidence": "medium",
                        "notify_level": "high",
                        "summary_tr": "Dış adresten iç sunucuya SMB bağlantısı kabul edilmiş.",
                        "urgent_events": [urgent_event()],
                        "recommended_actions": ["investigate_further"],
                        "data_gaps": [],
                        "case_url": "https://soc.example.com/cases/case-12345",
                    }
                ),
                case_id=case_id,
                status=note_status,
                written_at=T1,
                error="gateway unavailable" if note_status is NoteStatus.FAILED else None,
            )
        if with_notification:
            await record_notification(
                session,
                email_message(case_id),
                status=notification_status,
                level=Level.HIGH,
                case_id=case_id,
                sent_at=T1 if notification_status is NotificationStatus.SENT else None,
                error=(
                    "no recipient group is routed to this level"
                    if notification_status is NotificationStatus.FAILED
                    else None
                ),
            )
        if with_runs:
            await _agent_runs(session, case_id)


async def _agent_runs(session: AsyncSession, case_id: str) -> None:
    """A Triage run with a tool call and a Verification run with its result."""
    triage = f"{case_id}-triage-1"
    await start_agent_run(
        session,
        run_id=triage,
        task=agent_task(case_id, "triage"),
        prompt_version="v1",
        model_alias="soc-fast",
        model_target="lab-model",
        toolset_profile="qradar-triage-read",
        started_at=T0,
    )
    await record_tool_call(
        session,
        run_id=triage,
        intent=tool_intent(triage, case_id),
        policy_decision=PolicyDecision.ALLOW,
        status=ToolStatus.OK,
        latency_ms=120,
        evidence_id=EVIDENCE_ID,
    )
    await finish_agent_run(
        session,
        triage,
        status=RunStatus.COMPLETED,
        result=None,
        tokens=1500,
        tool_calls=1,
        ended_at=T0 + timedelta(seconds=30),
    )
    run_id = f"{case_id}-verification-1"
    await start_agent_run(
        session,
        run_id=run_id,
        task=agent_task(case_id, "verification"),
        prompt_version="v1",
        model_alias="soc-verifier",
        model_target="lab-model",
        toolset_profile="qradar-verify-read",
        started_at=T0 + timedelta(seconds=31),
    )
    await finish_agent_run(
        session,
        run_id,
        status=RunStatus.COMPLETED,
        result=verification_result(),
        tokens=900,
        tool_calls=0,
        ended_at=T0 + timedelta(seconds=90),
    )


async def open_qa_items(
    sessions: async_sessionmaker[AsyncSession],
    case_id: str = CASE_ID,
    reasons: Sequence[QAReason] = (QAReason.LOW_CONFIDENCE,),
) -> list[Any]:
    """Open QA items for a case; returns their IDs."""
    async with sessions.begin() as session:
        rows = await add_qa_items(session, case_id, 1, reasons)
        return [row.id for row in rows]


async def add_group(
    sessions: async_sessionmaker[AsyncSession],
    *,
    group_id: str = GROUP_ID,
    status: GroupStatus = GroupStatus.OPEN,
    offense_ids: Sequence[int] = (5001, 5002),
    window_start: datetime = T0,
    case_id: str | None = None,
) -> None:
    """A group and its offenses, as the intake records them."""
    for offense_id in offense_ids:
        await add_offense(
            sessions,
            offense_id=offense_id,
            description=f"Excessive Firewall Accepts ({offense_id})",
            group_id=group_id,
            status=OffenseStatus.GROUPED,
        )
    async with sessions.begin() as session:
        await create_offense_group(
            session,
            group_id=group_id,
            rule_set_hash="hash-100201",
            window_start=window_start,
            window_end=window_start + timedelta(minutes=10),
            offense_count=len(offense_ids),
            status=status,
        )
        if case_id is not None:
            # The group evaluation's case is attached when the workflow opens it.
            await update_offense_group(session, group_id, case_id=case_id)


async def add_catalog(
    sessions: async_sessionmaker[AsyncSession],
    *,
    rules: Sequence[tuple[int, str]] = ((100201, "Excessive Firewall Accepts"),),
    log_sources: Sequence[tuple[int, str]] = ((2001, "SRV-0001.example.com"),),
) -> None:
    """The catalog as the sync writes it: every entry undefined, in scope and enabled."""
    async with sessions.begin() as session:
        if rules:
            await sync_catalog_rules(
                session,
                [SyncedRule(rule_id, name) for rule_id, name in rules],
                synced_by="knowledge-sync",
                synced_at=T0,
            )
        if log_sources:
            await sync_catalog_log_sources(
                session,
                [
                    SyncedLogSource(source_id, name, "Windows Security")
                    for source_id, name in log_sources
                ],
                synced_by="knowledge-sync",
                synced_at=T0,
            )
