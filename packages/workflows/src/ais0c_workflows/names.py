"""Names of the task queues: workflows, signals, Schedules and the activities they call.

Activities are called by name; `ais0c_activities.names` registers them under the same names.
A test in services/worker checks that the two lists match.
"""

from typing import Final

CASE_TASK_QUEUE: Final = "soc-case"
BATCH_TASK_QUEUE: Final = "soc-batch"
# The Action Executor's activities run in their own process on their own queue, with their own
# secrets (T-33 (1), T-045).
EXECUTOR_TASK_QUEUE: Final = "soc-executor"

OFFENSE_INTAKE: Final = "OffenseIntake"
CASE_WORKFLOW: Final = "CaseWorkflow"
# The case of an offense group in storm (T-027).
GROUP_CASE_WORKFLOW: Final = "GroupCaseWorkflow"
TRIAGE_WORKFLOW: Final = "TriageWorkflow"
# One run of a chain agent: Orchestrator, Investigation, Verification or Reporting.
AGENT_WORKFLOW: Final = "AgentWorkflow"
KNOWLEDGE_SYNC: Final = "KnowledgeSync"

# The Schedule that starts KnowledgeSync; its runs' workflow IDs begin with it.
KNOWLEDGE_SYNC_SCHEDULE_ID: Final = "knowledge-sync"

# CaseWorkflow signals and query.
OFFENSE_UPDATED: Final = "offense_updated"
OFFENSE_CLOSED: Final = "offense_closed"
CASE_STATE: Final = "state"
# GroupCaseWorkflow's signal: the group took another offense. Its query is `state` too.
GROUP_UPDATED: Final = "group_updated"


def case_workflow_id(offense_id: int) -> str:
    """ID of the offense's case workflow (architecture §20)."""
    return f"case-{offense_id}"


def group_case_id(group_id: str) -> str:
    """ID of the group's case workflow and of its `cases` row (docs/impl/data-model.md)."""
    return f"group-{group_id}"


def agent_workflow_id(case_id: str, agent: str, evaluation_no: int, *, retry: bool = False) -> str:
    """ID of an agent's run in one evaluation, `<case_id>-<agent>-<evaluation_no>`; it is also
    the run's `agent_runs.run_id` (decision T-29).

    The run that retries one the model's outage ended has its own ID (D-33).
    """
    run_id = f"{case_id}-{agent}-{evaluation_no}"
    return f"{run_id}-retry" if retry else run_id


def triage_workflow_id(case_id: str, evaluation_no: int, *, retry: bool = False) -> str:
    """ID of the Triage run of one evaluation; it is also the run's `agent_runs.run_id`."""
    return agent_workflow_id(case_id, "triage", evaluation_no, retry=retry)


# Intake activities.
FETCH_OFFENSE_CHANGES: Final = "fetch_offense_changes"
ADMIT_OFFENSES: Final = "admit_offenses"
FIND_CLOSED_OFFENSES: Final = "find_closed_offenses"
NEXT_PENDING_OFFENSES: Final = "next_pending_offenses"
START_CASE: Final = "start_case"
WAKE_GROUP_CASES: Final = "wake_group_cases"

# Case activities.
FETCH_OFFENSE: Final = "fetch_offense"
RECORD_OFFENSE_UPDATE: Final = "record_offense_update"
REEVALUATION_INTERVAL: Final = "reevaluation_interval"
ENRICH_OFFENSE: Final = "enrich_offense"
START_EVALUATION: Final = "start_evaluation"
AGENT_RETRY_DELAY: Final = "agent_retry_delay"
RECORD_DECISION: Final = "record_decision"
MARK_NO_AI_DECISION: Final = "mark_no_ai_decision"
CLOSE_CASE: Final = "close_case"
# The agent chain of an evaluation (T-026): what the plan is made from and how it is recorded.
EVALUATION_WINDOW: Final = "evaluation_window"
CANDIDATE_SKILLS: Final = "candidate_skills"
PLAN_BUDGETS: Final = "plan_budgets"
RECORD_PLAN: Final = "record_plan"
# The executor calls of an evaluation (T-045): the case link of a note or e-mail, then the
# note and the alert e-mail themselves, the last two on the executor's own task queue.
CASE_URL: Final = "case_url"
WRITE_OFFENSE_NOTE: Final = "write_offense_note"
SEND_EMAIL: Final = "send_email"

# GroupCaseWorkflow activities (T-027). It also calls `case_url`, `fetch_offense`,
# `evaluation_window`, `record_decision`, `mark_no_ai_decision`, `agent_retry_delay` and
# `reevaluation_interval`, and the chain's.
GROUP_SETTLE_DELAY: Final = "group_settle_delay"
GROUP_CASE_STATE: Final = "group_case_state"
ENRICH_GROUP: Final = "enrich_group"
BEGIN_GROUP_EVALUATION: Final = "begin_group_evaluation"
CLOSE_GROUP_CASE: Final = "close_group_case"

# TriageWorkflow activities. The agent's model requests and tool calls are activities too;
# Pydantic AI's TemporalDurability registers them under names it derives from the agent.
BEGIN_TRIAGE_RUN: Final = "begin_triage_run"
FINISH_TRIAGE_RUN: Final = "finish_triage_run"

# AgentWorkflow activities; as with Triage, the agent's own come from TemporalDurability.
BEGIN_AGENT_RUN: Final = "begin_agent_run"
LOAD_EVIDENCE: Final = "load_evidence"
FINISH_AGENT_RUN: Final = "finish_agent_run"

# KnowledgeSync activities.
SYNC_ANALYSIS_CATALOG: Final = "sync_analysis_catalog"

ACTIVITY_NAMES: Final = frozenset(
    {
        FETCH_OFFENSE_CHANGES,
        ADMIT_OFFENSES,
        FIND_CLOSED_OFFENSES,
        NEXT_PENDING_OFFENSES,
        START_CASE,
        WAKE_GROUP_CASES,
        FETCH_OFFENSE,
        RECORD_OFFENSE_UPDATE,
        REEVALUATION_INTERVAL,
        ENRICH_OFFENSE,
        START_EVALUATION,
        AGENT_RETRY_DELAY,
        RECORD_DECISION,
        MARK_NO_AI_DECISION,
        CLOSE_CASE,
        EVALUATION_WINDOW,
        CANDIDATE_SKILLS,
        PLAN_BUDGETS,
        RECORD_PLAN,
        CASE_URL,
        WRITE_OFFENSE_NOTE,
        SEND_EMAIL,
        GROUP_SETTLE_DELAY,
        GROUP_CASE_STATE,
        ENRICH_GROUP,
        BEGIN_GROUP_EVALUATION,
        CLOSE_GROUP_CASE,
        BEGIN_TRIAGE_RUN,
        FINISH_TRIAGE_RUN,
        BEGIN_AGENT_RUN,
        LOAD_EVIDENCE,
        FINISH_AGENT_RUN,
        SYNC_ANALYSIS_CATALOG,
    }
)
