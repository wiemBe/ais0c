"""The worker of the `soc-batch` task queue: KnowledgeSync and its activities (architecture §6).

`python -m ais0c_worker batch` runs it (`ais0c_worker.main`). Its runtime comes from
`load_batch_runtime` (T-022): the database and the gateway with the token of the
`qradar-inventory-read` profile. It runs no agent, so it needs neither the model registry nor
LiteLLM.
"""

from pydantic_ai.durable_exec.temporal import PydanticAIPlugin
from temporalio.client import Client
from temporalio.worker import Worker

from ais0c_activities import BatchRuntime
from ais0c_workflows import BATCH_QUEUE_WORKFLOWS
from ais0c_workflows.names import BATCH_TASK_QUEUE


def build_batch_worker(client: Client, runtime: BatchRuntime) -> Worker:
    """A worker for the `soc-batch` task queue; run it with `async with` or `run()`.

    `runtime.activities()` registers `sync_analysis_catalog` (T-022) under the name
    `KnowledgeSync` calls. The worker runs no agent, but the workflow's result is a Pydantic
    model, so `client` must carry Pydantic AI's plugin (`connect`), which brings the Pydantic
    data converter. Workflows run in Temporal's sandbox, the default runner.
    """
    if not any(isinstance(plugin, PydanticAIPlugin) for plugin in client.config()["plugins"]):
        raise ValueError("the Temporal client must be created with Pydantic AI's plugin")
    return Worker(
        client,
        task_queue=BATCH_TASK_QUEUE,
        workflows=list(BATCH_QUEUE_WORKFLOWS),
        activities=runtime.activities(),
    )
