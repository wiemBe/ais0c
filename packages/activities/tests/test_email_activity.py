"""The `send_email` activity (T-020 criteria 1 and 4): the relay's settings come from the
environment, and a retried activity sends one e-mail.

The relay is a fake that keeps what it was given; the database is real. The e-mail itself is
tested in packages/executor/tests/test_email_*.py.
"""

import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import insert, select
from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment, WorkflowEnvironment
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from ais0c_activities import (
    SEND_EMAIL,
    EmailActivities,
    RuntimeConfigError,
    SessionFactory,
    load_email_runtime,
    load_smtp_settings,
)
from ais0c_activities.email import (
    SMTP_CA_FILE_ENV,
    SMTP_FROM_ENV,
    SMTP_HOST_ENV,
    SMTP_PORT_ENV,
    SMTP_TIMEOUT_ENV,
    SMTP_TLS_ENV,
    SMTP_USERNAME_ENV,
)
from ais0c_contracts import (
    CaseVerdict,
    Confidence,
    EmailKind,
    EmailMessage,
    Level,
    NoteContent,
)
from ais0c_executor.common import EXECUTOR_SECRETS_DIR_ENV
from ais0c_executor.email import (
    CaseAlert,
    EmailOutcome,
    EmailRequest,
    EmailResult,
    EmailTransportError,
    GroupAlert,
    HealthAlarm,
    SendReceipt,
    TlsMode,
)
from ais0c_storage import ActorKind, NotificationStatus, PlatformFlag
from ais0c_storage.models import (
    AllowedEmailDomainRow,
    NotificationRecipientRow,
    NotificationRow,
)
from ais0c_storage.repositories import set_platform_flag

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 2, 11, 6, tzinfo=UTC)
OPERATORS = ("soc-1@example.com", "soc-2@example.com")
PASSWORD = "relay-test-password"  # noqa: S105 - a test value
CASE_URL = "https://ais0c.example.com/cases/case-4711"


def alert(evaluation_no: int = 1, level: Level = Level.HIGH) -> CaseAlert:
    return CaseAlert(
        case_id="case-4711",
        offense_name="Multiple Login Failures Followed By Success",
        evaluated_at=NOW,
        content=NoteContent(
            offense_id=4711,
            evaluation_no=evaluation_no,
            run_marker="5e1f00",
            verdict=CaseVerdict.SUSPICIOUS,
            confidence=Confidence.MEDIUM,
            notify_level=level,
            summary_tr="Aynı hesapla çok sayıda başarısız oturumun ardından başarılı oturum.",
            urgent_events=[],
            recommended_actions=[],
            data_gaps=[],
            case_url=CASE_URL,
        ),
    )


def group() -> GroupAlert:
    return GroupAlert(
        case_id="group-G-0123456789ab-20261002T110000Z",
        group_id="G-0123456789ab-20261002T110000Z",
        title="Multiple Login Failures for the Same User",
        offense_count=37,
        evaluation_no=1,
        evaluated_at=NOW,
        verdict=CaseVerdict.SUSPICIOUS,
        confidence=Confidence.MEDIUM,
        notify_level=Level.CRITICAL,
        summary_tr="Aynı kural 37 farklı kaynak için offense açtı.",
        urgent_events=[],
        recommended_actions=[],
        case_url="https://ais0c.example.com/cases/group-G-0123456789ab-20261002T110000Z",
    )


class Relay:
    """Stands in for the SMTP relay: keeps what it took; `failures` are raised first."""

    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []
        self.attempts = 0
        self.failures: list[EmailTransportError] = []

    @asynccontextmanager
    async def connect(self) -> AsyncIterator["Relay"]:
        yield self

    async def send(self, message: EmailMessage, body: str) -> SendReceipt:
        self.attempts += 1
        if self.failures:
            raise self.failures.pop(0)
        assert body.startswith(("AI-SOC: bildirim seviyesi", "AI-SOC platform sağlık alarmı"))
        self.sent.append(message)
        return SendReceipt(message_id=f"<relay.{len(self.sent)}@example.com>")


@pytest.fixture
def relay() -> Relay:
    return Relay()


@pytest.fixture
async def activities(sessions: SessionFactory, relay: Relay) -> EmailActivities:
    """Operators in an allowed domain and writes switched on."""
    async with sessions.begin() as session:
        await session.execute(insert(AllowedEmailDomainRow), [{"domain": "example.com"}])
        await session.execute(
            insert(NotificationRecipientRow),
            [{"list_name": "operators", "email": email} for email in OPERATORS],
        )
        await set_platform_flag(
            session,
            PlatformFlag.WRITES_ENABLED,
            enabled=True,
            reason="Canary starts.",
            actor_kind=ActorKind.USER,
            actor_id="admin01",
        )
    return EmailActivities(sessions=sessions, transport=relay, clock=lambda: NOW)


async def recorded(sessions: SessionFactory) -> list[NotificationRow]:
    async with sessions() as session:
        return list(await session.scalars(select(NotificationRow)))


async def test_the_activity_sends_an_alert(activities: EmailActivities, relay: Relay) -> None:
    outcome = await ActivityEnvironment().run(activities.send_email, alert())

    assert outcome == EmailOutcome(
        result=EmailResult.SENT, kind=EmailKind.CASE_ALERT, idempotency_key="case_alert:case-4711:1"
    )
    assert [message.recipients for message in relay.sent] == [list(OPERATORS)]


async def test_an_email_that_cannot_be_built_fails_without_a_retry(
    activities: EmailActivities, relay: Relay
) -> None:
    valid = alert()
    unchecked = CaseAlert.model_construct(
        case_id=valid.case_id,
        offense_name=valid.offense_name,
        evaluated_at=valid.evaluated_at,
        content=valid.content.model_copy(update={"case_url": "javascript:alert(1)"}),
    )

    with pytest.raises(ApplicationError) as raised:
        await ActivityEnvironment().run(activities.send_email, unchecked)

    assert raised.value.type == "InvalidEmail"
    assert raised.value.non_retryable is True
    assert relay.attempts == 0


async def test_a_relay_failure_fails_the_attempt(
    activities: EmailActivities, relay: Relay, sessions: SessionFactory
) -> None:
    relay.failures = [EmailTransportError("send: the relay answered 451 later", retryable=True)]

    with pytest.raises(EmailTransportError, match="451"):
        await ActivityEnvironment().run(activities.send_email, alert())

    assert [row.status for row in await recorded(sessions)] == [NotificationStatus.FAILED]


# --- through Temporal ------------------------------------------------------------------------


@workflow.defn(sandboxed=False)
class SendEmailWorkflow:
    """Calls the activity as a case workflow would (T-026), with retries."""

    @workflow.run
    async def run(self, request: CaseAlert | GroupAlert | HealthAlarm) -> EmailOutcome:
        return await workflow.execute_activity(
            SEND_EMAIL,
            request,
            result_type=EmailOutcome,
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(milliseconds=100), maximum_attempts=3
            ),
        )


async def run_workflow(
    activity_functions: list, request: CaseAlert | GroupAlert | HealthAlarm
) -> EmailOutcome:
    async with (
        await WorkflowEnvironment.start_time_skipping(
            data_converter=pydantic_data_converter
        ) as env,
        Worker(
            env.client,
            task_queue="t020-email",
            workflows=[SendEmailWorkflow],
            activities=activity_functions,
            workflow_runner=UnsandboxedWorkflowRunner(),
        ),
    ):
        return await env.client.execute_workflow(
            SendEmailWorkflow.run,
            request,
            id=f"t020-{secrets.token_hex(4)}",
            task_queue="t020-email",
        )


async def test_a_retried_activity_sends_one_email(
    activities: EmailActivities, relay: Relay, sessions: SessionFactory
) -> None:
    """The first attempt sends and records the e-mail, but its answer never reaches Temporal,
    as when the worker stops right after. The retry finds the e-mail recorded and sends
    nothing."""

    @activity.defn(name=SEND_EMAIL)
    async def send_then_lose_the_answer(request: EmailRequest) -> EmailOutcome:
        outcome = await activities.send_email(request)
        if activity.info().attempt == 1:
            raise RuntimeError("the worker stopped before it reported the outcome")
        return outcome

    outcome = await run_workflow([send_then_lose_the_answer], alert())

    assert outcome.result is EmailResult.ALREADY_SENT
    assert len(relay.sent) == 1
    rows = await recorded(sessions)
    assert [(row.status, row.sent_at, row.level) for row in rows] == [
        (NotificationStatus.SENT, NOW, Level.HIGH)
    ]


async def test_a_relay_failure_is_retried_until_the_email_goes_out(
    activities: EmailActivities, relay: Relay, sessions: SessionFactory
) -> None:
    relay.failures = [EmailTransportError("connect: ConnectionRefusedError", retryable=True)]

    outcome = await run_workflow(activities.activities(), group())

    assert outcome.result is EmailResult.SENT
    assert outcome.kind is EmailKind.GROUP_ALERT
    assert (relay.attempts, len(relay.sent)) == (2, 1)
    assert [row.status for row in await recorded(sessions)] == [NotificationStatus.SENT]


async def test_a_health_alarm_is_sent_through_temporal_while_writes_are_off(
    activities: EmailActivities, relay: Relay, sessions: SessionFactory
) -> None:
    """T-032 criterion 8: the activity takes the health alarm as the workflow sends it, routes
    it to `analyst-eng` (revision 0010's seed route) and sends it with the kill switch off."""
    async with sessions.begin() as session:
        await session.execute(
            insert(NotificationRecipientRow),
            [{"list_name": "analyst-eng", "email": "platform-1@example.com"}],
        )
        await set_platform_flag(
            session,
            PlatformFlag.WRITES_ENABLED,
            enabled=False,
            reason="AI incident, writes stopped.",
            actor_kind=ActorKind.USER,
            actor_id="admin01",
        )
    alarm = HealthAlarm(
        alarm_id="0193a5c2-7b4e-7d1a-8c3f-5e2b9a1d4f60",
        alarm_kind="intake_stopped",
        subject="intake",
        status="open",
        notification_no=1,
        opened_at=NOW,
        counts={"lag_minutes": 35, "threshold": 15},
    )

    outcome = await run_workflow(activities.activities(), alarm)

    assert outcome == EmailOutcome(
        result=EmailResult.SENT,
        kind=EmailKind.HEALTH_ALARM,
        idempotency_key=f"health_alarm:{alarm.alarm_id}:open:1",
    )
    assert [message.recipients for message in relay.sent] == [["platform-1@example.com"]]
    [row] = await recorded(sessions)
    assert (row.kind, row.level, row.case_id, row.status) == (
        EmailKind.HEALTH_ALARM,
        None,
        None,
        NotificationStatus.SENT,
    )


# --- settings --------------------------------------------------------------------------------


def smtp_env(**changes: str) -> dict[str, str]:
    return {SMTP_HOST_ENV: "relay.example.com", SMTP_FROM_ENV: "ai-soc@example.com"} | changes


def test_the_relay_settings_come_from_the_environment(tmp_path: Path) -> None:
    (tmp_path / "smtp-password").write_text(PASSWORD + "\n", encoding="utf-8")
    ca_file = tmp_path / "company-ca.pem"
    ca_file.write_text("not read here\n", encoding="utf-8")

    plain = load_smtp_settings(smtp_env())
    full = load_smtp_settings(
        smtp_env(
            **{
                SMTP_PORT_ENV: "2465",
                SMTP_TLS_ENV: "implicit",
                SMTP_USERNAME_ENV: "ais0c-relay",
                SMTP_CA_FILE_ENV: str(ca_file),
                SMTP_TIMEOUT_ENV: "12.5",
                EXECUTOR_SECRETS_DIR_ENV: str(tmp_path),
            }
        )
    )

    assert (plain.host, plain.sender, plain.tls, plain.effective_port) == (
        "relay.example.com",
        "ai-soc@example.com",
        TlsMode.STARTTLS,
        587,
    )
    assert (plain.username, plain.password, plain.ca_file, plain.timeout) == (None, None, None, 30)
    assert (full.tls, full.effective_port, full.timeout, full.ca_file) == (
        TlsMode.IMPLICIT,
        2465,
        12.5,
        ca_file,
    )
    assert full.username == "ais0c-relay"
    assert full.password is not None
    assert full.password.get_secret_value() == PASSWORD


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({SMTP_HOST_ENV: " "}, "AIS0C_SMTP_HOST is not set"),
        ({SMTP_FROM_ENV: ""}, "AIS0C_SMTP_FROM is not set"),
        ({SMTP_FROM_ENV: "AI-SOC <ai-soc@example.com>"}, "sender"),
        ({SMTP_PORT_ENV: "smtp"}, "AIS0C_SMTP_PORT must be a number"),
        ({SMTP_PORT_ENV: "0"}, "port"),
        ({SMTP_TLS_ENV: "ssl"}, "AIS0C_SMTP_TLS must be one of: starttls, implicit, none"),
        ({SMTP_TLS_ENV: "none", SMTP_USERNAME_ENV: "ais0c-relay"}, "login needs TLS"),
        ({SMTP_USERNAME_ENV: "ais0c-relay"}, "smtp-password cannot be read"),
        ({SMTP_CA_FILE_ENV: "/nonexistent/ca.pem"}, "ca_file"),
        ({SMTP_TIMEOUT_ENV: "-1"}, "timeout"),
    ],
    ids=[
        "no-host",
        "no-sender",
        "sender-with-a-name",
        "port-not-a-number",
        "port-0",
        "unknown-tls-mode",
        "login-without-tls",
        "no-password-file",
        "missing-ca-file",
        "negative-timeout",
    ],
)
def test_missing_or_invalid_settings_stop_the_runtime(
    tmp_path: Path, changes: dict[str, str], problem: str
) -> None:
    (tmp_path / "smtp-password").write_text(PASSWORD, encoding="utf-8")
    env = smtp_env(**changes)
    if "smtp-password" not in problem:
        env[EXECUTOR_SECRETS_DIR_ENV] = str(tmp_path)

    with pytest.raises(RuntimeConfigError, match=problem) as raised:
        load_smtp_settings(env)

    assert PASSWORD not in str(raised.value)


@pytest.mark.parametrize("content", ["", "\n", "one\ntwo\n"], ids=["empty", "blank", "two-lines"])
def test_the_password_file_holds_one_line(tmp_path: Path, content: str) -> None:
    (tmp_path / "smtp-password").write_text(content, encoding="utf-8")
    env = smtp_env(**{SMTP_USERNAME_ENV: "ais0c-relay", EXECUTOR_SECRETS_DIR_ENV: str(tmp_path)})

    with pytest.raises(RuntimeConfigError, match="one password on one line"):
        load_smtp_settings(env)


async def test_the_runtime_connects_to_nothing_while_it_is_built() -> None:
    env = smtp_env(
        AIS0C_DATABASE_URL="postgresql+psycopg://ais0c:unused@127.0.0.1:1/ais0c",
        **{SMTP_TLS_ENV: "none", SMTP_PORT_ENV: "1025"},
    )

    runtime = load_email_runtime(env)
    try:
        assert runtime.settings.effective_port == 1025
        assert [function.__name__ for function in runtime.activities.activities()] == ["send_email"]
    finally:
        await runtime.close()
