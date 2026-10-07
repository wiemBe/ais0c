"""Temporal workflows: OffenseIntake, CaseWorkflow, GroupCaseWorkflow, HuntWorkflow,
TuningWorkflow, KnowledgeSync, HealthCheck (the platform's health alarms, T-032).

CaseWorkflow runs each evaluation's agents as child workflows: TriageWorkflow, then AgentWorkflow
for the Orchestrator, the plan's steps and Reporting. GroupCaseWorkflow runs the same chain for
an offense group in storm (T-027).

Deterministic: no I/O. Activities are called by name, never imported. Workers run these
workflows in Temporal's sandbox with the Pydantic data converter.

Agents that run in workflow code are installed through `ais0c_workflows.agent_runtime`. Import
that module by its own name: workflows must reach the worker's copy of it, which the sandbox
passes through, so this package does not re-export it. The Schedules that start workflows are
in `ais0c_workflows.schedules`, which the worker imports by its own name as well.
"""

from typing import Final

from ais0c_workflows.agent import AgentOutcome, AgentRequest, AgentWorkflow, ChainResult
from ais0c_workflows.agent_run import AgentFailure
from ais0c_workflows.case import CaseCarry, CaseStatus, CaseView, CaseWorkflow
from ais0c_workflows.chain import ChainDecision
from ais0c_workflows.evaluation import AgentChain, ExecutorCalls
from ais0c_workflows.group import GroupCarry, GroupCaseWorkflow, GroupDecision, GroupView
from ais0c_workflows.group_summary import GroupSummary
from ais0c_workflows.health import HealthCheck, HealthCheckResult
from ais0c_workflows.intake import IntakeCheckpoint, OffenseIntake
from ais0c_workflows.knowledge_sync import KnowledgeSync, KnowledgeSyncResult
from ais0c_workflows.reevaluation import reevaluation_due, should_reevaluate
from ais0c_workflows.triage import (
    MODEL_ACCESS_FAILURES,
    TriageFailure,
    TriageOutcome,
    TriageRequest,
    TriageWorkflow,
)

# Workflows of the `soc-case` task queue.
CASE_QUEUE_WORKFLOWS: Final = (
    OffenseIntake,
    CaseWorkflow,
    GroupCaseWorkflow,
    TriageWorkflow,
    AgentWorkflow,
)
# Workflows of the `soc-batch` task queue.
BATCH_QUEUE_WORKFLOWS: Final = (KnowledgeSync, HealthCheck)

__all__ = [
    "BATCH_QUEUE_WORKFLOWS",
    "CASE_QUEUE_WORKFLOWS",
    "MODEL_ACCESS_FAILURES",
    "AgentChain",
    "AgentFailure",
    "AgentOutcome",
    "AgentRequest",
    "AgentWorkflow",
    "CaseCarry",
    "CaseStatus",
    "CaseView",
    "CaseWorkflow",
    "ChainDecision",
    "ChainResult",
    "ExecutorCalls",
    "GroupCarry",
    "GroupCaseWorkflow",
    "GroupDecision",
    "GroupSummary",
    "GroupView",
    "HealthCheck",
    "HealthCheckResult",
    "IntakeCheckpoint",
    "KnowledgeSync",
    "KnowledgeSyncResult",
    "OffenseIntake",
    "TriageFailure",
    "TriageOutcome",
    "TriageRequest",
    "TriageWorkflow",
    "reevaluation_due",
    "should_reevaluate",
]
