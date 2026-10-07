"""GroupCaseWorkflow (T-027): the settle time and the group's summary (criterion 1), the group
note (criterion 2), the group's alert e-mail (criterion 3), the group decision's lifetime
(criterion 6), an evaluation without a decision, and replay.

The workflow runs in the sandbox against GroupFakes, with TriageStub and AgentStub in place of
the agents and the executor's fakes on their own queue; time is skipped on the Temporal test
server. IPs are from the RFC 5737 ranges.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

import pytest
from temporalio.client import WorkflowHandle
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker
from workflow_fakes import (
    WAIT_SECONDS,
    AgentStub,
    GroupFakes,
    TriageCall,
    TriageStub,
    executor_worker,
    group_summary,
    offense,
    triage_failure,
    triage_result,
)

from ais0c_contracts import CaseVerdict, Confidence, Level, TriageResult
from ais0c_workflows import (
    CASE_QUEUE_WORKFLOWS,
    CaseStatus,
    GroupCarry,
    GroupCaseWorkflow,
    GroupDecision,
    GroupSummary,
    GroupView,
    TriageFailure,
)
from ais0c_workflows.names import CASE_TASK_QUEUE, GROUP_UPDATED, group_case_id
from ais0c_workflows.notify import (
    GROUP_NOTE_KIND,
    EvaluationNoteRequest,
    note_content,
    run_marker,
)

pytestmark = pytest.mark.anyio

GROUP_ID = "G-0123456789ab-20261007T090000Z"
CASE_ID = group_case_id(GROUP_ID)
EXAMPLE = 201
SETTLE = timedelta(minutes=10)


@asynccontextmanager
async def running_group(
    env: WorkflowEnvironment, fakes: GroupFakes, carry: GroupCarry | None = None
) -> AsyncIterator[WorkflowHandle[GroupCaseWorkflow, GroupView]]:
    """The group case as the intake starts it: signal-with-start with `group_updated`; with
    `carry`, as Continue-As-New starts its next run."""
    async with executor_worker(env, fakes):
        async with Worker(
            env.client,
            task_queue=CASE_TASK_QUEUE,
            workflows=[GroupCaseWorkflow, TriageStub, AgentStub],
            activities=fakes.activities(),
        ):
            handle = await env.client.start_workflow(
                GroupCaseWorkflow.run,
                args=[GROUP_ID] if carry is None else [GROUP_ID, carry],
                id=CASE_ID,
                task_queue=CASE_TASK_QUEUE,
                start_signal=GROUP_UPDATED,
            )
            yield handle


def spray(
    now: datetime,
    count: int = 6,
    *,
    window: timedelta = timedelta(hours=2),
    grouped: tuple[int, ...] = (EXAMPLE,),
    **fields: object,
) -> GroupFakes:
    return GroupFakes(
        offense(EXAMPLE, start=now),
        summary=group_summary(count, at=now),
        grouped=grouped,
        window_end=now + window,
        **fields,  # pyright: ignore[reportArgumentType]
    )


async def wake(
    handle: WorkflowHandle[GroupCaseWorkflow, GroupView],
    fakes: GroupFakes,
    *,
    summary: GroupSummary | None = None,
    grouped: list[int] | None = None,
) -> None:
    """The intake took offenses into the group and woke its case; returns once the case read
    the group."""
    if summary is not None:
        fakes.summary = summary
    if grouped is not None:
        fakes.grouped = grouped
    read = fakes.states_read
    await handle.signal(GROUP_UPDATED)
    await fakes.events.wait_for("state", read + 1)


async def notes_when(
    fakes: GroupFakes, check: Callable[[list[tuple[int, int, str]]], bool]
) -> list[tuple[int, int, str]]:
    """The group notes once `check` holds; the executor's calls run beside the case."""
    async with asyncio.timeout(WAIT_SECONDS):
        while not check(fakes.group_notes()):
            await fakes.executor_events.wait_for("note", len(fakes.note_requests) + 1)
    return fakes.group_notes()


def near(value: datetime, expected: datetime) -> bool:
    """`expected` measured from the test's clock, which is a moment before the workflow's."""
    return abs(value - expected) < timedelta(seconds=5)


def triage_numbers(fakes: GroupFakes) -> list[int]:
    return [number for name, number in fakes.events.seen if name == "triage"]


async def finish(handle: WorkflowHandle[GroupCaseWorkflow, GroupView]) -> GroupView:
    """Let the group's window end; the case closes."""
    return await handle.result()


# --- criterion 1: one evaluation after the settle time, with every offense in the summary ---


async def test_the_group_is_evaluated_once_after_the_settle_time(env: WorkflowEnvironment) -> None:
    now = await env.get_current_time()
    fakes = spray(now)

    async with running_group(env, fakes) as handle:
        await fakes.events.wait_for("state", 1)
        await env.sleep(timedelta(minutes=5))
        assert "triage" not in fakes.events.names()
        # An offense arrives within the settle time: it joins the group and its summary.
        await wake(handle, fakes, summary=group_summary(7, at=now), grouped=[EXAMPLE, 202])
        assert "triage" not in fakes.events.names()

        await env.sleep(timedelta(minutes=6))
        await fakes.events.wait_for("decided", 1)
        state = await handle.query(GroupCaseWorkflow.state)
        result = await finish(handle)

    assert (state.case_id, state.group_id, state.status, state.evaluation_no) == (
        CASE_ID,
        GROUP_ID,
        CaseStatus.DECIDED,
        1,
    )
    assert fakes.triage_summaries == [(1, group_summary(7, at=now))]
    assert fakes.triage_runs == [f"{CASE_ID}-triage-1"]
    # The first evaluation's SLA runs from the storm's start: the case's start.
    assert [b.evaluation_no for b in fakes.begun] == [1]
    assert near(fakes.begun[0].sla_start, now)
    assert fakes.enriched == [group_summary(7, at=now)]
    # The decision's level, and the rule IDs the QA sample rate is chosen by.
    assert [(d.evaluation_no, d.rule_ids) for d in fakes.chain_decisions] == [(1, [100201])]
    assert result.status is CaseStatus.CLOSED
    assert fakes.closed_at


async def test_the_chain_agents_get_the_group_as_their_subject(env: WorkflowEnvironment) -> None:
    now = await env.get_current_time()
    fakes = spray(now)

    async with running_group(env, fakes) as handle:
        await env.sleep(SETTLE)
        await fakes.events.wait_for("decided", 1)
        await finish(handle)

    objectives = [call.request.objective for call in fakes.agent_calls if call.attempt == 1]
    assert sum(o.startswith(("Plan the rest", "Write the report")) for o in objectives) == 2
    for objective in objectives:
        if objective.startswith(("Plan the rest", "Write the report")):
            assert "group of 6 QRadar offenses" in objective
            assert f"offense {EXAMPLE}" in objective


# --- criterion 2: the group note ---------------------------------------------------------------


async def test_every_offense_the_group_took_gets_the_decision_once(
    env: WorkflowEnvironment,
) -> None:
    """No note before the decision; then one per offense the group took, with the group's ID
    and marker; an offense taken later gets the same decision's note; another wake-up with the
    same offenses writes nothing again."""
    now = await env.get_current_time()
    fakes = spray(now, grouped=(EXAMPLE, 202))

    async with running_group(env, fakes) as handle:
        await fakes.events.wait_for("state", 1)
        await env.sleep(timedelta(minutes=5))
        assert fakes.note_requests == []
        await env.sleep(timedelta(minutes=5))
        notes = await notes_when(fakes, lambda found: len(found) == 2)
        assert notes == [(EXAMPLE, 1, "suspicious"), (202, 1, "suspicious")]

        await wake(handle, fakes, grouped=[EXAMPLE, 202, 203])
        notes = await notes_when(fakes, lambda found: len(found) == 3)
        await wake(handle, fakes)
        await wake(handle, fakes)
        state = await handle.query(GroupCaseWorkflow.state)
        await finish(handle)

    assert notes[2] == (203, 1, "suspicious")
    assert fakes.group_notes() == notes
    assert state.noted == 3
    marker = run_marker(CASE_ID, 1, GROUP_NOTE_KIND)
    for request in fakes.note_requests:
        assert isinstance(request, EvaluationNoteRequest)
        assert request.case_id == CASE_ID
        assert request.content.group_id == GROUP_ID
        assert request.content.run_marker == marker
        assert request.content.case_url == f"https://ais0c.example.com/cases/{CASE_ID}"


async def test_an_offense_gets_another_note_only_when_the_verdict_changed(
    env: WorkflowEnvironment,
) -> None:
    """A re-evaluation with the same verdict writes no note again; a changed verdict reaches
    every offense the group took."""
    now = await env.get_current_time()
    verdicts = {1: CaseVerdict.SUSPICIOUS, 2: CaseVerdict.SUSPICIOUS, 3: CaseVerdict.TP}

    async def by_evaluation(call: TriageCall) -> TriageResult:
        return triage_result().model_copy(update={"verdict": verdicts[call.evaluation_no]})

    fakes = spray(
        now,
        window=timedelta(hours=60),
        grouped=(EXAMPLE, 202),
        triage_behavior=by_evaluation,
    )
    async with running_group(env, fakes) as handle:
        await env.sleep(SETTLE)
        await fakes.events.wait_for("decided", 1)
        await env.sleep(timedelta(hours=24))
        await fakes.events.wait_for("decided", 2)
        await env.sleep(timedelta(hours=24))
        await fakes.events.wait_for("decided", 3)
        notes = await notes_when(fakes, lambda found: len(found) == 4)
        await handle.terminate()

    assert notes == [
        (EXAMPLE, 1, "suspicious"),
        (202, 1, "suspicious"),
        (EXAMPLE, 3, "tp"),
        (202, 3, "tp"),
    ]


# --- criterion 3: the group's alert e-mail -----------------------------------------------------


async def test_a_high_group_is_e_mailed_and_a_higher_level_again(env: WorkflowEnvironment) -> None:
    """D-42: the workflow sends the alert of every high or critical decision; the executor sends
    it only when the level rose above the group's alerts already sent (T-036)."""
    now = await env.get_current_time()
    levels = {1: Level.MEDIUM, 2: Level.HIGH, 3: Level.CRITICAL}

    async def by_evaluation(call: TriageCall) -> TriageResult:
        return triage_result(ai_level=levels[call.evaluation_no])

    fakes = spray(now, window=timedelta(hours=60), triage_behavior=by_evaluation)
    async with running_group(env, fakes) as handle:
        await env.sleep(SETTLE)
        await fakes.events.wait_for("decided", 1)
        await env.sleep(timedelta(hours=24))
        await fakes.events.wait_for("decided", 2)
        await fakes.executor_events.wait_for("group_alert", 1)
        await env.sleep(timedelta(hours=24))
        await fakes.executor_events.wait_for("group_alert", 2)
        await handle.terminate()

    assert fakes.email_requests == []
    assert [(a.evaluation_no, a.notify_level) for a in fakes.group_alerts] == [
        (2, Level.HIGH),
        (3, Level.CRITICAL),
    ]
    alert = fakes.group_alerts[0]
    assert (alert.case_id, alert.group_id, alert.offense_count) == (CASE_ID, GROUP_ID, 6)
    assert alert.title == offense(EXAMPLE, start=now).description
    assert alert.case_url == f"https://ais0c.example.com/cases/{CASE_ID}"


# --- criterion 6: the group decision's lifetime --------------------------------------------------


async def test_the_group_is_evaluated_again_after_24_hours(env: WorkflowEnvironment) -> None:
    now = await env.get_current_time()
    fakes = spray(now, window=timedelta(hours=48))

    async with running_group(env, fakes) as handle:
        await env.sleep(SETTLE)
        await fakes.events.wait_for("decided", 1)
        await env.sleep(timedelta(hours=23))
        assert triage_numbers(fakes) == [1]
        await env.sleep(timedelta(hours=1))
        await fakes.events.wait_for("decided", 2)
        await handle.terminate()

    assert [b.evaluation_no for b in fakes.begun] == [1, 2]
    # A later evaluation's SLA runs from its own start.
    assert near(fakes.begun[1].sla_start, now + SETTLE + timedelta(hours=24))


async def test_twice_the_offenses_evaluate_the_group_again_after_the_interval(
    env: WorkflowEnvironment,
) -> None:
    now = await env.get_current_time()
    fakes = spray(now, window=timedelta(hours=10))

    async with running_group(env, fakes) as handle:
        await env.sleep(SETTLE)
        await fakes.events.wait_for("decided", 1)
        await wake(handle, fakes, summary=group_summary(11, at=now))
        await env.sleep(timedelta(minutes=40))
        assert triage_numbers(fakes) == [1]  # less than twice: nothing
        await wake(handle, fakes, summary=group_summary(12, at=now))
        await env.sleep(timedelta(minutes=1))
        await fakes.events.wait_for("decided", 2)
        assert fakes.triage_summaries[1] == (2, group_summary(12, at=now))
        await wake(handle, fakes, summary=group_summary(23, at=now))
        await env.sleep(timedelta(hours=1))
        await handle.terminate()

    assert triage_numbers(fakes) == [1, 2]


async def test_a_doubling_waits_for_the_interval_after_the_last_evaluation(
    env: WorkflowEnvironment,
) -> None:
    now = await env.get_current_time()
    fakes = spray(now, window=timedelta(hours=10))

    async with running_group(env, fakes) as handle:
        await env.sleep(SETTLE)
        await fakes.events.wait_for("decided", 1)
        await wake(handle, fakes, summary=group_summary(12, at=now))
        await env.sleep(timedelta(minutes=20))
        assert triage_numbers(fakes) == [1]
        await env.sleep(timedelta(minutes=11))
        await fakes.events.wait_for("decided", 2)
        await handle.terminate()

    assert near(fakes.begun[1].sla_start, now + SETTLE + timedelta(minutes=30))


@pytest.mark.parametrize("kind", ["source", "destination"])
async def test_a_new_leading_source_or_destination_evaluates_the_group_again(
    env: WorkflowEnvironment, kind: str
) -> None:
    now = await env.get_current_time()

    def summary(*leading: tuple[str, int]) -> GroupSummary:
        field = "sources" if kind == "source" else "destinations"
        return group_summary(6, at=now, **{field: leading})  # pyright: ignore[reportArgumentType]

    fakes = spray(now, window=timedelta(hours=10))
    fakes.summary = summary(("203.0.113.7", 4), ("203.0.113.8", 2))
    async with running_group(env, fakes) as handle:
        await env.sleep(SETTLE)
        await fakes.events.wait_for("decided", 1)
        # A tie leads nowhere: no new leader, nothing to do.
        await wake(handle, fakes, summary=summary(("203.0.113.7", 4), ("203.0.113.8", 4)))
        await env.sleep(timedelta(minutes=40))
        assert triage_numbers(fakes) == [1]
        await wake(handle, fakes, summary=summary(("203.0.113.8", 5), ("203.0.113.7", 4)))
        await env.sleep(timedelta(minutes=1))
        await fakes.events.wait_for("decided", 2)
        await handle.terminate()

    assert triage_numbers(fakes) == [1, 2]


# --- an evaluation without a decision ----------------------------------------------------------


async def test_without_a_decision_no_note_is_written_until_one_comes(
    env: WorkflowEnvironment,
) -> None:
    """The group note has no form for "no decision": the offenses wait. The group is evaluated
    again when it takes another offense, once the retry wait has passed."""
    now = await env.get_current_time()

    async def first_fails(call: TriageCall) -> TriageResult:
        if call.evaluation_no == 1:
            raise triage_failure(TriageFailure.INVALID_OUTPUT)
        return triage_result()

    fakes = spray(now, grouped=(EXAMPLE, 202), triage_behavior=first_fails)
    async with running_group(env, fakes) as handle:
        await env.sleep(SETTLE)
        await fakes.events.wait_for("no_ai_decision", 1)
        state = await handle.query(GroupCaseWorkflow.state)
        assert state.status is CaseStatus.NO_AI_DECISION
        await wake(handle, fakes, grouped=[EXAMPLE, 202, 203])
        await env.sleep(timedelta(minutes=5))
        await fakes.events.wait_for("decided", 2)
        notes = await notes_when(fakes, lambda found: len(found) == 3)
        await handle.terminate()

    assert notes == [(EXAMPLE, 2, "suspicious"), (202, 2, "suspicious"), (203, 2, "suspicious")]
    assert len(fakes.note_requests) == 3


async def test_a_group_without_an_offense_to_evaluate_waits_for_one(
    env: WorkflowEnvironment,
) -> None:
    """Every offense the group took was skipped by the catalog: nothing to evaluate until the
    group takes another one."""
    now = await env.get_current_time()
    fakes = spray(now)
    fakes.summary, fakes.grouped = None, []

    async with running_group(env, fakes) as handle:
        await env.sleep(SETTLE + timedelta(minutes=5))
        assert triage_numbers(fakes) == []
        await wake(handle, fakes, summary=group_summary(7, at=now), grouped=[203])
        await fakes.events.wait_for("decided", 1)
        await finish(handle)


async def test_a_continued_case_keeps_its_decision_and_its_notes(
    env: WorkflowEnvironment,
) -> None:
    """Continue-As-New: the next run writes the carried decision's note only on the offense
    that does not carry it yet, and keeps the next evaluation's time."""
    now = await env.get_current_time()
    content = note_content(
        case_id=CASE_ID,
        offense_id=EXAMPLE,
        evaluation_no=1,
        case_url=f"https://ais0c.example.com/cases/{CASE_ID}",
        verdict=CaseVerdict.FP,
        confidence=Confidence.HIGH,
        notify_level=Level.LOW,
        report=None,
    )
    carry = GroupCarry(
        storm_started_at=now - timedelta(hours=1),
        status=CaseStatus.DECIDED,
        evaluation_no=1,
        notify_level=Level.LOW,
        evaluated_at=now - timedelta(minutes=50),
        evaluated_count=6,
        evaluated_source=None,
        evaluated_destination=None,
        decision=GroupDecision(content=content, decided_at=now - timedelta(minutes=45)),
        noted={EXAMPLE: CaseVerdict.FP},
        window_end=now + timedelta(hours=2),
        due_at=now + timedelta(hours=23),
        updated=True,
    )
    fakes = spray(now, grouped=(EXAMPLE, 202))

    async with running_group(env, fakes, carry) as handle:
        notes = await notes_when(fakes, lambda found: len(found) == 1)
        state = await handle.query(GroupCaseWorkflow.state)
        await finish(handle)

    assert notes == [(202, 1, "fp")]
    assert (state.evaluation_no, state.noted) == (1, 2)
    assert triage_numbers(fakes) == []


# --- the end of the window, and replay -----------------------------------------------------------


async def test_the_case_ends_when_the_window_ends_and_an_offense_can_move_it(
    env: WorkflowEnvironment,
) -> None:
    now = await env.get_current_time()
    fakes = spray(now, window=timedelta(hours=1))

    async with running_group(env, fakes) as handle:
        await env.sleep(SETTLE)
        await fakes.events.wait_for("decided", 1)
        # An offense the group analyzed moved the window; the case finds out when it checks.
        fakes.window_end = now + timedelta(hours=3)
        await env.sleep(timedelta(hours=1))
        assert fakes.closed_at == []
        result = await finish(handle)

    assert result.status is CaseStatus.CLOSED
    assert fakes.closed_at[0] > now + timedelta(hours=3)


async def test_a_group_history_replays(env: WorkflowEnvironment) -> None:
    now = await env.get_current_time()
    fakes = spray(now, grouped=(EXAMPLE, 202))

    async with running_group(env, fakes) as handle:
        await env.sleep(SETTLE)
        await fakes.events.wait_for("decided", 1)
        await wake(handle, fakes, summary=group_summary(12, at=now), grouped=[EXAMPLE, 202, 203])
        await finish(handle)
        history = await handle.fetch_history()

    replayer = Replayer(
        workflows=list(CASE_QUEUE_WORKFLOWS), data_converter=pydantic_data_converter
    )
    await replayer.replay_workflow(history)
