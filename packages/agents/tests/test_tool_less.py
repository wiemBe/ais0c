"""T-043 criterion 7: an agent without tools (Orchestrator, Reporting).

Its manifest has `toolset_profile: null` and may have a tool call budget of 0; a manifest with
a profile may not (test_manifest.py). run_agent runs it with the same budgets and status
mapping as an agent with tools: completed, budget_exhausted at the step limit, failed on output
that stays invalid.
"""

import pytest
from pydantic_ai.models.test import TestModel

from ais0c_agents import check_agent_config, prompt_tool_budget, usage_limits
from ais0c_contracts import Budget, RunStatus

from .helpers import (
    CONTEXT_EVIDENCE,
    PROFILES,
    SUMMARY_BUDGET,
    SUMMARY_PROMPT,
    ScriptedModel,
    answer,
    context_evidence,
    retry_prompts,
    run_summary,
    summary_manifest,
    summary_output,
    summary_spec,
)


def test_an_agent_without_tools_completes_with_a_zero_tool_call_budget() -> None:
    script = ScriptedModel(answer(summary_output("ev_c1")))

    run = run_summary(script.model, evidence=context_evidence())

    assert run.status is RunStatus.COMPLETED
    assert run.error is None
    assert run.result is not None
    assert run.result.claims[0].evidence_ids == [CONTEXT_EVIDENCE[0]]
    assert (run.usage.tool_calls, run.usage.seconds) == (0, 1.5)
    assert run.usage.tokens > 0
    assert (run.prompt_version, run.prompt_hash) == ("summary/v1", SUMMARY_PROMPT.sha256)
    [(_, info)] = script.requests
    assert info.function_tools == []
    assert [tool.name for tool in info.output_tools] == ["final_result"]


def test_an_agent_without_tools_has_no_tool_calls_in_its_limits_or_prompt() -> None:
    manifest = summary_manifest()

    limits = usage_limits(manifest, SUMMARY_BUDGET)

    assert manifest.toolset_profile is None
    assert (limits.tool_calls_limit, limits.request_limit) == (0, 4)
    assert prompt_tool_budget(manifest, SUMMARY_BUDGET) == 0


def test_the_step_limit_ends_the_run_as_budget_exhausted() -> None:
    # Output retries would allow more requests than max_steps: the step limit binds first.
    script = ScriptedModel(answer({"summary": 123, "claims": []}))

    run = run_summary(
        script.model, manifest=summary_manifest(max_steps=2), spec=summary_spec(output_retries=5)
    )

    assert run.status is RunStatus.BUDGET_EXHAUSTED
    assert run.result is None
    assert run.error is not None
    assert "request_limit of 2" in run.error
    assert len(script.requests) == 2
    assert retry_prompts(run.messages)
    assert run.usage.tool_calls == 0


def test_the_task_token_budget_ends_the_run_as_budget_exhausted() -> None:
    model = TestModel(custom_output_args=summary_output())

    run = run_summary(model, budget=Budget(tokens=1, tool_calls=0, seconds=120))

    assert run.status is RunStatus.BUDGET_EXHAUSTED
    assert run.error is not None
    assert "total_tokens_limit of 1" in run.error


def test_output_that_stays_invalid_fails_the_run() -> None:
    script = ScriptedModel(answer({"summary": 123, "claims": []}))

    run = run_summary(script.model, spec=summary_spec(output_retries=1))

    assert run.status is RunStatus.FAILED
    assert run.result is None
    assert run.error is not None
    assert "output retries" in run.error
    assert len(script.requests) == 2
    assert len(retry_prompts(run.messages)) == 1


def test_citations_that_stay_invalid_fail_the_run() -> None:
    script = ScriptedModel(answer(summary_output("ev_c3")))

    run = run_summary(
        script.model, evidence=context_evidence(), spec=summary_spec(output_retries=1)
    )

    assert run.status is RunStatus.FAILED
    assert run.result is None


# --- configuration --------------------------------------------------------------------------


def test_an_agent_without_tools_refuses_a_manifest_with_a_profile() -> None:
    manifest = summary_manifest(toolset_profile="qradar-triage-read")

    with pytest.raises(ValueError, match="names toolset profile 'qradar-triage-read'; the Summary"):
        check_agent_config(summary_spec(), manifest, SUMMARY_PROMPT)


def test_an_agent_with_tools_refuses_a_manifest_without_a_profile() -> None:
    with pytest.raises(ValueError, match="unknown toolset profile None"):
        check_agent_config(summary_spec(), summary_manifest(), SUMMARY_PROMPT, PROFILES)


def test_the_config_check_returns_the_profile_of_an_agent_with_tools() -> None:
    manifest = summary_manifest(toolset_profile="qradar-triage-read")

    profile = check_agent_config(summary_spec(), manifest, SUMMARY_PROMPT, PROFILES)

    assert profile is PROFILES["qradar-triage-read"]


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"input_schema": "TriageTask"}, "declares TriageTask -> CaseSummary"),
        ({"prompt": "prompts/triage/v2.md"}, "uses prompts/triage/v2.md, not"),
        ({"shared_rules": "prompts/_shared/rules/v1.md"}, "uses prompts/_shared/rules/v1.md"),
    ],
)
def test_the_config_check_refuses_another_agents_manifest(
    update: dict[str, str], message: str
) -> None:
    manifest = summary_manifest().model_copy(update=update)

    with pytest.raises(ValueError, match=message):
        check_agent_config(summary_spec(), manifest, SUMMARY_PROMPT)


def test_the_config_check_refuses_a_prompt_without_the_agents_inputs() -> None:
    spec = summary_spec()
    prompt = SUMMARY_PROMPT.__class__(
        path=SUMMARY_PROMPT.path,
        template="{{ shared_rules }}\n{{ evidence }}\n",
        shared_rules_path=SUMMARY_PROMPT.shared_rules_path,
        shared_rules=SUMMARY_PROMPT.shared_rules,
        sha256=SUMMARY_PROMPT.sha256,
    )

    with pytest.raises(ValueError, match="takes evidence; the Summary agent fills evidence, skill"):
        check_agent_config(spec, summary_manifest(), prompt)
