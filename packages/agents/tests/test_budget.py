"""Acceptance criterion 7: exceeding a budget ends the run as `budget_exhausted`.

Budgets are Pydantic AI usage limits; the agents package keeps no counters of its own.
"""

from ais0c_agents.runner import prompt_tool_budget, usage_limits
from ais0c_contracts import Budget, RunStatus

from .helpers import (
    ScriptedModel,
    Step,
    alias,
    answer,
    build,
    call,
    gateway,
    run_triage,
    triage_manifest,
    triage_output,
    triage_task,
)


def offense_calls(count: int) -> list[Step]:
    return [call("get_offense", offense_id=4711)] * count


def test_tool_call_limit_of_the_manifest_ends_the_run_as_budget_exhausted() -> None:
    fake = gateway()
    script = ScriptedModel(*offense_calls(3), answer(triage_output(alias(1))))

    run = run_triage(build(script, fake, triage_manifest(tool_calls=2)))

    assert run.status is RunStatus.BUDGET_EXHAUSTED
    assert run.result is None
    assert len(fake.intents) == 2
    assert run.usage.tool_calls == 2
    assert run.error is not None
    assert "tool_calls_limit of 2" in run.error


def test_run_within_the_tool_call_limit_completes() -> None:
    fake = gateway()
    script = ScriptedModel(*offense_calls(2), answer(triage_output(alias(1))))

    run = run_triage(build(script, fake, triage_manifest(tool_calls=2)))

    assert run.status is RunStatus.COMPLETED
    assert run.usage.tool_calls == len(fake.intents) == 2


def test_smaller_task_budget_applies() -> None:
    fake = gateway()
    script = ScriptedModel(*offense_calls(2), answer(triage_output(alias(1))))

    run = run_triage(build(script, fake), triage_task(tool_calls=1))

    assert run.status is RunStatus.BUDGET_EXHAUSTED
    assert len(fake.intents) == 1


def test_step_limit_ends_the_run_as_budget_exhausted() -> None:
    script = ScriptedModel(*offense_calls(2), answer(triage_output(alias(1))))

    run = run_triage(build(script, gateway(), triage_manifest(max_steps=2)))

    assert run.status is RunStatus.BUDGET_EXHAUSTED
    assert len(script.requests) == 2
    assert run.error is not None
    assert "request_limit of 2" in run.error


def test_token_limit_ends_the_run_as_budget_exhausted() -> None:
    script = ScriptedModel(*offense_calls(1), answer(triage_output(alias(1))))

    run = run_triage(build(script, gateway(), triage_manifest(tokens=100)))

    assert run.status is RunStatus.BUDGET_EXHAUSTED
    assert run.result is None
    assert run.usage.tokens > 100
    assert run.error is not None
    assert "total_tokens_limit of 100" in run.error


def test_exhausted_run_still_reports_its_usage() -> None:
    script = ScriptedModel(*offense_calls(3))

    run = run_triage(build(script, gateway(), triage_manifest(tool_calls=2)))

    assert run.usage.tool_calls == 2
    assert run.usage.tokens > 0
    assert run.usage.seconds == 1.5


def test_limits_take_the_smaller_of_manifest_and_task_budget() -> None:
    manifest = triage_manifest()  # max_steps 8, tool_calls 12, tokens 60000

    limits = usage_limits(manifest, Budget(tokens=1000, tool_calls=20, seconds=60))

    assert (limits.request_limit, limits.tool_calls_limit, limits.total_tokens_limit) == (
        8,
        12,
        1000,
    )


def test_prompt_states_the_tool_call_budget() -> None:
    script = ScriptedModel(answer(triage_output()))

    run_triage(build(script, gateway(), triage_manifest(tool_calls=2)))

    [(_, info)] = script.requests
    assert "Budget: at most 2 tool calls." in (info.instructions or "")


def test_prompt_budget_leaves_two_steps_for_the_answer() -> None:
    task_budget = Budget(tokens=60000, tool_calls=12, seconds=180)

    assert (
        prompt_tool_budget(triage_manifest(), task_budget) == 6
    )  # max_steps 8: the answer and one correction are kept back
    assert prompt_tool_budget(triage_manifest(max_steps=20), task_budget) == 12
    assert prompt_tool_budget(triage_manifest(max_steps=2), task_budget) == 0
