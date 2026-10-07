"""T-048 criterion 1 (decision T-52), T-051: the run keeps the agent's last answer.

Once the tool call budget is used up, or fewer tokens remain than three times what the next
request costs at least, the next request offers no function tool and tells the model to answer. A run
that answers then completes, with a `budget_exhausted` data gap; a model that calls a tool
anyway ends `budget_exhausted` as before. Agents without tools are not touched.

The next request carries the whole conversation again, so what it costs at least is the last
request's total tokens plus the tool results that came since. T-051: case-34's Verification run
lost its answer because a tool result made the next request cost more than twice the last one
while the rule still offered the tools.
"""

from collections.abc import Sequence

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo
from pydantic_ai.usage import RequestUsage, RunUsage, UsageLimits

from ais0c_agents import AgentRun, runner
from ais0c_agents.runner import (
    FINAL_ANSWER_PROMPT,
    REQUEST_RESERVE,
    TOKEN_RESERVE_FACTOR,
    TOOL_RESULT_CHARS_PER_TOKEN,
    budget_spent,
)
from ais0c_agents.toolset import render_tool_result
from ais0c_contracts import DataGap, DataGapReason, RunStatus, ToolResult, VerificationResult

from .helpers import (
    DOUBTED_CLAIM,
    END,
    NONCE,
    START,
    ScriptedModel,
    Step,
    alias,
    answer,
    build,
    build_verification,
    call,
    disagreement,
    gateway,
    ok,
    run_triage,
    run_verification,
    triage_manifest,
    triage_output,
    verification_agent_task,
    verification_gateway,
    verification_manifest,
    verification_output,
    verification_task,
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


def while_tools_remain(tool_step: Step, answer_step: Step) -> Step:
    """`tool_step` while the request still offers tools, `answer_step` once they are withdrawn.

    What a real model does depends on the tools it is offered, so the script has to look at them:
    this is the branch the threshold decides, and the tests below need it.
    """

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        return (tool_step if info.function_tools else answer_step)(messages, info)

    return respond


def while_answer_is_new(first: Step, later: Step) -> Step:
    """`first` the first time the model answers, `later` for every answer after it."""
    answers = 0

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal answers
        answers += 1
        return (first if answers == 1 else later)(messages, info)

    return respond


def tool_return(text: str) -> ModelRequest:
    """The request the model reads a tool result of `text` characters back in."""
    return ModelRequest(parts=[ToolReturnPart(tool_name="get_ariel_search_results", content=text)])


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
    # 14 000 tokens: after two requests of 3 000, 8 000 remain, less than three times the last one.
    script = ScriptedModel(
        costing(3000, call("get_offense", offense_id=4711)),
        costing(3000, call("get_rule", rule_id=100234)),
        costing(
            1000, answer(triage_output(alias(1), data_gaps=[MODEL_GAP.model_dump(mode="json")]))
        ),
    )
    fake = gateway()

    run = run_triage(build(script, fake, triage_manifest(tokens=14000)))

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

    run = run_triage(build(script, fake, triage_manifest(tokens=14000)))

    assert run.status is RunStatus.BUDGET_EXHAUSTED
    assert run.result is None
    assert run.error is not None
    assert "total_tokens_limit of 14000" in run.error
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


# --- case-34: the run that lost its answer (T-051 criterion 1) -------------------------------------

# The Verification run of QRadar lab offense 34 on 2026-10-06 (main 530c01e), request by request:
# what each model request cost, as the Temporal history of `case-34-verification-1` records it in
# its `agent__verification__model_request` activities' usage. 82 377 tokens over 8 requests, 6
# tool calls, and the run ended `budget_exhausted` with no result, so the case fell to QA as
# `verifier_conflict`. The seventh request answered; its `claims` field held a string instead of
# a list, so Pydantic AI sent it back and the eighth request corrected it. That correction cost
# more than the 80 000 tokens the run had left, and Pydantic AI checks the limit after a response
# arrives, so the corrected answer was thrown away.
CASE_34_REQUESTS: tuple[int, ...] = (8016, 7946, 8463, 9018, 9628, 11734, 13724, 13848)


def test_case_34_keeps_the_answer_its_eighth_request_corrected() -> None:
    manifest = verification_manifest()
    assert manifest.budgets.tokens > sum(CASE_34_REQUESTS), (
        "the manifest's token budget must cover what this run measured, or its answer is lost"
    )
    task = verification_task()
    task = task.model_copy(update={"task": verification_agent_task(tokens=manifest.budgets.tokens)})
    # The first six requests are the Ariel search lifecycle of the recorded run.
    script = ScriptedModel(
        *(
            costing(
                tokens,
                call(
                    tool,
                    **(
                        {"query_expression": "SELECT 1"}
                        if tool in {"create_ariel_search"}
                        else {"search_id": "search-1"}
                    ),
                ),
            )
            for tokens, tool in zip(
                CASE_34_REQUESTS[:6],
                (
                    "create_ariel_search",
                    "create_ariel_search",
                    "get_ariel_search_status",
                    "get_ariel_search_results",
                    "create_ariel_search",
                    "delete_ariel_search",
                ),
                strict=True,
            )
        ),
        # The seventh request answers, but its `claims` field is the string the model wrote.
        costing(CASE_34_REQUESTS[6], answer(verification_output(alias(5), claims="[]"))),
        # The eighth request corrects it and names the claim it contests.
        costing(
            CASE_34_REQUESTS[7],
            answer(
                verification_output(
                    alias(5), agrees=False, disagreements=[disagreement(DOUBTED_CLAIM)]
                )
            ),
        ),
    )

    run = run_verification(
        build_verification(script, verification_gateway(), manifest),
        task,
    )

    assert run.status is RunStatus.COMPLETED, run.error
    assert run.result is not None
    # The corrected answer is the one that is kept, not the one the validator sent back.
    assert [item.claim_text for item in run.result.disagreements] == [DOUBTED_CLAIM]
    assert run.usage.tokens == sum(CASE_34_REQUESTS)
    # The budget paid for the whole run, so there is nothing to report as spent.
    assert run.result.data_gaps == []


# --- a tool result that costs more than the request that read it (T-051 criterion 2) ---------------

# The verify profile's Ariel results carry at most 200 rows, and the gateway wraps them in one
# untrusted block. Case-34's tool results were small (121 to 328 tokens), but a result set of
# that shape can be bigger than the request that read it, and the next request sends it all
# again: the cost that decides the reserve is the request's own tokens plus the result.
BIG_RESULT = ok(
    "ev_01JB3K4M5N6P7Q8R9W",
    *(
        {
            "sourceip": "203.0.113.77",
            "username": "svc_backup_7731",
            "qid": "5000849",
            "summary": "An event the gateway masked as it filtered the result set.",
        }
        for _ in range(120)
    ),
)
"""A result set of the profile's shape: many rows in one untrusted block."""


def result_tokens(result: ToolResult) -> int:
    """How many tokens the model reads `result` back in, as the rule counts it."""
    return (
        len(
            render_tool_result(
                result,
                source="qradar.get_ariel_search_results",
                nonce=NONCE,
                alias="ev_1",
            )
        )
        // TOOL_RESULT_CHARS_PER_TOKEN
    )


def test_a_tool_result_bigger_than_the_request_ends_the_run_early_enough_to_answer() -> None:
    # The budget pays for two requests of `BASE + result`, which is the tool call and the answer
    # after it. Without the result in the estimate, the reserve after the first request is only
    # twice BASE, so the second request still offers the tools: the model reads a second result
    # the same size, and the request after that costs more than the budget, so the run ends
    # `budget_exhausted` with no result. With the result counted, the reserve does not fit, the
    # model answers at once, and the answer is kept with a `budget_exhausted` gap.
    base = 1000
    big = result_tokens(BIG_RESULT)
    budget = 2 * (base + big)
    # The gateway answers the result call with the big set, so the rule reads its real size.
    fake = verification_gateway(get_ariel_search_results=[BIG_RESULT, BIG_RESULT])
    script = ScriptedModel(
        costing(base, call("get_ariel_search_results", search_id="search-1")),
        costing(
            base + big,
            while_tools_remain(
                call("get_ariel_search_results", search_id="search-2"),
                answer(verification_output(alias(1))),
            ),
        ),
        costing(base + 2 * big, answer(verification_output(alias(2)))),
    )
    task = verification_task().model_copy(update={"task": verification_agent_task(tokens=budget)})

    run = run_verification(build_verification(script, fake), task)

    assert run.status is RunStatus.COMPLETED, run.error
    # The tools went after the first request, so only one result was read.
    assert [bool(tools) for tools in offered_tools(script)] == [True, False]
    assert told_to_answer(script) == [False, True]
    assert run.result is not None
    assert run.result.data_gaps == [budget_gap("verification")]
    # Two requests: the tool call and the answer. The second one fits the budget exactly.
    assert run.usage.tokens == 2 * base + big
    assert run.usage.tokens <= budget
    # The result is what made the reserve matter: the run could not have afforded the request
    # that read it a second time, and the old estimate said it could.
    assert big > TOKEN_RESERVE_FACTOR * base
    assert base + big + base + 2 * big > budget


# --- the rule -----------------------------------------------------------------------------------


def response(tokens: int) -> ModelResponse:
    return ModelResponse(parts=[], usage=RequestUsage(input_tokens=tokens))


def test_the_rule_counts_the_tool_result_that_came_after_the_last_request() -> None:
    limits = UsageLimits(total_tokens_limit=20000)
    last = ModelResponse(parts=[], usage=RequestUsage(input_tokens=2300))
    text = "x" * (10_000 * TOOL_RESULT_CHARS_PER_TOKEN)

    assert not budget_spent(RunUsage(input_tokens=2300), limits, [last])
    # 17 700 left, three times the last request is 6 900, three times the next request's floor is 39 900.
    assert budget_spent(RunUsage(input_tokens=2300), limits, [last, tool_return(text)])
    # A result that only a part of the reserve fills leaves the run its tools.
    short = ModelRequest(
        parts=[ToolReturnPart(tool_name="x", content="x" * (1_000 * TOOL_RESULT_CHARS_PER_TOKEN))]
    )
    assert not budget_spent(RunUsage(input_tokens=2300), limits, [last, short])
    # Without a response there is nothing to base the estimate on.
    assert not budget_spent(RunUsage(), limits, [tool_return(text)])


def test_the_rule_reads_the_last_request_and_all_three_budgets() -> None:
    limits = UsageLimits(total_tokens_limit=10000, tool_calls_limit=5)

    def spent(total: int, last: int, tool_calls: int = 0) -> bool:
        usage = RunUsage(input_tokens=total, tool_calls=tool_calls)
        return budget_spent(usage, limits, [response(1), response(last)])

    assert not spent(total=0, last=0)
    assert not spent(total=1000, last=3000)  # 9000 left, three times the last is 9000
    assert spent(total=1001, last=3000)
    assert spent(total=100, last=100, tool_calls=5)
    assert not spent(total=100, last=100, tool_calls=4)
    assert not budget_spent(RunUsage(input_tokens=10**9), UsageLimits(total_tokens_limit=None), [])
    assert not budget_spent(RunUsage(), None, [])

    requests = UsageLimits(request_limit=3)  # the answer and one correction stay
    assert not budget_spent(RunUsage(requests=0), requests, [])
    assert budget_spent(RunUsage(requests=1), requests, [])


def test_with_one_model_request_left_the_run_returns_a_budget_gap() -> None:
    script = ScriptedModel(
        call("get_offense", offense_id=4711),
        answer(triage_output(alias(1))),
    )

    run = run_triage(build(script, gateway(), triage_manifest(max_steps=3)))

    assert run.status is RunStatus.COMPLETED
    assert [bool(tools) for tools in offered_tools(script)] == [True, False]
    assert told_to_answer(script) == [False, True]
    assert run.result is not None
    assert run.result.data_gaps == [budget_gap("triage")]


# --- case-36: a correction that crossed the budget (T-056 criterion 1, 2) --------------------------

# The Verification run of QRadar lab offense 36 on 2026-10-07 (main 8d33c73), request by request:
# each request's input and output tokens, as the Temporal history of `case-36-verification-1`
# records them in its `agent__verification__model_request` activities' usage. The first nine
# requests (96 695 tokens) were Ariel search calls; the tenth was `final_result` with a `reason`
# over 300 characters, and the eleventh was Pydantic AI's correction request. The tenth request
# was the first the old rule (factor 2) withdrew the tools at: 23 305 tokens were left, less
# than twice the 13 950 of the ninth request. Its answer needed a correction, and 110 076 plus
# the correction's 12 901 crossed the 120 000 limit, so the corrected answer was thrown away.
CASE_36_REQUESTS: tuple[int, ...] = (
    7424 + 657,
    8468 + 138,
    9009 + 160,
    9503 + 405,
    10020 + 625,
    11031 + 131,
    11567 + 173,
    12678 + 756,
    13828 + 122,
    11126 + 2255,
    11927 + 974,
)
LONG_REASON = "The evidence shows another account. " * 9  # 324 characters, over the limit of 300


def case_36_step(costs: Sequence[int]) -> Step:
    """The recorded run: search calls while the tools are offered, then an answer.

    The first answer carries a `reason` over the length limit and the next one is the model's
    correction; which request that is depends on where the rule withdraws the tools.
    """
    requests = 0
    answered = False

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal requests, answered
        cost = costs[min(requests, len(costs) - 1)]
        requests += 1
        if info.function_tools:
            step = call("get_ariel_search_status", search_id="search-1")
        else:
            reason = "The account is a user account." if answered else LONG_REASON
            answered = True
            step = answer(
                verification_output(
                    alias(1),
                    agrees=False,
                    disagreements=[disagreement(DOUBTED_CLAIM, reason)],
                )
            )
        return costing(cost, step)(messages, info)

    return respond


def run_case_36() -> tuple[ScriptedModel, AgentRun[VerificationResult]]:
    manifest = verification_manifest()
    assert manifest.budgets.tokens == 120000
    script = ScriptedModel(case_36_step(CASE_36_REQUESTS))
    task = verification_task().model_copy(
        update={"task": verification_agent_task(tokens=manifest.budgets.tokens)}
    )
    return script, run_verification(
        build_verification(script, verification_gateway(), manifest), task
    )


def test_case_36_with_the_old_reserve_loses_the_corrected_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "TOKEN_RESERVE_FACTOR", 2)

    script, run = run_case_36()

    assert run.status is RunStatus.BUDGET_EXHAUSTED
    assert run.result is None
    assert run.error is not None
    assert "total_tokens_limit of 120000" in run.error
    assert len(script.requests) == 11
    assert [bool(tools) for tools in offered_tools(script)] == [True] * 9 + [False] * 2


def test_case_36_keeps_the_answer_with_a_reserve_for_one_correction() -> None:
    script, run = run_case_36()

    assert run.status is RunStatus.COMPLETED, run.error
    assert run.result is not None
    assert [item.reason for item in run.result.disagreements] == ["The account is a user account."]
    assert run.result.data_gaps == [budget_gap("verification")]
    # After the eighth request 37 255 tokens were left, less than three times the 13 434 it cost:
    # the tools go at the ninth request, one request earlier than with the old reserve.
    assert [bool(tools) for tools in offered_tools(script)] == [True] * 8 + [False] * 2
    assert told_to_answer(script) == [False] * 8 + [True] * 2
    # The ninth request answered, the tenth corrected it: two requests fewer than the record.
    assert len(script.requests) == 10
    assert run.usage.tokens == sum(CASE_36_REQUESTS[:8]) + CASE_36_REQUESTS[8] + CASE_36_REQUESTS[9]
    assert run.usage.tokens <= 120000
    assert TOKEN_RESERVE_FACTOR == 3


# --- the request limit keeps the answer and one correction (T-056 criterion 2) ---------------------


@pytest.mark.parametrize(
    ("reserve", "status"),
    [(1, RunStatus.BUDGET_EXHAUSTED), (REQUEST_RESERVE, RunStatus.COMPLETED)],
)
def test_at_the_request_limit_an_answer_that_needs_a_correction_is_corrected(
    monkeypatch: pytest.MonkeyPatch, reserve: int, status: RunStatus
) -> None:
    # `max_steps` 4: a reserve of one (the old rule) offers the tools to the third request, whose
    # answer is sent back and the correction would be a fifth request. A reserve of two withdraws
    # them at the third request, so answer and correction are the third and the fourth.
    monkeypatch.setattr(runner, "REQUEST_RESERVE", reserve)
    script = ScriptedModel(
        call("get_offense", offense_id=4711),
        call("get_rule", rule_id=100234),
        while_tools_remain(
            call("get_rule", rule_id=100234),
            while_answer_is_new(
                answer(triage_output("ev_9")),  # an alias the run never made: sent back
                answer(triage_output(alias(1))),
            ),
        ),
    )

    run = run_triage(build(script, gateway(), triage_manifest(max_steps=4)))

    assert run.status is status, run.error
    if status is RunStatus.COMPLETED:
        assert run.result is not None
        assert run.result.data_gaps == [budget_gap("triage")]
        assert [bool(tools) for tools in offered_tools(script)] == [True, True, False, False]
    else:
        assert run.result is None
