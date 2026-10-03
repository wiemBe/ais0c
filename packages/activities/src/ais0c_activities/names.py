"""Names the case task queue shares with `ais0c_workflows`.

Workflows call activities by name and may not import this package (docs/impl/repo-structure.md),
so `ais0c_workflows.names` holds the same names; a test in services/worker checks that the two
lists match.
"""

from typing import Final

CASE_TASK_QUEUE: Final = "soc-case"
CASE_WORKFLOW: Final = "CaseWorkflow"


def case_workflow_id(offense_id: int) -> str:
    """ID of the offense's case workflow and of its `cases` row (architecture §20)."""
    return f"case-{offense_id}"


# Intake activities (OffenseIntake).
FETCH_OFFENSE_CHANGES: Final = "fetch_offense_changes"
ADMIT_OFFENSES: Final = "admit_offenses"
FIND_CLOSED_OFFENSES: Final = "find_closed_offenses"
NEXT_PENDING_OFFENSES: Final = "next_pending_offenses"
START_CASE: Final = "start_case"

# Case activities (CaseWorkflow). The intake also calls `close_case` for an offense whose case
# workflow no longer exists.
FETCH_OFFENSE: Final = "fetch_offense"
RECORD_OFFENSE_UPDATE: Final = "record_offense_update"
REEVALUATION_INTERVAL: Final = "reevaluation_interval"
ENRICH_OFFENSE: Final = "enrich_offense"
START_EVALUATION: Final = "start_evaluation"
TRIAGE_RETRY_DELAY: Final = "triage_retry_delay"
RECORD_DECISION: Final = "record_decision"
MARK_NO_AI_DECISION: Final = "mark_no_ai_decision"
CLOSE_CASE: Final = "close_case"

# Triage run activities (TriageWorkflow). The agent's model and tool activities come from
# Pydantic AI's TemporalDurability.
BEGIN_TRIAGE_RUN: Final = "begin_triage_run"
FINISH_TRIAGE_RUN: Final = "finish_triage_run"
