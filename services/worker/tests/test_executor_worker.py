"""The two workers split by T-33 (1) (T-045 criteria 1 and 2).

The executor worker serves the `soc-executor` queue with the note and the e-mail activity and
nothing else; the case worker serves `soc-case` without them. Their runtimes and the command
line are tested in packages/activities/tests/test_runtime.py and test_batch_worker.py.
"""

from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager

import pytest
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment
from worker_support import ChainModels, RecordingGateway, TriageModel, chain_runtime, triage_runtime

from ais0c_activities import (
    CaseSettings,
    EmailActivities,
    EmailRuntime,
    ExecutorRuntime,
    FakeOffenseSource,
    NoteActivities,
    NoteGatewayClient,
    NoteRuntime,
    NoteToolset,
    SessionFactory,
    load_smtp_settings,
)
from ais0c_executor.email import EmailConnection
from ais0c_worker import build_case_worker, build_executor_worker
from ais0c_workflows.names import (
    CASE_TASK_QUEUE,
    CASE_URL,
    EXECUTOR_TASK_QUEUE,
    SEND_EMAIL,
    WRITE_OFFENSE_NOTE,
)

pytestmark = pytest.mark.anyio

TOOLSET = NoteToolset.model_validate(
    {
        "name": "qradar-note-write",
        "connector": "qradar",
        "tools": [
            {
                "id": "add_offense_note",
                "description": "Add a note to a QRadar offense.",
                "schema_version": "a1b2c3d4e5f60718",
                "cost_class": "low",
                "risk": "write",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
            {
                "id": "get_offense_notes",
                "description": "Read one page of an offense's notes.",
                "schema_version": "0f1e2d3c4b5a6978",
                "cost_class": "low",
                "risk": "read",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
            },
        ],
    }
)


class NoRelay:
    """A relay the tests never reach."""

    def connect(self) -> AbstractAsyncContextManager[EmailConnection]:
        raise AssertionError("no e-mail is sent here")


def executor_runtime(sessions: SessionFactory) -> ExecutorRuntime:
    gateway = NoteGatewayClient("http://127.0.0.1:9", "executor-note-token-for-tests")
    settings = load_smtp_settings(
        {
            "AIS0C_SMTP_HOST": "127.0.0.1",
            "AIS0C_SMTP_TLS": "none",
            "AIS0C_SMTP_FROM": "ai-soc@example.com",
        }
    )
    return ExecutorRuntime(
        note=NoteRuntime(
            sessions=sessions,
            gateway=gateway,
            toolset=TOOLSET,
            activities=NoteActivities(sessions=sessions, gateway=gateway, toolset=TOOLSET),
        ),
        email=EmailRuntime(
            sessions=sessions,
            settings=settings,
            activities=EmailActivities(sessions=sessions, transport=NoRelay()),
        ),
    )


def activity_names(activities: Sequence[Callable[..., object]]) -> set[str]:
    return {getattr(item, "__temporal_activity_definition").name for item in activities}


async def test_the_executor_worker_runs_only_the_note_and_the_email(
    env: WorkflowEnvironment, sessions: SessionFactory
) -> None:
    config = build_executor_worker(env.client, executor_runtime(sessions)).config()

    assert config.get("task_queue") == EXECUTOR_TASK_QUEUE
    assert list(config.get("workflows", [])) == []
    assert activity_names(config.get("activities", [])) == {WRITE_OFFENSE_NOTE, SEND_EMAIL}


async def test_the_case_worker_runs_no_executor_activity(
    env: WorkflowEnvironment, sessions: SessionFactory
) -> None:
    config = build_case_worker(
        env.client,
        sessions=sessions,
        source=FakeOffenseSource(),
        triage=triage_runtime(TriageModel(), RecordingGateway()),
        chain=chain_runtime(ChainModels(), RecordingGateway()),
        settings=CaseSettings(case_url_base="https://ais0c.example.com/cases"),
    ).config()

    assert config.get("task_queue") == CASE_TASK_QUEUE
    names = activity_names(config.get("activities", []))
    assert CASE_URL in names
    assert not names & {WRITE_OFFENSE_NOTE, SEND_EMAIL}


async def test_the_executor_worker_needs_pydantic_ais_plugin(
    env: WorkflowEnvironment, sessions: SessionFactory
) -> None:
    """The requests are Pydantic models: without the plugin's converter they would not
    decode."""
    plain = Client(env.client.service_client, namespace=env.client.namespace)

    with pytest.raises(ValueError, match="Pydantic AI's plugin"):
        build_executor_worker(plain, executor_runtime(sessions))
