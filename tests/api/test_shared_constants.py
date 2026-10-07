"""Criterion 8: the constants the API and the workflows package must agree on.

The API may not import `ais0c_workflows` (docs/impl/repo-structure.md), so the Schedule ID and the
format of an agent run's ID are defined twice. These tests keep the two copies from drifting: they
are cross-package tests, so they live in `tests/api/` rather than in either package's own tests.
"""

import pytest

from ais0c_api.run_ids import evaluation_of, is_retry
from ais0c_api.temporal import KNOWLEDGE_SYNC_SCHEDULE_ID
from ais0c_workflows.agent_runtime import AgentKind
from ais0c_workflows.names import KNOWLEDGE_SYNC_SCHEDULE_ID as WORKFLOWS_SCHEDULE_ID
from ais0c_workflows.names import agent_workflow_id, triage_workflow_id


def test_the_knowledge_sync_schedule_id_is_the_same_on_both_sides() -> None:
    assert KNOWLEDGE_SYNC_SCHEDULE_ID == WORKFLOWS_SCHEDULE_ID
    # Temporal names each run of this Schedule `knowledge-sync-<start time>`, so the ID is the
    # prefix of every run's workflow ID (ais0c_workflows.schedules).
    assert KNOWLEDGE_SYNC_SCHEDULE_ID == "knowledge-sync"


@pytest.mark.parametrize("case_id", ["case-12345", "case-hunt-hunt-1-1", "group-G-0a1b2c3d4e5f-1"])
@pytest.mark.parametrize("evaluation_no", [1, 2, 17])
@pytest.mark.parametrize("retry", [False, True])
def test_the_api_reads_the_evaluation_from_the_run_ids_the_workflows_make(
    case_id: str, evaluation_no: int, retry: bool
) -> None:
    """The case detail and the steps read the evaluation from the run ID (decision T-29)."""
    for agent in [*AgentKind, "triage"]:
        run_id = agent_workflow_id(case_id, str(agent), evaluation_no, retry=retry)
        assert evaluation_of(run_id, case_id=case_id, agent_id=str(agent)) == evaluation_no
        assert is_retry(run_id) is retry
    assert (
        evaluation_of(
            triage_workflow_id(case_id, evaluation_no, retry=retry),
            case_id=case_id,
            agent_id="triage",
        )
        == evaluation_no
    )


def test_a_run_id_of_another_case_or_agent_has_no_evaluation() -> None:
    run_id = agent_workflow_id("case-1", AgentKind.VERIFICATION, 2)

    assert evaluation_of(run_id, case_id="case-12", agent_id="verification") is None
    assert evaluation_of(run_id, case_id="case-1", agent_id="reporting") is None
    assert evaluation_of("executor-note-7f3a9c", case_id="case-1", agent_id="executor") is None
