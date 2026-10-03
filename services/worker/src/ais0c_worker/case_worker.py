"""The worker of the `soc-case` task queue: OffenseIntake, CaseWorkflow and their activities."""

from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Worker

from ais0c_activities import (
    CaseSettings,
    IocMatcher,
    OffenseSource,
    SessionFactory,
    TriageRunner,
    case_queue_activities,
)
from ais0c_workflows import CASE_QUEUE_WORKFLOWS
from ais0c_workflows.names import CASE_TASK_QUEUE


def build_case_worker(
    client: Client,
    *,
    sessions: SessionFactory,
    source: OffenseSource,
    triage: TriageRunner,
    settings: CaseSettings | None = None,
    ioc_matcher: IocMatcher | None = None,
) -> Worker:
    """A worker for the `soc-case` task queue; run it with `async with` or `run()`.

    `settings` default to the environment (`CaseSettings.from_env`). Workflows run in Temporal's
    sandbox, the default runner. `client` must use the Pydantic data converter, because contract
    models cross the workflow boundary.
    """
    converter = client.data_converter.payload_converter_class
    if converter is not pydantic_data_converter.payload_converter_class:
        raise ValueError("the Temporal client must use the Pydantic data converter")
    activities = case_queue_activities(
        client=client,
        sessions=sessions,
        source=source,
        triage=triage,
        settings=CaseSettings.from_env() if settings is None else settings,
        ioc_matcher=ioc_matcher,
    )
    return Worker(
        client,
        task_queue=CASE_TASK_QUEUE,
        workflows=list(CASE_QUEUE_WORKFLOWS),
        activities=activities,
    )
