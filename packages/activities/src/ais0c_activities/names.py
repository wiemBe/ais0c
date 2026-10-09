"""Names the task queues share with `ais0c_workflows`.

Workflows call activities by name and may not import this package (docs/impl/repo-structure.md),
so `ais0c_workflows.names` holds the same names; a test in services/worker checks that the two
lists match.
"""

from typing import Final

CASE_TASK_QUEUE: Final = "soc-case"
CASE_WORKFLOW: Final = "CaseWorkflow"
# The case of an offense group in storm (T-027) and the signal that wakes it.
GROUP_CASE_WORKFLOW: Final = "GroupCaseWorkflow"
GROUP_UPDATED: Final = "group_updated"


def case_workflow_id(offense_id: int) -> str:
    """ID of the offense's case workflow and of its `cases` row (architecture §20)."""
    return f"case-{offense_id}"


def group_case_id(group_id: str) -> str:
    """ID of the group's case workflow and of its `cases` row (docs/impl/data-model.md)."""
    return f"group-{group_id}"


# Intake activities (OffenseIntake).
FETCH_OFFENSE_CHANGES: Final = "fetch_offense_changes"
ADMIT_OFFENSES: Final = "admit_offenses"
FIND_CLOSED_OFFENSES: Final = "find_closed_offenses"
NEXT_PENDING_OFFENSES: Final = "next_pending_offenses"
START_CASE: Final = "start_case"
WAKE_GROUP_CASES: Final = "wake_group_cases"

# Case activities (CaseWorkflow). The intake also calls `close_case` for an offense whose case
# workflow no longer exists.
FETCH_OFFENSE: Final = "fetch_offense"
RECORD_OFFENSE_UPDATE: Final = "record_offense_update"
REEVALUATION_INTERVAL: Final = "reevaluation_interval"
ENRICH_OFFENSE: Final = "enrich_offense"
START_EVALUATION: Final = "start_evaluation"
AGENT_RETRY_DELAY: Final = "agent_retry_delay"
RECORD_DECISION: Final = "record_decision"
MARK_NO_AI_DECISION: Final = "mark_no_ai_decision"
CLOSE_CASE: Final = "close_case"
# The agent chain of an evaluation (CaseWorkflow, T-026).
EVALUATION_WINDOW: Final = "evaluation_window"
CANDIDATE_SKILLS: Final = "candidate_skills"
SKILL_TELEMETRY: Final = "skill_telemetry"
PLAN_BUDGETS: Final = "plan_budgets"
RECORD_PLAN: Final = "record_plan"
# The executor calls of an evaluation (CaseWorkflow, T-045): the case link on the case queue,
# the note and the alert e-mail on the `soc-executor` queue (ais0c_workflows.names).
CASE_URL: Final = "case_url"
WRITE_OFFENSE_NOTE: Final = "write_offense_note"
SEND_EMAIL: Final = "send_email"
# A note or e-mail the case gave up on (the executor was away for the whole hour): the case
# queue records it as failed (T-032, T-59 (7)).
RECORD_EXECUTOR_FAILURE: Final = "record_executor_failure"

# Group case activities (GroupCaseWorkflow, T-027). The group case also calls `case_url`,
# `fetch_offense`, `evaluation_window`, `record_decision`, `mark_no_ai_decision` and
# `agent_retry_delay`, and runs the agent chain as CaseWorkflow does.
GROUP_SETTLE_DELAY: Final = "group_settle_delay"
GROUP_CASE_STATE: Final = "group_case_state"
ENRICH_GROUP: Final = "enrich_group"
BEGIN_GROUP_EVALUATION: Final = "begin_group_evaluation"
CLOSE_GROUP_CASE: Final = "close_group_case"

# Triage run activities (TriageWorkflow). The agent's model and tool activities come from
# Pydantic AI's TemporalDurability.
BEGIN_TRIAGE_RUN: Final = "begin_triage_run"
FINISH_TRIAGE_RUN: Final = "finish_triage_run"

# Chain agent run activities (AgentWorkflow): Orchestrator, Investigation, Verification and
# Reporting. Their model and tool activities come from TemporalDurability too.
BEGIN_AGENT_RUN: Final = "begin_agent_run"
LOAD_EVIDENCE: Final = "load_evidence"
FINISH_AGENT_RUN: Final = "finish_agent_run"

# KnowledgeSync activities (the `soc-batch` task queue).
SYNC_ANALYSIS_CATALOG: Final = "sync_analysis_catalog"

# HealthCheck activities (the `soc-batch` task queue, T-032): the four checks, the syslog
# channel and the record that a notification went out.
CHECK_INTAKE: Final = "check_intake"
CHECK_LOG_SOURCES: Final = "check_log_sources"
CHECK_WRITE_FAILURES: Final = "check_write_failures"
CHECK_EXECUTOR_WORKER: Final = "check_executor_worker"
SEND_ALARM_SYSLOG: Final = "send_alarm_syslog"
MARK_ALARM_NOTIFIED: Final = "mark_alarm_notified"
