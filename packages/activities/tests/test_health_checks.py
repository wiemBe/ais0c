"""The four health checks and the alarm state (T-032 criteria 2 to 7).

QRadar is the real MCP Policy Gateway in process with the repository's registry, so the calls'
arguments are checked against the tools' schemas and every read is a recorded run of the pseudo
agent `health-check`; only the MCP side is a fake. The database is real, the clock is injected,
and Temporal's task queue is a fake.
"""

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from activity_db import catalog_log_source, gone_from_qradar
from activity_payloads import T0
from catalog_gateway import inventory_client
from pydantic import JsonValue

from ais0c_activities import (
    Finding,
    HealthActivities,
    HealthSettings,
    SessionFactory,
    reconcile,
)
from ais0c_activities.health import (
    EMAILS_SUBJECT,
    EXECUTOR_TASK_QUEUE,
    HEALTH_AGENT_ID,
    HEALTH_CONTEXT,
    INTAKE_SUBJECT,
    NOTES_SUBJECT,
    QRADAR_UNREACHABLE_SUBJECT,
)
from ais0c_contracts import (
    CaseSource,
    CaseVerdict,
    CatalogMode,
    Confidence,
    EmailKind,
    EmailMessage,
    Level,
    NoteContent,
    RunStatus,
)
from ais0c_executor.email import HealthAlarm
from ais0c_executor.syslog import SyslogProtocol, SyslogSender, SyslogSettings
from ais0c_mcp_gateway.upstream import UpstreamFailure, UpstreamOutcome
from ais0c_storage import NotificationStatus
from ais0c_storage.enums import HealthAlarmKind, HealthAlarmStatus, NoteStatus, OffenseStatus
from ais0c_storage.models import AgentRunRow
from ais0c_storage.repositories import (
    add_offense_seen,
    create_case,
    get_health_alarm,
    list_agent_runs,
    list_health_alarms,
    list_open_health_alarms,
    record_note,
    record_note_failure,
    record_notification,
    update_catalog_log_source,
)

pytestmark = pytest.mark.anyio

# The clock of the checks, and the time QRadar says it last heard from things.
NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
SETTINGS = HealthSettings()
Row = dict[str, JsonValue]


def ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


@dataclass
class QRadar:
    """The fork's two tools the checks read, over fixed rows; records every call."""

    offenses: list[Row] = field(default_factory=list[Row])
    log_sources: list[Row] = field(default_factory=list[Row])
    failing: set[str] = field(default_factory=set[str])
    calls: list[tuple[str, dict[str, JsonValue]]] = field(
        default_factory=list[tuple[str, dict[str, JsonValue]]]
    )

    async def call_tool(self, name: str, arguments: Mapping[str, JsonValue]) -> UpstreamOutcome:
        self.calls.append((name, dict(arguments)))
        if name in self.failing:
            return UpstreamOutcome.failed(UpstreamFailure.ERROR, "QRadar answered HTTP 503")
        if name == "list_offenses":
            return UpstreamOutcome.ok({"offenses": list[JsonValue](self.offenses[:1])})
        assert name == "list_log_sources"
        rows = sorted(self.log_sources, key=lambda row: int(str(row["id"])))
        offset, limit = int(str(arguments["offset"])), int(str(arguments["limit"]))
        return UpstreamOutcome.ok({"items": list[JsonValue](rows[offset : offset + limit])})

    def calls_of(self, name: str) -> list[dict[str, JsonValue]]:
        return [arguments for tool, arguments in self.calls if tool == name]


class Pollers:
    """Temporal's view of a task queue: how many workers poll it, as the test says."""

    def __init__(self, count: int = 1) -> None:
        self.workers = count
        self.asked: list[str] = []

    async def count(self, task_queue: str) -> int:
        self.asked.append(task_queue)
        return self.workers


async def health(
    sessions: SessionFactory,
    qradar: QRadar | None = None,
    *,
    now: datetime = NOW,
    settings: HealthSettings = SETTINGS,
    pollers: Pollers | None = None,
    syslog: SyslogSender | None = None,
) -> HealthActivities:
    gateway, profile = await inventory_client(sessions, qradar or QRadar(), now=lambda: now)
    return HealthActivities(
        sessions=sessions,
        gateway=gateway,
        profile=profile,
        pollers=pollers or Pollers(),
        settings=settings,
        syslog=syslog,
        clock=lambda: now,
    )


async def seen_offense(sessions: SessionFactory, offense_id: int, updated: datetime) -> None:
    async with sessions.begin() as session:
        await add_offense_seen(
            session,
            offense_id=offense_id,
            first_seen_at=updated,
            last_updated_at=updated,
            description="AIS0C LAB - Excessive Firewall Accepts",
            rule_ids=[100201],
            catalog_mode=CatalogMode.ANALYZE,
            pre_priority=3,
            status=OffenseStatus.DONE,
        )


def offense_row(offense_id: int, updated: datetime) -> Row:
    return {"id": offense_id, "last_updated_time": ms(updated)}


async def open_alarms(
    sessions: SessionFactory, kind: HealthAlarmKind | None = None
) -> dict[str, dict[str, JsonValue]]:
    """{subject: details} of the open alarms."""
    async with sessions() as session:
        return {
            row.subject: dict(row.details) if isinstance(row.details, dict) else {}
            for row in await list_open_health_alarms(session, kind)
        }


async def announced(
    sessions: SessionFactory, activities: HealthActivities, *notices: HealthAlarm
) -> None:
    """What the workflow does once a notification went out."""
    for notice in notices:
        await activities.mark_alarm_notified(notice.alarm_id)


# --- criterion 2: intake -----------------------------------------------------------------------


async def test_an_intake_that_is_behind_qradar_opens_an_alarm(sessions: SessionFactory) -> None:
    await seen_offense(sessions, 101, NOW - timedelta(minutes=40))
    qradar = QRadar(offenses=[offense_row(105, NOW - timedelta(minutes=5))])

    notices = await (await health(sessions, qradar)).check_intake()

    [notice] = notices
    assert (notice.alarm_kind, notice.subject, notice.status, notice.notification_no) == (
        "intake_stopped",
        INTAKE_SUBJECT,
        "open",
        1,
    )
    assert notice.counts == {"lag_minutes": 35, "threshold": 15}
    assert set(await open_alarms(sessions)) == {INTAKE_SUBJECT}
    # The read: QRadar's newest open offense update, through the gateway.
    assert qradar.calls_of("list_offenses") == [
        {
            "filter": "status = 'OPEN'",
            "fields": "id,last_updated_time",
            "sort": "-last_updated_time",
            "limit": 1,
        }
    ]


async def test_an_intake_that_has_caught_up_has_no_alarm(sessions: SessionFactory) -> None:
    await seen_offense(sessions, 101, NOW - timedelta(minutes=12))
    qradar = QRadar(offenses=[offense_row(105, NOW - timedelta(minutes=1))])

    assert await (await health(sessions, qradar)).check_intake() == []
    assert await open_alarms(sessions) == {}


async def test_the_lag_must_be_over_the_threshold(sessions: SessionFactory) -> None:
    await seen_offense(sessions, 101, NOW - timedelta(minutes=30))
    qradar = QRadar(offenses=[offense_row(105, NOW - timedelta(minutes=15))])

    assert await (await health(sessions, qradar)).check_intake() == []


async def test_an_empty_qradar_has_no_intake_alarm(sessions: SessionFactory) -> None:
    await seen_offense(sessions, 101, NOW - timedelta(hours=3))

    assert await (await health(sessions, QRadar())).check_intake() == []
    assert await open_alarms(sessions) == {}


async def test_a_platform_that_has_seen_no_offense_has_no_intake_alarm(
    sessions: SessionFactory,
) -> None:
    qradar = QRadar(offenses=[offense_row(105, NOW - timedelta(minutes=1))])

    assert await (await health(sessions, qradar)).check_intake() == []


async def test_an_unreachable_qradar_opens_its_own_subject_of_the_same_kind(
    sessions: SessionFactory,
) -> None:
    qradar = QRadar(failing={"list_offenses"})

    [notice] = await (await health(sessions, qradar)).check_intake()

    assert (notice.alarm_kind, notice.subject) == ("intake_stopped", QRADAR_UNREACHABLE_SUBJECT)
    assert set(await open_alarms(sessions, HealthAlarmKind.INTAKE_STOPPED)) == {
        QRADAR_UNREACHABLE_SUBJECT
    }


async def test_an_open_intake_alarm_is_neither_kept_nor_closed_while_qradar_is_unreachable(
    sessions: SessionFactory,
) -> None:
    await seen_offense(sessions, 101, NOW - timedelta(minutes=40))
    behind = QRadar(offenses=[offense_row(105, NOW - timedelta(minutes=5))])
    await (await health(sessions, behind)).check_intake()

    await (await health(sessions, QRadar(failing={"list_offenses"}))).check_intake()
    assert set(await open_alarms(sessions)) == {INTAKE_SUBJECT, QRADAR_UNREACHABLE_SUBJECT}

    # QRadar is back and the platform has caught up: both alarms close.
    await seen_offense(sessions, 106, NOW - timedelta(minutes=2))
    current = QRadar(offenses=[offense_row(106, NOW - timedelta(minutes=2))])
    await (await health(sessions, current)).check_intake()
    assert await open_alarms(sessions) == {}


async def test_the_intake_read_is_a_run_of_the_health_check_pseudo_agent(
    sessions: SessionFactory,
) -> None:
    await seen_offense(sessions, 101, NOW - timedelta(minutes=40))
    qradar = QRadar(offenses=[offense_row(105, NOW)])

    await (await health(sessions, qradar)).check_intake()

    async with sessions() as session:
        [run] = await list_agent_runs(session, case_id=HEALTH_CONTEXT)
    assert isinstance(run, AgentRunRow)
    assert (run.agent_id, run.toolset_profile, run.status, run.tool_calls) == (
        HEALTH_AGENT_ID,
        "qradar-inventory-read",
        RunStatus.COMPLETED,
        1,
    )


# --- criterion 3: log sources ------------------------------------------------------------------


def log_source(log_source_id: int, last_event: datetime | None, *, enabled: bool = True) -> Row:
    return {
        "id": log_source_id,
        "enabled": enabled,
        "last_event_time": 0 if last_event is None else ms(last_event),
    }


async def test_log_sources_that_send_nothing_alarm_and_the_others_do_not(
    sessions: SessionFactory,
) -> None:
    for number in range(1, 8):
        await catalog_log_source(sessions, number, "Linux OS")
    async with sessions.begin() as session:
        await update_catalog_log_source(
            session,
            3,
            description=None,
            owner=None,
            criticality=None,
            in_scope=False,
            context_note=None,
            telemetry_classes=None,
            updated_by="admin",
            updated_at=T0,
        )
    await gone_from_qradar(sessions, log_source_ids=[5])
    silent, recent = NOW - timedelta(minutes=75), NOW - timedelta(minutes=10)
    qradar = QRadar(
        log_sources=[
            log_source(1, silent),  # silent: alarm
            log_source(2, recent),  # sending
            log_source(3, silent),  # out of scope
            log_source(4, silent, enabled=False),  # disabled in QRadar
            log_source(5, silent),  # missing from the catalog's view of QRadar
            log_source(6, None),  # never sent an event: alarm
            # 7 is in the catalog but QRadar does not list it
        ]
    )

    notices = await (await health(sessions, qradar)).check_log_sources()

    assert {notice.subject for notice in notices} == {"1", "6"}
    by_subject = {notice.subject: notice for notice in notices}
    assert by_subject["1"].counts == {"silent_minutes": 75, "threshold": 60}
    assert by_subject["1"].subject_name == "Log source 1"
    assert by_subject["6"].counts == {"never_sent": 1, "threshold": 60}
    assert {notice.alarm_kind for notice in notices} == {"log_source_silent"}
    assert set(await open_alarms(sessions)) == {"1", "6"}
    [read] = qradar.calls_of("list_log_sources")
    assert (read["fields"], read["sort"]) == ("id,enabled,last_event_time", "+id")


async def test_a_log_source_that_starts_sending_closes_its_alarm(
    sessions: SessionFactory,
) -> None:
    await catalog_log_source(sessions, 1, "Linux OS")
    silent = QRadar(log_sources=[log_source(1, NOW - timedelta(hours=2))])
    activities = await health(sessions, silent)
    [opened] = await activities.check_log_sources()
    await announced(sessions, activities, opened)

    sending = QRadar(log_sources=[log_source(1, NOW - timedelta(minutes=1))])
    [resolved] = await (await health(sessions, sending)).check_log_sources()

    assert (resolved.subject, resolved.status, resolved.notification_no) == ("1", "resolved", 2)
    assert await open_alarms(sessions) == {}
    async with sessions() as session:
        [row] = await list_health_alarms(session)
    assert (row.status, row.resolved_at) == (HealthAlarmStatus.RESOLVED, NOW)


async def test_the_log_source_check_reads_every_page(sessions: SessionFactory) -> None:
    await catalog_log_source(sessions, 449, "Linux OS")
    rows = [log_source(number, NOW) for number in range(1, 450)]
    rows[-1] = log_source(449, NOW - timedelta(hours=3))
    qradar = QRadar(log_sources=rows)

    notices = await (await health(sessions, qradar)).check_log_sources()

    assert [notice.subject for notice in notices] == ["449"]
    assert [call["offset"] for call in qradar.calls_of("list_log_sources")] == [0, 200, 400]


async def test_no_catalog_log_source_in_scope_asks_qradar_nothing(
    sessions: SessionFactory,
) -> None:
    qradar = QRadar()

    assert await (await health(sessions, qradar)).check_log_sources() == []
    assert qradar.calls == []


async def test_an_unreachable_qradar_fails_the_log_source_check_without_an_alarm(
    sessions: SessionFactory,
) -> None:
    await catalog_log_source(sessions, 1, "Linux OS")
    qradar = QRadar(failing={"list_log_sources"})

    with pytest.raises(Exception, match="list_log_sources"):
        await (await health(sessions, qradar)).check_log_sources()
    assert await open_alarms(sessions) == {}


# --- criterion 4: failed notes and e-mails -----------------------------------------------------


REAL_NOW = datetime.now(UTC)


async def failed_note(sessions: SessionFactory, offense_id: int, *, at: datetime) -> None:
    case_id = f"case-{offense_id}"
    async with sessions.begin() as session:
        await create_case(
            session,
            case_id=case_id,
            source=CaseSource.OFFENSE,
            offense_id=offense_id,
            sla_due_at=T0,
            workflow_id=case_id,
            run_id="run-1",
        )
        await record_note_failure(
            session,
            case_id=case_id,
            offense_id=offense_id,
            evaluation_no=1,
            run_marker="a" * 12,
            written_at=at,
            error="executor_unavailable",
        )


async def email_row(
    sessions: SessionFactory, number: int, status: NotificationStatus, *, kind: str = "case_alert"
) -> None:
    message = EmailMessage(
        kind=EmailKind(kind),
        recipients=["soc-1@example.com"],
        subject="[AI-SOC] test",
        template_id=kind,
        fields={},
        attachments=[],
        idempotency_key=f"{kind}:case-{number}:1",
    )
    error = (
        "relay down" if status in {NotificationStatus.FAILED, NotificationStatus.REJECTED} else None
    )
    async with sessions.begin() as session:
        await record_notification(
            session,
            message,
            status=status,
            level=Level.HIGH,
            case_id=f"case-{number}",
            sent_at=REAL_NOW if status is NotificationStatus.SENT else None,
            error=error,
        )


async def test_more_failed_notes_than_the_threshold_alarm(sessions: SessionFactory) -> None:
    for number in range(1, 5):
        await failed_note(sessions, number, at=REAL_NOW)

    [notice] = await (await health(sessions, now=REAL_NOW)).check_write_failures()

    assert (notice.alarm_kind, notice.subject) == ("write_failures", NOTES_SUBJECT)
    assert notice.counts == {"failures": 4, "threshold": 3, "window_minutes": 60}


async def test_a_threshold_of_failures_is_not_over_it(sessions: SessionFactory) -> None:
    for number in range(1, 4):
        await failed_note(sessions, number, at=REAL_NOW)

    assert await (await health(sessions, now=REAL_NOW)).check_write_failures() == []


async def test_failed_notes_outside_the_window_do_not_count(sessions: SessionFactory) -> None:
    for number in range(1, 8):
        await failed_note(sessions, number, at=REAL_NOW - timedelta(hours=2))

    assert await (await health(sessions, now=REAL_NOW)).check_write_failures() == []


async def test_failed_and_rejected_emails_alarm_on_their_own_subject(
    sessions: SessionFactory,
) -> None:
    for number, status in enumerate(
        [NotificationStatus.FAILED] * 2 + [NotificationStatus.REJECTED] * 2, start=1
    ):
        await email_row(sessions, number, status)

    [notice] = await (await health(sessions, now=REAL_NOW)).check_write_failures()

    assert (notice.alarm_kind, notice.subject) == ("write_failures", EMAILS_SUBJECT)
    assert notice.counts["failures"] == 4


def disabled_note(offense_id: int) -> NoteContent:
    return NoteContent(
        offense_id=offense_id,
        evaluation_no=1,
        run_marker=f"{offense_id:012d}",
        verdict=CaseVerdict.SUSPICIOUS,
        confidence=Confidence.MEDIUM,
        notify_level=Level.HIGH,
        summary_tr="Özet.",
        urgent_events=[],
        recommended_actions=[],
        data_gaps=[],
        case_url=f"https://ais0c.example.com/cases/case-{offense_id}",
    )


async def test_ten_disabled_records_do_not_alarm(sessions: SessionFactory) -> None:
    """Shadow mode holds every write back; that is not a failure (T-37)."""
    for number in range(1, 11):
        await email_row(sessions, number, NotificationStatus.DISABLED)
    async with sessions.begin() as session:
        for number in range(1, 11):
            offense_id = 900 + number
            case_id = f"case-{offense_id}"
            await create_case(
                session,
                case_id=case_id,
                source=CaseSource.OFFENSE,
                offense_id=offense_id,
                sla_due_at=T0,
                workflow_id=case_id,
                run_id="run-1",
            )
            await record_note(
                session,
                disabled_note(offense_id),
                case_id=case_id,
                status=NoteStatus.DISABLED,
                written_at=REAL_NOW,
            )

    assert await (await health(sessions, now=REAL_NOW)).check_write_failures() == []
    assert await open_alarms(sessions) == {}


async def test_the_write_failure_alarm_closes_when_the_window_empties(
    sessions: SessionFactory,
) -> None:
    for number in range(1, 5):
        await failed_note(sessions, number, at=REAL_NOW)
    [opened] = await (await health(sessions, now=REAL_NOW)).check_write_failures()
    activities = await health(sessions, now=REAL_NOW + timedelta(hours=2))
    await announced(sessions, await health(sessions, now=REAL_NOW), opened)

    [resolved] = await activities.check_write_failures()

    assert (resolved.subject, resolved.status) == (NOTES_SUBJECT, "resolved")


# --- criterion 5: the executor worker ----------------------------------------------------------


async def test_an_executor_queue_without_a_worker_alarms_after_the_threshold(
    sessions: SessionFactory,
) -> None:
    pollers = Pollers(0)
    start = NOW

    # The first run that finds the queue empty opens the alarm and says nothing yet.
    assert await (await health(sessions, now=start, pollers=pollers)).check_executor_worker() == []
    assert set(await open_alarms(sessions)) == {EXECUTOR_TASK_QUEUE}
    # A short absence, within the threshold.
    soon = start + timedelta(minutes=3)
    assert await (await health(sessions, now=soon, pollers=pollers)).check_executor_worker() == []
    # Five minutes after the first sighting: the notification.
    later = start + timedelta(minutes=6)
    [notice] = await (await health(sessions, now=later, pollers=pollers)).check_executor_worker()

    assert (notice.alarm_kind, notice.subject, notice.status) == (
        "executor_absent",
        EXECUTOR_TASK_QUEUE,
        "open",
    )
    assert notice.counts == {"absent_minutes": 6, "threshold": 5}
    assert notice.opened_at == start
    assert set(pollers.asked) == {EXECUTOR_TASK_QUEUE}


async def test_a_worker_that_is_there_has_no_alarm(sessions: SessionFactory) -> None:
    assert await (await health(sessions, pollers=Pollers(2))).check_executor_worker() == []
    assert await open_alarms(sessions) == {}


async def test_an_absence_that_ends_within_the_threshold_closes_quietly(
    sessions: SessionFactory,
) -> None:
    await (await health(sessions, now=NOW, pollers=Pollers(0))).check_executor_worker()

    back = await health(sessions, now=NOW + timedelta(minutes=2), pollers=Pollers(1))
    assert await back.check_executor_worker() == []
    assert await open_alarms(sessions) == {}


async def test_the_return_of_the_worker_resolves_an_announced_alarm(
    sessions: SessionFactory,
) -> None:
    pollers = Pollers(0)
    await (await health(sessions, now=NOW, pollers=pollers)).check_executor_worker()
    opening = await health(sessions, now=NOW + timedelta(minutes=6), pollers=pollers)
    [opened] = await opening.check_executor_worker()
    await announced(sessions, opening, opened)

    back = await health(sessions, now=NOW + timedelta(minutes=9), pollers=Pollers(1))
    [resolved] = await back.check_executor_worker()

    assert (resolved.status, resolved.notification_no) == ("resolved", 2)


# --- criterion 6: state and repeats ------------------------------------------------------------


async def test_an_alarm_is_announced_once_reminded_on_the_interval_and_resolved(
    sessions: SessionFactory,
) -> None:
    """Time-skipping on the injected clock: open, nothing in between, a reminder after the
    interval, the resolved notice, and a new alarm for the same subject."""
    await catalog_log_source(sessions, 1, "Linux OS")
    silent = QRadar(log_sources=[log_source(1, NOW - timedelta(hours=2))])

    async def run(at: datetime, qradar: QRadar = silent) -> list[HealthAlarm]:
        activities = await health(
            sessions, qradar, now=at, settings=HealthSettings(renotify=timedelta(hours=6))
        )
        notices = await activities.check_log_sources()
        await announced(sessions, activities, *notices)
        return notices

    [first] = await run(NOW)
    assert (first.status, first.notification_no) == ("open", 1)
    # Still silent in between: no second notification, however often the check runs.
    for minutes in (5, 60, 5 * 60 + 55):
        assert await run(NOW + timedelta(minutes=minutes)) == []
    # Six hours after the announcement: the reminder.
    [reminder] = await run(NOW + timedelta(hours=6))
    assert (reminder.status, reminder.notification_no) == ("reminder", 2)
    assert reminder.alarm_id == first.alarm_id
    assert await run(NOW + timedelta(hours=7)) == []
    # It sends again: the alarm closes with a notice of its own.
    sending = QRadar(log_sources=[log_source(1, NOW + timedelta(hours=8, minutes=30))])
    [resolved] = await run(NOW + timedelta(hours=8), sending)
    assert (resolved.status, resolved.notification_no) == ("resolved", 3)
    assert await run(NOW + timedelta(hours=9), sending) == []
    # Silent again: a new alarm, announced as a new one.
    [again] = await run(NOW + timedelta(days=2), QRadar(log_sources=[log_source(1, NOW)]))
    assert (again.status, again.notification_no) == ("open", 1)
    assert again.alarm_id != first.alarm_id
    async with sessions() as session:
        history = await list_health_alarms(session, subject="1")
    assert [row.status for row in history] == [HealthAlarmStatus.RESOLVED, HealthAlarmStatus.OPEN]


async def test_a_notification_that_was_not_recorded_comes_up_again_with_the_same_number(
    sessions: SessionFactory,
) -> None:
    await catalog_log_source(sessions, 1, "Linux OS")
    silent = QRadar(log_sources=[log_source(1, NOW - timedelta(hours=2))])

    [first] = await (await health(sessions, silent)).check_log_sources()
    [second] = await (
        await health(sessions, silent, now=NOW + timedelta(minutes=5))
    ).check_log_sources()

    assert (second.alarm_id, second.status, second.notification_no) == (
        first.alarm_id,
        "open",
        1,
    )
    # So the e-mail's idempotency key is the same and it goes out once.
    assert second.idempotency_key == first.idempotency_key


async def test_two_overlapping_checks_open_one_alarm(sessions: SessionFactory) -> None:
    await catalog_log_source(sessions, 1, "Linux OS")
    silent = QRadar(log_sources=[log_source(1, NOW - timedelta(hours=2))])
    activities = await health(sessions, silent)

    await asyncio.gather(activities.check_log_sources(), activities.check_log_sources())

    async with sessions() as session:
        assert len(await list_health_alarms(session, subject="1")) == 1


async def test_reconcile_on_its_own_resolves_what_the_check_no_longer_finds(
    sessions: SessionFactory,
) -> None:
    async with sessions.begin() as session:
        due = await reconcile(
            session,
            HealthAlarmKind.LOG_SOURCE_SILENT,
            [Finding("1"), Finding("2")],
            now=NOW,
            renotify=timedelta(hours=6),
        )
    assert {notice.subject for notice in due} == {"1", "2"}

    async with sessions.begin() as session:
        await reconcile(
            session,
            HealthAlarmKind.LOG_SOURCE_SILENT,
            [Finding("2")],
            now=NOW + timedelta(minutes=5),
            renotify=timedelta(hours=6),
        )
    assert set(await open_alarms(sessions)) == {"2"}


# --- criterion 7: the syslog channel -----------------------------------------------------------


@asynccontextmanager
async def udp_listener() -> AsyncIterator[tuple[int, asyncio.Queue[bytes]]]:
    received: asyncio.Queue[bytes] = asyncio.Queue()

    class Listener(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr: object) -> None:
            received.put_nowait(data)

    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(Listener, local_addr=("127.0.0.1", 0))
    try:
        yield transport.get_extra_info("sockname")[1], received
    finally:
        transport.close()


def an_alarm(**changes: object) -> HealthAlarm:
    values: dict[str, object] = {
        "alarm_id": "0193a5c2-7b4e-7d1a-8c3f-5e2b9a1d4f60",
        "alarm_kind": "executor_absent",
        "subject": EXECUTOR_TASK_QUEUE,
        "status": "open",
        "notification_no": 1,
        "opened_at": NOW,
        "counts": {"absent_minutes": 6},
    }
    return HealthAlarm.model_validate(values | changes)


async def test_the_syslog_activity_sends_the_notification(sessions: SessionFactory) -> None:
    async with udp_listener() as (port, received):
        sender = SyslogSender(SyslogSettings(host="127.0.0.1", port=port))
        activities = await health(sessions, syslog=sender)

        assert await activities.send_alarm_syslog(an_alarm()) is True
        datagram = await asyncio.wait_for(received.get(), 5)

    assert datagram.startswith(b"<131>1 2026-10-07T12:00:00.000Z - ais0c - executor_absent [")


async def test_syslog_off_sends_nothing(sessions: SessionFactory) -> None:
    assert await (await health(sessions)).send_alarm_syslog(an_alarm()) is False


async def test_a_syslog_failure_stops_nothing_and_the_state_is_still_recorded(
    sessions: SessionFactory,
) -> None:
    """The TCP server is gone: the activity says False instead of raising, and the alarm's
    notification is recorded as usual."""
    server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()
    sender = SyslogSender(
        SyslogSettings(host="127.0.0.1", port=port, protocol=SyslogProtocol.TCP, timeout=2)
    )
    await seen_offense(sessions, 101, NOW - timedelta(minutes=40))
    qradar = QRadar(offenses=[offense_row(105, NOW)])
    activities = await health(sessions, qradar, syslog=sender)
    [notice] = await activities.check_intake()

    assert await activities.send_alarm_syslog(notice) is False
    await activities.mark_alarm_notified(notice.alarm_id)

    async with sessions() as session:
        row = await get_health_alarm(session, UUID(notice.alarm_id))
    assert row is not None
    assert (row.last_notified_at, row.details["notifications"]) == (NOW, 1)  # type: ignore[index]
