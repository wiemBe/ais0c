"""The worker of the `soc-executor` task queue: the Action Executor's two activities (T-045).

`python -m ais0c_worker executor` runs it (`ais0c_worker.main`). Its runtime comes from
`load_executor_runtime`: the database, the gateway with the note profile's token and the SMTP
relay. It runs no agent, no workflow and no Schedule, and holds no agent token; the note and the
e-mail are the only things it writes outside the database (T-33 (1)).
"""

from pydantic_ai.durable_exec.temporal import PydanticAIPlugin
from temporalio.client import Client
from temporalio.worker import Worker

from ais0c_activities import ExecutorRuntime
from ais0c_workflows.names import EXECUTOR_TASK_QUEUE


def build_executor_worker(client: Client, runtime: ExecutorRuntime) -> Worker:
    """A worker for the `soc-executor` task queue; run it with `async with` or `run()`.

    It registers `write_offense_note` and `send_email` and nothing else (criterion 1). The
    worker runs no workflow, but the activities' arguments are Pydantic models the data
    converter must decode, so `client` carries Pydantic AI's plugin (`connect`), which brings
    the Pydantic data converter. Workflows run in Temporal's sandbox, the default runner.
    """
    if not any(isinstance(plugin, PydanticAIPlugin) for plugin in client.config()["plugins"]):
        raise ValueError("the Temporal client must be created with Pydantic AI's plugin")
    return Worker(
        client,
        task_queue=EXECUTOR_TASK_QUEUE,
        workflows=[],
        activities=runtime.activities(),
    )
