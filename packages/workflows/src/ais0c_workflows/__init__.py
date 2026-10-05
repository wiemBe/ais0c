"""Temporal workflows: OffenseIntake, CaseWorkflow, HuntWorkflow, TuningWorkflow, KnowledgeSync.

Deterministic: no I/O. Activities are called by name, never imported. Workers run these
workflows in Temporal's sandbox with the Pydantic data converter.

Agents that run in workflow code are installed through `ais0c_workflows.agent_runtime`. Import
that module by its own name: workflows must reach the worker's copy of it, which the sandbox
passes through, so this package does not re-export it. The Schedules that start workflows are
in `ais0c_workflows.schedules`, which the worker imports by its own name as well.
"""

from typing import Final

from ais0c_workflows.case import CaseCarry, CaseStatus, CaseView, CaseWorkflow
from ais0c_workflows.intake import IntakeCheckpoint, OffenseIntake
from ais0c_workflows.knowledge_sync import KnowledgeSync, KnowledgeSyncResult
from ais0c_workflows.reevaluation import should_reevaluate
from ais0c_workflows.triage import (
    MODEL_ACCESS_FAILURES,
    TriageFailure,
    TriageOutcome,
    TriageRequest,
    TriageWorkflow,
)

# Workflows of the `soc-case` task queue.
CASE_QUEUE_WORKFLOWS: Final = (OffenseIntake, CaseWorkflow, TriageWorkflow)
# Workflows of the `soc-batch` task queue.
BATCH_QUEUE_WORKFLOWS: Final = (KnowledgeSync,)

__all__ = [
    "BATCH_QUEUE_WORKFLOWS",
    "CASE_QUEUE_WORKFLOWS",
    "MODEL_ACCESS_FAILURES",
    "CaseCarry",
    "CaseStatus",
    "CaseView",
    "CaseWorkflow",
    "IntakeCheckpoint",
    "KnowledgeSync",
    "KnowledgeSyncResult",
    "OffenseIntake",
    "TriageFailure",
    "TriageOutcome",
    "TriageRequest",
    "TriageWorkflow",
    "should_reevaluate",
]
