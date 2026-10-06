"""Criterion 8: the constants the API and the workflows package must agree on.

The API may not import `ais0c_workflows` (docs/impl/repo-structure.md), so the Schedule ID is
defined twice. This test is what keeps the two copies from drifting: it is a cross-package test, so
it lives in `tests/api/` rather than in either package's own tests.
"""

from ais0c_api.temporal import KNOWLEDGE_SYNC_SCHEDULE_ID
from ais0c_workflows.names import KNOWLEDGE_SYNC_SCHEDULE_ID as WORKFLOWS_SCHEDULE_ID


def test_the_knowledge_sync_schedule_id_is_the_same_on_both_sides() -> None:
    assert KNOWLEDGE_SYNC_SCHEDULE_ID == WORKFLOWS_SCHEDULE_ID
    # Temporal names each run of this Schedule `knowledge-sync-<start time>`, so the ID is the
    # prefix of every run's workflow ID (ais0c_workflows.schedules).
    assert KNOWLEDGE_SYNC_SCHEDULE_ID == "knowledge-sync"
