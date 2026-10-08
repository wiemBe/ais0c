"""T-044 criterion 3: the Orchestrator returns a CasePlan of 1-4 steps and has no tools.

The agent checks the output's schema only. Whether the plan may run is the workflow's check
(ais0c_workflows.plan, decision T-41), so a plan that breaks those rules but fits the schema
comes back as it is.
"""

import inspect

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo

from ais0c_agents import OrchestratorOutput, build_orchestrator_agent
from ais0c_contracts import CasePlan, PlanStep, RunStatus

from .helpers import ScriptedModel, answer, retry_prompts, tool_returns
from .orchestrator_helpers import (
    DCSYNC,
    build_orchestrator,
    orchestrator_manifest,
    orchestrator_prompt,
    orchestrator_task,
    plan_output,
    plan_step,
    run_orchestrator,
)


def test_a_valid_plan_comes_back_as_a_case_plan() -> None:
    steps = [
        plan_step("investigation", skill=DCSYNC),
        plan_step("verification", budget={"tokens": 60000, "tool_calls": 10, "seconds": 150}),
    ]
    script = ScriptedModel(answer(plan_output(*steps)))

    run = run_orchestrator(build_orchestrator(script))

    assert run.status is RunStatus.COMPLETED
    assert run.error is None
    plan = run.result
    assert isinstance(plan, CasePlan)
    assert plan.task_id == "task-4711-orchestrator-1"
    assert (plan.claims, plan.data_gaps, plan.injection_suspected) == ([], [], True)
    assert plan.steps == [PlanStep.model_validate(step) for step in steps]
    assert plan.steps[0].skill_id == "windows-dcsync"
    assert plan.usage == run.usage
    assert (run.usage.tool_calls, run.usage.seconds) == (0, 1.5)
    assert (run.prompt_version, run.prompt_hash) == (
        "orchestrator/v2",
        orchestrator_prompt().sha256,
    )


def test_the_output_schema_is_a_case_plan_without_run_fields() -> None:
    schema = OrchestratorOutput.model_json_schema()

    assert schema["title"] == "CasePlan"
    assert set(schema["properties"]) == {"steps", "injection_suspected"}
    assert (
        schema["properties"]["steps"]["minItems"],
        schema["properties"]["steps"]["maxItems"],
    ) == (
        1,
        4,
    )
    assert "description" not in schema


# --- no tools --------------------------------------------------------------------------------------


def test_the_agent_has_no_tools() -> None:
    script = ScriptedModel(answer(plan_output()))

    run = run_orchestrator(build_orchestrator(script))

    [(_, info)] = script.requests
    assert info.function_tools == []
    assert [tool.name for tool in info.output_tools] == ["final_result"]
    assert [part.tool_name for part in tool_returns(run.messages)] == ["final_result"]


def test_the_builder_takes_no_gateway_or_toolset() -> None:
    parameters = set(inspect.signature(build_orchestrator_agent).parameters)

    assert parameters == {"manifest", "prompt", "model", "capabilities"}


def test_a_manifest_with_a_toolset_profile_is_refused() -> None:
    manifest = orchestrator_manifest().model_copy(update={"toolset_profile": "qradar-triage-read"})

    with pytest.raises(ValueError, match="the Orchestrator agent has no tools"):
        build_orchestrator(ScriptedModel(answer(plan_output())), manifest)


def test_a_tool_call_ends_the_run_before_anything_runs() -> None:
    # The tool call budget is 0, so the first call ends the run; the workflow then uses the
    # default plan (T-41).
    def call_a_tool(messages: object, info: AgentInfo) -> ModelResponse:
        return ModelResponse(
            parts=[ToolCallPart("create_ariel_search", {"query": "SELECT * FROM events"})]
        )

    script = ScriptedModel(call_a_tool, answer(plan_output()))

    run = run_orchestrator(build_orchestrator(script))

    assert run.status is RunStatus.BUDGET_EXHAUSTED
    assert run.result is None
    assert run.error is not None
    assert "tool_calls_limit of 0" in run.error
    assert len(script.requests) == 1
    assert list(tool_returns(run.messages)) == []


# --- the schema and nothing more --------------------------------------------------------------------


@pytest.mark.parametrize(
    "steps",
    [
        [],
        [plan_step("verification")] * 5,
        [plan_step("investigation", skill=DCSYNC) | {"skill_version": None}],
        [plan_step("verification", start="2026-10-02T13:00:00")],
        [plan_step("verification", objective="x" * 301)],
        [plan_step("verification") | {"budget": {"tokens": 1000}}],
    ],
    ids=[
        "no-steps",
        "five-steps",
        "skill-without-version",
        "naive-time",
        "long-objective",
        "budget",
    ],
)
def test_output_that_breaks_the_schema_gets_a_retry(steps: list[dict[str, object]]) -> None:
    script = ScriptedModel(
        answer({"steps": steps, "injection_suspected": False}), answer(plan_output())
    )

    run = run_orchestrator(build_orchestrator(script))

    assert run.status is RunStatus.COMPLETED
    assert len(retry_prompts(run.messages)) == 1
    assert run.result is not None
    assert len(run.result.steps) == 2


def test_output_that_stays_invalid_fails_the_run() -> None:
    script = ScriptedModel(answer({"steps": [], "injection_suspected": False}))

    run = run_orchestrator(build_orchestrator(script))

    assert run.status is RunStatus.FAILED
    assert run.result is None
    # Two output retries: three requests, within max_steps (4).
    assert len(script.requests) == 3


def text(messages: object, info: AgentInfo) -> ModelResponse:
    return ModelResponse(parts=[TextPart("I would investigate first.")])


def test_text_instead_of_a_plan_fails_the_run() -> None:
    script = ScriptedModel(text)

    run = run_orchestrator(build_orchestrator(script))

    assert run.status is RunStatus.FAILED
    assert run.result is None
    assert len(script.requests) == 3
    assert all(
        "Please include your response in a tool call" in retry.model_response()
        for retry in retry_prompts(run.messages)
    )


def test_the_step_limit_ends_the_run_as_budget_exhausted() -> None:
    script = ScriptedModel(text)

    run = run_orchestrator(build_orchestrator(script, orchestrator_manifest(max_steps=2)))

    assert run.status is RunStatus.BUDGET_EXHAUSTED
    assert run.error is not None
    assert "request_limit of 2" in run.error
    assert len(script.requests) == 2


@pytest.mark.parametrize(
    "steps",
    [
        # Triage and Reporting as steps, an agent twice, a skill that is not a candidate and a
        # budget far above the manifest's: all for the workflow to reject or cut.
        [plan_step("triage"), plan_step("reporting")],
        [plan_step("investigation"), plan_step("investigation")],
        [plan_step("investigation") | {"skill_id": "made-up", "skill_version": "9.9.9"}],
        [plan_step("verification", budget={"tokens": 10**9, "tool_calls": 999, "seconds": 99999})],
        [plan_step("verification", objective="")],
    ],
)
def test_the_agent_checks_the_schema_only(steps: list[dict[str, object]]) -> None:
    script = ScriptedModel(answer({"steps": steps, "injection_suspected": False}))

    run = run_orchestrator(build_orchestrator(script))

    assert run.status is RunStatus.COMPLETED
    assert retry_prompts(run.messages) == []
    assert run.result is not None
    assert [step.model_dump(mode="json") for step in run.result.steps] == [
        PlanStep.model_validate(step).model_dump(mode="json") for step in steps
    ]


def test_a_task_for_another_agent_is_refused() -> None:
    agent = build_orchestrator(ScriptedModel(answer(plan_output())))

    with pytest.raises(ValueError, match="task is for agent 'triage', not 'orchestrator'"):
        run_orchestrator(agent, orchestrator_task(agent_id="triage"))
