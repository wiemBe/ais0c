"""The worker of the `soc-case` task queue: OffenseIntake, CaseWorkflow, TriageWorkflow and their
activities, the Triage agent's model and tool activities included."""

from pydantic_ai.durable_exec.temporal import PydanticAIPlugin
from temporalio.client import Client
from temporalio.worker import Worker

from ais0c_activities import (
    CaseSettings,
    IocMatcher,
    OffenseSource,
    SessionFactory,
    TriageRuntime,
    case_queue_activities,
)
from ais0c_workflows import CASE_QUEUE_WORKFLOWS
from ais0c_workflows.agent_runtime import install_triage_agent
from ais0c_workflows.names import CASE_TASK_QUEUE


async def connect(address: str, *, namespace: str = "default") -> Client:
    """A Temporal client set up for the case worker: Pydantic AI's plugin brings the Pydantic data
    converter that contract models need and the sandbox settings that agent runs need."""
    return await Client.connect(address, namespace=namespace, plugins=[PydanticAIPlugin()])


def build_case_worker(
    client: Client,
    *,
    sessions: SessionFactory,
    source: OffenseSource,
    triage: TriageRuntime,
    settings: CaseSettings | None = None,
    ioc_matcher: IocMatcher | None = None,
) -> Worker:
    """A worker for the `soc-case` task queue; run it with `async with` or `run()`.

    Installs `triage`'s agent as the Triage agent of this process's TriageWorkflows
    (`ais0c_workflows.agent_runtime`). `settings` default to the environment
    (`CaseSettings.from_env`). Workflows run in Temporal's sandbox, the default runner.
    `client` must carry Pydantic AI's plugin (`connect`): contract models and agent messages
    cross the workflow boundary, and the agent runs in workflow code.
    """
    if not any(isinstance(plugin, PydanticAIPlugin) for plugin in client.config()["plugins"]):
        raise ValueError("the Temporal client must be created with Pydantic AI's plugin")
    install_triage_agent(triage.run)
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
