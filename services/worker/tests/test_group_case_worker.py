"""A storm end to end (T-027): real workflows and activities, PostgreSQL, a fake offense source,
the agents with scripted models and the executor's fakes, on the time-skipping test server.

The intake gives N offenses of one rule a full analysis, groups the next ones and starts the
group's case; the case waits the settle time, evaluates the group once with its summary in
Triage's prompt, notes every offense the group took with the group's decision and closes with
the group's window. The group's ID, and so the hourly sample, follows from the intake's own
clock, so the test does not count on which grouped offense the sample might pick.
IPs are from the RFC 5737 ranges.
"""

from datetime import timedelta

import pytest
from pydantic_ai.durable_exec.temporal import PydanticAIPlugin
from pydantic_ai.messages import ModelRequest
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer
from worker_support import eventually, model_requests, offense, running_platform

from ais0c_activities import CaseSettings, SessionFactory
from ais0c_contracts import CaseSource
from ais0c_storage.enums import CaseStatus, GroupStatus, OffenseStatus
from ais0c_storage.models import OffenseGroupRow
from ais0c_storage.repositories import get_offense_group
from ais0c_workflows import CASE_QUEUE_WORKFLOWS
from ais0c_workflows.notify import EvaluationNoteRequest

pytestmark = pytest.mark.anyio

LIMIT = 2
SETTLE = timedelta(minutes=10)
SETTINGS = CaseSettings(
    case_url_base="https://ais0c.example.com/cases",
    group_full_analyses_per_hour=LIMIT,
    group_settle=SETTLE,
)


async def test_a_storm_is_evaluated_as_one_group_case(
    env: WorkflowEnvironment, sessions: SessionFactory
) -> None:
    t0 = await env.get_current_time()
    async with running_platform(env, sessions, settings=SETTINGS) as platform:
        first = await platform.run_intake()
        await env.sleep(timedelta(minutes=5))
        t1 = await env.get_current_time()
        ids = list(range(11, 17))
        for number in ids:
            platform.source.put(
                offense(
                    number,
                    start=t1 - timedelta(seconds=60 - number),
                    destination_ips=[f"198.51.100.{number}"],
                )
            )
        await platform.run_intake(first)

        rows = [await platform.seen(number) for number in ids]
        statuses = [row.status for row in rows if row is not None]
        # The limit's offenses get their cases; the first over it starts the storm.
        assert statuses[:LIMIT] == [OffenseStatus.RUNNING] * LIMIT
        assert statuses[LIMIT] is OffenseStatus.GROUPED
        group_id = rows[0].group_id if rows[0] is not None else None
        assert group_id is not None
        case_id = f"group-{group_id}"
        grouped = [
            r.offense_id for r in rows if r is not None and r.status is OffenseStatus.GROUPED
        ]
        handle = env.client.get_workflow_handle(case_id)
        await handle.describe()  # the intake started the group's case

        # Nothing is evaluated within the settle time.
        await env.sleep(timedelta(minutes=5))
        assert await platform.case(case_id) is None

        await env.sleep(timedelta(minutes=6))
        decided = await platform.case_when(case_id, lambda row: row.status is CaseStatus.DECIDED)
        assert (decided.source, decided.group_id, decided.offense_id) == (
            CaseSource.GROUP,
            group_id,
            None,
        )

        async def stored_group() -> OffenseGroupRow | None:
            async with sessions() as session:
                return await get_offense_group(session, group_id)

        group = await stored_group()
        assert group is not None
        assert (group.status, group.case_id) == (GroupStatus.STORM, case_id)

        def group_notes() -> list[int]:
            return sorted(
                r.content.offense_id
                for r in platform.executor.notes
                if isinstance(r, EvaluationNoteRequest) and r.content.group_id == group_id
            )

        async def noted() -> list[int] | None:
            found = group_notes()
            return found if found == sorted(grouped) else None

        assert await eventually(noted) == sorted(grouped)

        # Triage saw the group's summary in its own untrusted block.
        triage = env.client.get_workflow_handle(f"{case_id}-triage-1")
        requests = model_requests(await triage.fetch_history())
        instructions = [
            message.instructions or ""
            for messages in requests
            for message in messages
            if isinstance(message, ModelRequest)
        ]
        assert any('source="qradar.group_summary"' in text for text in instructions)
        assert any(f'"offense_count": {len(ids)}' in text for text in instructions)

        # The window ends a day after the group's last offense: the group and its case close.
        await env.sleep(timedelta(hours=24))
        closed = await platform.case_when(case_id, lambda row: row.status is CaseStatus.CLOSED)
        assert closed.evaluation_no == 1
        group = await stored_group()
        assert group is not None
        assert group.status is GroupStatus.CLOSED
        await handle.result()

        replayer = Replayer(workflows=list(CASE_QUEUE_WORKFLOWS), plugins=[PydanticAIPlugin()])
        await replayer.replay_workflow(await handle.fetch_history())
        await replayer.replay_workflow(await triage.fetch_history())
    assert t0 < t1
