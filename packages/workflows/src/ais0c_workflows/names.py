"""Names of the case task queue: workflows, signals and the activities they call.

Activities are called by name; `ais0c_activities.names` registers them under the same names.
A test in services/worker checks that the two lists match.
"""

from typing import Final

CASE_TASK_QUEUE: Final = "soc-case"

OFFENSE_INTAKE: Final = "OffenseIntake"
CASE_WORKFLOW: Final = "CaseWorkflow"

# CaseWorkflow signals and query.
OFFENSE_UPDATED: Final = "offense_updated"
OFFENSE_CLOSED: Final = "offense_closed"
CASE_STATE: Final = "state"


def case_workflow_id(offense_id: int) -> str:
    """ID of the offense's case workflow (architecture §20)."""
    return f"case-{offense_id}"


# Intake activities.
FETCH_OFFENSE_CHANGES: Final = "fetch_offense_changes"
ADMIT_OFFENSES: Final = "admit_offenses"
FIND_CLOSED_OFFENSES: Final = "find_closed_offenses"
NEXT_PENDING_OFFENSES: Final = "next_pending_offenses"
START_CASE: Final = "start_case"

# Case activities.
FETCH_OFFENSE: Final = "fetch_offense"
ENRICH_OFFENSE: Final = "enrich_offense"
START_EVALUATION: Final = "start_evaluation"
TRIAGE: Final = "triage"
RECORD_DECISION: Final = "record_decision"
MARK_NO_AI_DECISION: Final = "mark_no_ai_decision"
CLOSE_CASE: Final = "close_case"

ACTIVITY_NAMES: Final = frozenset(
    {
        FETCH_OFFENSE_CHANGES,
        ADMIT_OFFENSES,
        FIND_CLOSED_OFFENSES,
        NEXT_PENDING_OFFENSES,
        START_CASE,
        FETCH_OFFENSE,
        ENRICH_OFFENSE,
        START_EVALUATION,
        TRIAGE,
        RECORD_DECISION,
        MARK_NO_AI_DECISION,
        CLOSE_CASE,
    }
)
