"""T-048 criterion 1 (decision T-52): the run keeps the agent's last answer.

Once the tool call budget is used up, or fewer tokens remain than twice the last request's
total, the next request offers no function tool and tells the model to answer. A run that
answers then completes, with a `budget_exhausted` data gap; a model that calls a tool anyway
ends `budget_exhausted` as before. Agents without tools are not touched.
"""

from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, UserPromptPart
from pydantic_ai.models.function import AgentInfo
from pydantic_ai.usage import RequestUsage, RunUsage, UsageLimits

from ais0c_agents.runner import FINAL_ANSWER_PROMPT, budget_spent
from ais0c_contracts import DataGap, DataGapReason, RunStatus

from .helpers import (
    END,
    START,
    ScriptedModel,
    Step,
    alias,
    answer,
    build,
    build_verification,
    call,
    gateway,
    run_triage,
    run_verification,
    triage_manifest,
    triage_output,
    verification_gateway,
    verification_manifest,
    verification_output,
)
from .investigation_helpers import (
    build_investigation,
    investigation_gateway,
    investigation_output,
    investigation_task,
    run_investigation,
)
from .orchestrator_helpers import build_orchestrator, plan_output, plan_step, run_orchestrator

MODEL_GAP = DataGap(
    source="Windows Security Event Log",
    period_start=START,
    period_end=END,
    reason=DataGapReason.NOT_PARSED,
)


def costing(tokens: int, step: Step) -> Step:
    """`step`, reporting `tokens` total tokens for its request."""

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        response = step(messages, info)
        response.usage = RequestUsage(input_tokens=tokens - 10, output_tokens=10)
        return response

    return respond


def offered_tools(script: ScriptedModel) -> list[list[str]]:
    """The function tools each request offered the model, by name."""
    return [[tool.name for tool in info.function_tools] for _, info in script.requests]


def told_to_answer(script: ScriptedModel) -> list[bool]:
    """Whether each request's last message carried FINAL_ANSWER_PROMPT."""
    return [_carries_prompt(messages[-1]) for messages, _ in script.requests]


def _carries_prompt(message: ModelMessage) -> bool:
    return isinstance(message, ModelRequest) and any(
        isinstance(part, UserPromptPart) and part.content == FINAL_ANSWER_PROMPT
        for part in message.parts
    )


def budget_gap(source: str) -> DataGap:
    return DataGap(
        source=source,
        period_start=START,
        period_end=END,
        reason=DataGapReason.BUDGET_EXHAUSTED,
    )


# --- the threshold ------------------------------------------------------------------------------


def test_near_the_token_budget_the_last_request_has_no_tool_and_the_answer_is_kept() -> None:
    # 10 000 tokens: after two requests of 3 000, 4 000 remain, less than twice the last one.
    script = ScriptedModel(
        costing(3000, call("get_offense", offense_id=4711)),
        costing(3000, call("get_rule", rule_id=100234)),
        costing(
            1000, answer(triage_output(alias(1), data_gaps=[MODEL_GAP.model_dump(mode="json")]))
        ),
    )
    fake = gateway()

    run = run_triage(build(script, fake, triage_manifest(tokens=10000)))

    assert run.status is RunStatus.COMPLETED, run.error
    assert (
        offered_tools(script)[:2]
        == [["get_offense", "get_rule", "list_log_sources", "list_assets"]] * 2
    )
    assert offered_tools(script)[2] == []
    assert [tool.name for tool in script.requests[2][1].output_tools] == ["final_result"]
    assert told_to_answer(script) == [False, False, True]
    assert run.result is not None
    # The model's own data gap stays; the run adds one for the budget.
    assert run.result.data_gaps == [MODEL_GAP, budget_gap("triage")]
    assert run.usage.tokens == 7000
    assert len(fake.intents) == 2


def test_with_the_tool_call_budget_used_up_the_next_request_can_only_answer() -> None:
    script = ScriptedModel(
        call("get_offense", offense_id=4711),
        call("get_rule", rule_id=100234),
        answer(triage_output(alias(1))),
    )

    run = run_triage(build(script, gateway(), triage_manifest(tool_calls=2)))

    assert run.status is RunStatus.COMPLETED
    assert [bool(tools) for tools in offered_tools(script)] == [True, True, False]
    assert told_to_answer(script) == [False, False, True]
    assert run.result is not None
    assert run.result.data_gaps == [budget_gap("triage")]


def test_once_withdrawn_the_tools_stay_withdrawn_through_an_output_retry() -> None:
    script = ScriptedModel(
        call("get_offense", offense_id=4711),
        answer(triage_output("ev_9")),  # an alias the run never made: sent back
        answer(triage_output(alias(1))),
    )

    run = run_triage(build(script, gateway(), triage_manifest(tool_calls=1)))

    assert run.status is RunStatus.COMPLETED
    assert offered_tools(script)[1:] == [[], []]
    assert told_to_answer(script) == [False, True, True]
    assert run.result is not None
    assert run.result.data_gaps == [budget_gap("triage")]


def test_investigation_and_verification_keep_their_answer_too() -> None:
    investigation = ScriptedModel(
        call("create_ariel_search", query_expression="SELECT 1"),
        answer(investigation_output(alias(1))),
    )
    verification = ScriptedModel(
        call("create_ariel_search", query_expression="SELECT 1"),
        answer(verification_output(alias(1))),
    )

    found = run_investigation(
        build_investigation(investigation, investigation_gateway()),
        investigation_task(tool_calls=1),
    )
    checked = run_verification(
        build_verification(
            verification,
            verification_gateway(),
            verification_manifest().model_copy(
                update={
                    "budgets": verification_manifest().budgets.model_copy(update={"tool_calls": 1})
                }
            ),
        )
    )

    assert found.status is checked.status is RunStatus.COMPLETED
    assert offered_tools(investigation)[1] == offered_tools(verification)[1] == []
    assert found.result is not None
    assert checked.result is not None
    assert found.result.data_gaps == [budget_gap("investigation")]
    assert checked.result.data_gaps == [budget_gap("verification")]


# --- unchanged runs -----------------------------------------------------------------------------


def test_a_run_below_the_threshold_is_unchanged() -> None:
    script = ScriptedModel(
        costing(3000, call("get_offense", offense_id=4711)),
        costing(3000, answer(triage_output(alias(1)))),
    )

    run = run_triage(build(script, gateway(), triage_manifest(tokens=60000)))

    assert run.status is RunStatus.COMPLETED
    assert all(offered_tools(script))
    assert told_to_answer(script) == [False, False]
    assert run.result is not None
    assert run.result.data_gaps == []


def test_agents_without_tools_are_not_touched() -> None:
    # The Orchestrator's tool call budget is 0, so by the rule its budget is spent from the
    # start; and after its first request of 15 000, 25 000 of its 40 000 tokens remain, less
    # than twice the last request. A schema retry makes the second request.
    script = ScriptedModel(
        costing(15000, answer({"steps": []})),
        costing(15000, answer(plan_output(plan_step("verification")))),
    )

    run = run_orchestrator(build_orchestrator(script))

    assert run.status is RunStatus.COMPLETED, run.error
    assert offered_tools(script) == [[], []]
    assert told_to_answer(script) == [False, False]
    assert run.result is not None
    assert run.result.data_gaps == []


# --- a model that does not answer ---------------------------------------------------------------


def test_a_model_that_calls_a_tool_after_the_token_threshold_ends_budget_exhausted() -> None:
    script = ScriptedModel(
        costing(3000, call("get_offense", offense_id=4711)),
        costing(3000, call("get_rule", rule_id=100234)),
        costing(3000, call("get_rule", rule_id=100234)),
    )
    fake = gateway()

    run = run_triage(build(script, fake, triage_manifest(tokens=10000)))

    assert run.status is RunStatus.BUDGET_EXHAUSTED
    assert run.result is None
    assert run.error is not None
    assert "total_tokens_limit of 10000" in run.error
    # The withdrawn tool never reached the gateway.
    assert len(fake.intents) == 2


def test_a_model_that_calls_a_tool_after_the_tool_budget_ends_budget_exhausted() -> None:
    script = ScriptedModel(*[call("get_offense", offense_id=4711)] * 3)
    fake = gateway()

    run = run_triage(build(script, fake, triage_manifest(tool_calls=2)))

    assert run.status is RunStatus.BUDGET_EXHAUSTED
    assert run.result is None
    assert run.error is not None
    assert "tool_calls_limit of 2" in run.error
    assert told_to_answer(script)[-1]
    assert len(fake.intents) == 2


# --- the rule -----------------------------------------------------------------------------------


def response(tokens: int) -> ModelResponse:
    return ModelResponse(parts=[], usage=RequestUsage(input_tokens=tokens))


def test_the_rule_reads_the_last_request_and_all_three_budgets() -> None:
    limits = UsageLimits(total_tokens_limit=10000, tool_calls_limit=5)

    def spent(total: int, last: int, tool_calls: int = 0) -> bool:
        usage = RunUsage(input_tokens=total, tool_calls=tool_calls)
        return budget_spent(usage, limits, [response(1), response(last)])

    assert not spent(total=0, last=0)
    assert not spent(total=4000, last=3000)  # 6000 left, twice the last is 6000
    assert spent(total=4001, last=3000)
    assert spent(total=100, last=100, tool_calls=5)
    assert not spent(total=100, last=100, tool_calls=4)
    assert not budget_spent(RunUsage(input_tokens=10**9), UsageLimits(total_tokens_limit=None), [])
    assert not budget_spent(RunUsage(), None, [])

    requests = UsageLimits(request_limit=3)
    assert not budget_spent(RunUsage(requests=1), requests, [])
    assert budget_spent(RunUsage(requests=2), requests, [])


def test_with_one_model_request_left_the_run_returns_a_budget_gap() -> None:
    script = ScriptedModel(
        call("get_offense", offense_id=4711),
        answer(triage_output(alias(1))),
    )

    run = run_triage(build(script, gateway(), triage_manifest(max_steps=2)))

    assert run.status is RunStatus.COMPLETED
    assert [bool(tools) for tools in offered_tools(script)] == [True, False]
    assert told_to_answer(script) == [False, True]
    assert run.result is not None
    assert run.result.data_gaps == [budget_gap("triage")]
