"""The platform's minimal health alarms: the checks, their settings and the syslog channel
(architecture §26, decisions T-23 and T-68; T-032).

`HealthCheck` (`ais0c_workflows.health`) runs four checks on the `soc-batch` queue every few
minutes. Each is an activity that looks at one thing, brings `health_alarms` in line
(`ais0c_activities.health_state`) and returns the notifications that are due:

- `check_intake`: QRadar's newest open-offense update against the platform's. The QRadar read
  is one system run of the pseudo agent `health-check` (D-33) with the `qradar-inventory-read`
  profile, which carries `list_offenses` for this (read only). A QRadar the gateway cannot read
  is its own subject, `qradar_unreachable`, of the same kind.
- `check_log_sources`: the catalog's log sources that are in scope and not missing, against the
  `last_event_time` QRadar lists for them. QRadar's `enabled` field is the log source's
  `qradar_enabled`: a log source disabled there is not expected to send events.
- `check_write_failures`: `failed` notes and `failed` or `rejected` e-mails of the last window.
  `disabled` and `skipped_duplicate` are never failures (T-37).
- `check_executor_worker`: whether any worker polls the `soc-executor` queue. The first run that
  finds none opens the alarm, which is announced once the queue has been empty for the
  threshold, counted from that first sighting.

A failing check raises; the workflow logs it and goes on with the others, and a failure of a
check is not an alarm. The channels are `send_alarm_syslog` (syslog to QRadar, a fixed RFC 5424
template, `ais0c_executor.syslog`) and the executor's `send_email`; `mark_alarm_notified`
records that a notification went out.

| Variable | Default | Meaning |
|---|---|---|
| `AIS0C_HEALTH_INTERVAL_MINUTES` | 5 | How often HealthCheck runs (the Schedule) |
| `AIS0C_HEALTH_INTAKE_LAG_MINUTES` | 15 | QRadar's newest open-offense update may be this much newer than the platform's |
| `AIS0C_HEALTH_LOG_SOURCE_SILENT_MINUTES` | 60 | A log source in scope may send no event this long |
| `AIS0C_HEALTH_WRITE_FAILURES` | 3 | More failed notes (or e-mails) than this in the window alarm |
| `AIS0C_HEALTH_WRITE_FAILURE_WINDOW_MINUTES` | 60 | The window of the count |
| `AIS0C_HEALTH_EXECUTOR_ABSENT_MINUTES` | 5 | The executor queue may have no worker this long |
| `AIS0C_HEALTH_RENOTIFY_HOURS` | 6 | An alarm that stays open is announced again this often |
| `AIS0C_ALARM_SYSLOG_HOST` | none | Syslog server; unset turns syslog off |
| `AIS0C_ALARM_SYSLOG_PORT` | 514 | Its port |
| `AIS0C_ALARM_SYSLOG_PROTOCOL` | `udp` | `udp` or `tcp` (octet-counting frames) |
"""

import logging
import os
from collections.abc import Callable, Collection, Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol, Self
from uuid import UUID

from pydantic import JsonValue
from temporalio import activity
from temporalio.api.enums.v1 import TaskQueueKind, TaskQueueType
from temporalio.api.taskqueue.v1 import TaskQueue
from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest
from temporalio.client import Client

from ais0c_activities.db import SessionFactory
from ais0c_activities.gateway import (
    GatewayProfile,
    SystemRun,
    SystemRunError,
    system_run,
    utc_now,
)
from ais0c_activities.health_state import Finding, reconcile
from ais0c_activities.names import (
    CHECK_EXECUTOR_WORKER,
    CHECK_INTAKE,
    CHECK_LOG_SOURCES,
    CHECK_WRITE_FAILURES,
    MARK_ALARM_NOTIFIED,
    SEND_ALARM_SYSLOG,
)
from ais0c_agents import GatewayClient, GatewayError
from ais0c_contracts import Budget, TimeWindow
from ais0c_executor.email import HealthAlarm
from ais0c_executor.syslog import (
    DEFAULT_PORT,
    SyslogError,
    SyslogProtocol,
    SyslogSender,
    SyslogSettings,
)
from ais0c_storage.enums import HealthAlarmKind
from ais0c_storage.repositories import (
    count_failed_notes,
    count_failed_notifications,
    get_open_health_alarm,
    latest_offense_update,
    list_catalog_log_sources,
    mark_health_alarm_notified,
)

HEALTH_AGENT_ID: Final = "health-check"
HEALTH_CONTEXT: Final = "health-check"
# What the checks read with, besides the catalog sync's lists: `list_log_sources` is one of
# those already, `list_offenses` the one the inventory profile gained for this (T-032).
HEALTH_TOOLS: Final = frozenset({"list_offenses", "list_log_sources"})
# The executor's task queue, the one whose workers are checked.
EXECUTOR_TASK_QUEUE: Final = "soc-executor"
# The subjects of the intake alarm.
INTAKE_SUBJECT: Final = "intake"
QRADAR_UNREACHABLE_SUBJECT: Final = "qradar_unreachable"
# The subjects of the write failure alarm.
NOTES_SUBJECT: Final = "notes"
EMAILS_SUBJECT: Final = "emails"
LOG_SOURCE_PAGE_SIZE: Final = 200
MAX_LOG_SOURCE_PAGES: Final = 500
# What a system run records as the window of the reads; the lists are current state.
HEALTH_RUN_WINDOW: Final = timedelta(hours=1)
HEALTH_BUDGET: Final = Budget(tokens=0, tool_calls=MAX_LOG_SOURCE_PAGES + 1, seconds=300)

INTERVAL_ENV: Final = "AIS0C_HEALTH_INTERVAL_MINUTES"
INTAKE_LAG_ENV: Final = "AIS0C_HEALTH_INTAKE_LAG_MINUTES"
LOG_SOURCE_SILENT_ENV: Final = "AIS0C_HEALTH_LOG_SOURCE_SILENT_MINUTES"
WRITE_FAILURES_ENV: Final = "AIS0C_HEALTH_WRITE_FAILURES"
WRITE_FAILURE_WINDOW_ENV: Final = "AIS0C_HEALTH_WRITE_FAILURE_WINDOW_MINUTES"
EXECUTOR_ABSENT_ENV: Final = "AIS0C_HEALTH_EXECUTOR_ABSENT_MINUTES"
RENOTIFY_ENV: Final = "AIS0C_HEALTH_RENOTIFY_HOURS"
SYSLOG_HOST_ENV: Final = "AIS0C_ALARM_SYSLOG_HOST"
SYSLOG_PORT_ENV: Final = "AIS0C_ALARM_SYSLOG_PORT"
SYSLOG_PROTOCOL_ENV: Final = "AIS0C_ALARM_SYSLOG_PROTOCOL"

_log = logging.getLogger(__name__)


class HealthSettingsError(ValueError):
    """A health setting is not valid."""


@dataclass(frozen=True)
class HealthSettings:
    interval: timedelta = timedelta(minutes=5)
    intake_lag: timedelta = timedelta(minutes=15)
    log_source_silent: timedelta = timedelta(minutes=60)
    write_failures: int = 3
    write_failure_window: timedelta = timedelta(minutes=60)
    executor_absent: timedelta = timedelta(minutes=5)
    renotify: timedelta = timedelta(hours=6)

    def __post_init__(self) -> None:
        durations = (
            self.interval,
            self.intake_lag,
            self.log_source_silent,
            self.write_failure_window,
            self.executor_absent,
            self.renotify,
        )
        if any(duration <= timedelta(0) for duration in durations):
            raise HealthSettingsError("health durations must be positive")
        if self.write_failures < 1:
            raise HealthSettingsError("write_failures must be at least 1")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Self:
        """Settings from `environ` (default `os.environ`); an unset variable keeps its default."""
        env = os.environ if environ is None else environ
        defaults = cls()
        return cls(
            interval=timedelta(minutes=_positive(env, INTERVAL_ENV, _minutes(defaults.interval))),
            intake_lag=timedelta(
                minutes=_positive(env, INTAKE_LAG_ENV, _minutes(defaults.intake_lag))
            ),
            log_source_silent=timedelta(
                minutes=_positive(env, LOG_SOURCE_SILENT_ENV, _minutes(defaults.log_source_silent))
            ),
            write_failures=_positive(env, WRITE_FAILURES_ENV, defaults.write_failures),
            write_failure_window=timedelta(
                minutes=_positive(
                    env, WRITE_FAILURE_WINDOW_ENV, _minutes(defaults.write_failure_window)
                )
            ),
            executor_absent=timedelta(
                minutes=_positive(env, EXECUTOR_ABSENT_ENV, _minutes(defaults.executor_absent))
            ),
            renotify=timedelta(
                hours=_positive(env, RENOTIFY_ENV, int(defaults.renotify.total_seconds() // 3600))
            ),
        )


def load_syslog_settings(environ: Mapping[str, str] | None = None) -> SyslogSettings | None:
    """The alarm syslog's settings, or None when `AIS0C_ALARM_SYSLOG_HOST` is unset (syslog off).
    Raises `HealthSettingsError` for an invalid value."""
    env = os.environ if environ is None else environ
    host = env.get(SYSLOG_HOST_ENV, "").strip()
    if not host:
        return None
    protocol = env.get(SYSLOG_PROTOCOL_ENV, "").strip().lower() or SyslogProtocol.UDP.value
    if protocol not in {item.value for item in SyslogProtocol}:
        raise HealthSettingsError(f"{SYSLOG_PROTOCOL_ENV} must be udp or tcp")
    try:
        return SyslogSettings.model_validate(
            {
                "host": host,
                "port": _positive(env, SYSLOG_PORT_ENV, DEFAULT_PORT),
                "protocol": protocol,
            }
        )
    except ValueError as error:
        raise HealthSettingsError(f"invalid alarm syslog settings: {error}") from None


class TaskQueuePollers(Protocol):
    """What the executor check needs of Temporal: how many workers poll a task queue."""

    async def count(self, task_queue: str) -> int: ...


class TemporalPollers:
    """`TaskQueuePollers` from Temporal's `DescribeTaskQueue`, for activity pollers: the
    executor worker runs activities only.

    The server forgets a poller a few minutes after its last poll, so a queue that lists none
    has had no worker for at least that long.
    """

    def __init__(self, client: Client) -> None:
        self._client = client

    async def count(self, task_queue: str) -> int:
        service = self._client.workflow_service
        queue = TaskQueue(name=task_queue, kind=TaskQueueKind.TASK_QUEUE_KIND_NORMAL)
        response = await service.describe_task_queue(
            DescribeTaskQueueRequest(
                namespace=self._client.namespace,
                task_queue=queue,
                task_queue_type=TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY,
            )
        )
        return len(response.pollers)


class HealthActivities:
    """The health checks and the syslog channel, reading QRadar with `gateway` and `profile`,
    the `qradar-inventory-read` profile and its token's client."""

    def __init__(
        self,
        *,
        sessions: SessionFactory,
        gateway: GatewayClient,
        profile: GatewayProfile,
        pollers: TaskQueuePollers,
        settings: HealthSettings | None = None,
        syslog: SyslogSender | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessions = sessions
        self._gateway = gateway
        self._profile = profile
        self._pollers = pollers
        self._settings = HealthSettings() if settings is None else settings
        self._syslog = syslog
        self._clock = clock

    def activities(self) -> list[Callable[..., object]]:
        return [
            self.check_intake,
            self.check_log_sources,
            self.check_write_failures,
            self.check_executor_worker,
            self.send_alarm_syslog,
            self.mark_alarm_notified,
        ]

    @activity.defn(name=CHECK_INTAKE)
    async def check_intake(self) -> list[HealthAlarm]:
        """Compare QRadar's newest open-offense update with the platform's (criterion 2).

        An alarm opens when the platform's newest `last_updated_at` in `offenses_seen` is more
        than the intake lag behind QRadar's. No open offense in QRadar, or none seen by the
        platform yet, is no alarm: there is nothing the platform missed, or no go-live to
        measure from. A QRadar the gateway cannot read is the subject `qradar_unreachable`.
        """
        now = self._clock()
        findings: list[Finding] = []
        unchecked: set[str] = set()
        qradar_latest: datetime | None = None
        async with self._run(
            "Read QRadar's newest open offense update for the health check."
        ) as run:
            try:
                result = await run.call(
                    "list_offenses",
                    {
                        "filter": "status = 'OPEN'",
                        "fields": "id,last_updated_time",
                        "sort": "-last_updated_time",
                        "limit": 1,
                    },
                    reason="Compare QRadar's newest offense update with the platform's intake.",
                    expected_evidence="The open offense QRadar updated last, with its update time.",
                )
            except (SystemRunError, GatewayError) as error:
                _log.warning(
                    "health check: QRadar cannot be read: %s: %s", type(error).__name__, error
                )
                findings.append(Finding(QRADAR_UNREACHABLE_SUBJECT))
                unchecked.add(INTAKE_SUBJECT)
            else:
                qradar_latest = _newest_update(result.data)
        if not findings and qradar_latest is not None:
            async with self._sessions() as session:
                platform_latest = await latest_offense_update(session)
            if platform_latest is not None:
                lag = qradar_latest - platform_latest
                if lag > self._settings.intake_lag:
                    findings.append(
                        Finding(
                            INTAKE_SUBJECT,
                            {
                                "lag_minutes": _minutes(lag),
                                "threshold": _minutes(self._settings.intake_lag),
                            },
                        )
                    )
        return await self._reconcile(
            HealthAlarmKind.INTAKE_STOPPED, findings, now=now, unchecked=unchecked
        )

    @activity.defn(name=CHECK_LOG_SOURCES)
    async def check_log_sources(self) -> list[HealthAlarm]:
        """Find the in-scope log sources that send no events (criterion 3).

        The catalog decides which log sources count (in scope, not missing from QRadar); QRadar
        says which of them are enabled and when each last sent an event. A log source QRadar no
        longer lists, or lists as disabled, has no alarm: the catalog sync's business. One that
        never sent an event is alarmed too, with `never_sent` in its numbers.
        """
        now = self._clock()
        async with self._sessions() as session:
            watched = {
                row.log_source_id: row.name
                for row in await list_catalog_log_sources(session, in_scope=True, missing=False)
            }
        findings: list[Finding] = []
        if watched:
            async with self._run("Read the last event time of the log sources in scope.") as run:
                listed = await self._read_log_sources(run)
            silent = self._settings.log_source_silent
            for log_source_id, name in sorted(watched.items()):
                enabled, last_event = listed.get(log_source_id, (False, None))
                if not enabled:
                    continue
                counts = {"threshold": _minutes(silent)}
                if last_event is None:
                    findings.append(
                        Finding(str(log_source_id), {"never_sent": 1, **counts}, subject_name=name)
                    )
                elif now - last_event > silent:
                    counts["silent_minutes"] = _minutes(now - last_event)
                    findings.append(Finding(str(log_source_id), counts, subject_name=name))
        return await self._reconcile(HealthAlarmKind.LOG_SOURCE_SILENT, findings, now=now)

    @activity.defn(name=CHECK_WRITE_FAILURES)
    async def check_write_failures(self) -> list[HealthAlarm]:
        """Count the failed notes and e-mails of the last window (criterion 4).

        More than the threshold alarms, notes and e-mails each on their own subject. `disabled`
        and `skipped_duplicate` records are not failures; `failed` notes and `failed` or
        `rejected` e-mails are, the notes and e-mails the executor's given-up calls left included
        (`record_executor_failure`).
        """
        now = self._clock()
        settings = self._settings
        since = now - settings.write_failure_window
        async with self._sessions() as session:
            counted = {
                NOTES_SUBJECT: await count_failed_notes(session, since=since),
                EMAILS_SUBJECT: await count_failed_notifications(session, since=since),
            }
        findings = [
            Finding(
                subject,
                {
                    "failures": failures,
                    "threshold": settings.write_failures,
                    "window_minutes": _minutes(settings.write_failure_window),
                },
            )
            for subject, failures in counted.items()
            if failures > settings.write_failures
        ]
        return await self._reconcile(HealthAlarmKind.WRITE_FAILURES, findings, now=now)

    @activity.defn(name=CHECK_EXECUTOR_WORKER)
    async def check_executor_worker(self) -> list[HealthAlarm]:
        """Look for a worker on the `soc-executor` queue (criterion 5).

        The first run that finds none opens the alarm; the notification goes out once the queue
        has been empty for the threshold, counted from the alarm's opening. A worker that
        returns resolves the alarm, quietly if it was never announced.
        """
        now = self._clock()
        findings: list[Finding] = []
        if await self._pollers.count(EXECUTOR_TASK_QUEUE) == 0:
            async with self._sessions() as session:
                alarm = await get_open_health_alarm(
                    session, HealthAlarmKind.EXECUTOR_ABSENT, EXECUTOR_TASK_QUEUE
                )
            absent = timedelta(0) if alarm is None else now - alarm.opened_at
            findings.append(
                Finding(
                    EXECUTOR_TASK_QUEUE,
                    {
                        "absent_minutes": _minutes(absent),
                        "threshold": _minutes(self._settings.executor_absent),
                    },
                    grace=self._settings.executor_absent,
                )
            )
        return await self._reconcile(HealthAlarmKind.EXECUTOR_ABSENT, findings, now=now)

    @activity.defn(name=SEND_ALARM_SYSLOG)
    async def send_alarm_syslog(self, alarm: HealthAlarm) -> bool:
        """Send `alarm`'s notification to syslog; False when syslog is off or the send failed.

        A failed send is logged and is no reason to stop: the alarm's state is recorded and its
        e-mail goes out either way (criterion 7).
        """
        if self._syslog is None:
            return False
        try:
            await self._syslog.send(alarm, now=self._clock())
        except SyslogError as error:
            _log.warning("health alarm %s: %s", alarm.alarm_id, error)
            return False
        return True

    @activity.defn(name=MARK_ALARM_NOTIFIED)
    async def mark_alarm_notified(self, alarm_id: str) -> None:
        """Record that a notification of the alarm went out: now is its `last_notified_at`."""
        async with self._sessions.begin() as session:
            await mark_health_alarm_notified(session, UUID(alarm_id), now=self._clock())

    def _run(self, objective: str) -> AbstractAsyncContextManager[SystemRun]:
        now = self._clock()
        return system_run(
            sessions=self._sessions,
            gateway=self._gateway,
            profile=self._profile,
            agent_id=HEALTH_AGENT_ID,
            case_id=HEALTH_CONTEXT,
            objective=objective,
            window=TimeWindow(start=now - HEALTH_RUN_WINDOW, end=now),
            budget=HEALTH_BUDGET,
            clock=self._clock,
        )

    async def _reconcile(
        self,
        kind: HealthAlarmKind,
        findings: Sequence[Finding],
        *,
        now: datetime,
        unchecked: Collection[str] = (),
    ) -> list[HealthAlarm]:
        async with self._sessions.begin() as session:
            return await reconcile(
                session,
                kind,
                findings,
                now=now,
                renotify=self._settings.renotify,
                unchecked=unchecked,
            )

    async def _read_log_sources(self, run: SystemRun) -> dict[int, tuple[bool, datetime | None]]:
        """Every log source QRadar lists: (enabled, last event time or None), page by page."""
        listed: dict[int, tuple[bool, datetime | None]] = {}
        offset = 0
        for _ in range(MAX_LOG_SOURCE_PAGES):
            result = await run.call(
                "list_log_sources",
                {
                    "fields": "id,enabled,last_event_time",
                    "sort": "+id",
                    "limit": LOG_SOURCE_PAGE_SIZE,
                    "offset": offset,
                },
                reason="Find the log sources in scope that have stopped sending events.",
                expected_evidence="Each log source's enabled state and last event time.",
            )
            page = result.data
            if not page:
                if result.truncated:
                    raise SystemRunError("list_log_sources: a row is over the gateway's size limit")
                return listed
            for row in page:
                identifier = row.get("id")
                if isinstance(identifier, bool) or not isinstance(identifier, int):
                    raise SystemRunError("list_log_sources returned a row without a usable ID")
                enabled = row.get("enabled")
                listed[identifier] = (enabled is True, _event_time(row.get("last_event_time")))
            offset += len(page)
            if len(page) < LOG_SOURCE_PAGE_SIZE and not result.truncated:
                return listed
        raise SystemRunError(f"list_log_sources: more than {MAX_LOG_SOURCE_PAGES} pages")


def _epoch_ms(value: JsonValue) -> datetime | None:
    """A QRadar time (epoch milliseconds) as a UTC time; None for 0, no value or a bad one."""
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        return None
    return datetime.fromtimestamp(value / 1000, UTC)


def _event_time(value: JsonValue) -> datetime | None:
    return _epoch_ms(value)


def _newest_update(rows: Sequence[Mapping[str, JsonValue]]) -> datetime | None:
    """The update time of QRadar's newest open offense; None for no row. A row without a usable
    time raises: the check cannot tell, and a failed check is logged, not alarmed."""
    if not rows:
        return None
    updated = _epoch_ms(rows[0].get("last_updated_time"))
    if updated is None:
        raise SystemRunError("list_offenses returned a row without a usable last_updated_time")
    return updated


def _minutes(duration: timedelta) -> int:
    return int(duration.total_seconds() // 60)


def _positive(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise HealthSettingsError(f"{name} must be a positive integer") from None
    if value < 1:
        raise HealthSettingsError(f"{name} must be a positive integer")
    return value
