"""Temporal workers for the case, hunt and batch task queues.

The case worker runs OffenseIntake, CaseWorkflow and TriageWorkflow; a Temporal Schedule starts
the intake. `python -m ais0c_worker` runs it against the gateway's QRadar and the Triage agent
(`ais0c_worker.main`); tests pass in an offense source and a Triage runtime of their own.

`python -m ais0c_worker batch` runs the batch worker of the `soc-batch` queue: KnowledgeSync's
catalog sync, with the Schedule that starts it (`ais0c_worker.batch_worker`,
`ais0c_worker.schedule`).

`python -m ais0c_worker.model_release verify` compares the prod model registry with the vLLM
servers at deployment (`ais0c_worker.model_release`).
"""

from ais0c_worker.batch_worker import build_batch_worker
from ais0c_worker.case_worker import build_case_worker, connect
from ais0c_worker.main import run_batch_worker, run_case_worker
from ais0c_worker.schedule import (
    INTAKE_INTERVAL,
    INTAKE_SCHEDULE_ID,
    ensure_intake_schedule,
    ensure_knowledge_sync_schedule,
    intake_schedule,
)

__all__ = [
    "INTAKE_INTERVAL",
    "INTAKE_SCHEDULE_ID",
    "build_batch_worker",
    "build_case_worker",
    "connect",
    "ensure_intake_schedule",
    "ensure_knowledge_sync_schedule",
    "intake_schedule",
    "run_batch_worker",
    "run_case_worker",
]
