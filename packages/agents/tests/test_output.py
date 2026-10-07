"""Acceptance criterion 8: output that does not fit TriageResult goes back to the model.

Pydantic AI's output retries give the model another try; when they run out, the run ends as
`failed` without a result.
"""

import pytest

from ais0c_agents import TriageOutput
from ais0c_agents.triage import OUTPUT_RETRIES
from ais0c_contracts import RunStatus, TriageResult

from .helpers import (
    OFFENSE_EVIDENCE,
    ScriptedModel,
    alias,
    answer,
    build,
    call,
    gateway,
    retry_prompts,
    run_triage,
    triage_output,
    triage_prompt,
)

RUN_FIELDS = {"task_id", "status", "usage"}


def without(field: str) -> dict[str, object]:
    output = triage_output(alias(1))
    del output[field]
    return output


INVALID_OUTPUTS = [
    pytest.param(triage_output(alias(1), verdict="maybe"), id="unknown verdict"),
    pytest.param(triage_output(alias(1), ai_level="severe"), id="unknown level"),
    pytest.param(triage_output(alias(1), rationale="x" * 601), id="rationale too long"),
    pytest.param(
        triage_output(alias(1), investigation_focus=["Check logons."] * 6),
        id="six focus items",
    ),
    pytest.param(
        triage_output(claims=[{"text": "No evidence.", "evidence_ids": []}]),
        id="claim without evidence",
    ),
    pytest.param(
        triage_output(claims=[{"text": "Bad ID.", "evidence_ids": ["01JB3K4M5N6P7Q8R9S"]}]),
        id="evidence ID without prefix",
    ),
    pytest.param(without("rationale"), id="missing rationale"),
    pytest.param(without("injection_suspected"), id="missing injection flag"),
    pytest.param(triage_output(alias(1), status="completed"), id="sets run status"),
    pytest.param(
        triage_output(alias(1), usage={"tokens": 0, "tool_calls": 0, "seconds": 0}),
        id="sets usage",
    ),
]


@pytest.mark.parametrize("invalid", INVALID_OUTPUTS)
def test_invalid_output_goes_back_to_the_model(invalid: dict[str, object]) -> None:
    script = ScriptedModel(
        call("get_offense", offense_id=4711),
        answer(invalid),
        answer(triage_output(alias(1))),
    )

    run = run_triage(build(script, gateway()))

    assert run.status is RunStatus.COMPLETED
    [retry] = retry_prompts(run.messages)
    assert retry.tool_name == "final_result"
    assert run.result is not None
    assert run.result.verdict == "suspicious"


@pytest.mark.parametrize("invalid", INVALID_OUTPUTS[:3])
def test_output_that_stays_invalid_fails_the_run(invalid: dict[str, object]) -> None:
    script = ScriptedModel(call("get_offense", offense_id=4711), answer(invalid))

    run = run_triage(build(script, gateway()))

    assert run.status is RunStatus.FAILED
    assert run.result is None
    assert run.error is not None
    assert "output retries" in run.error
    assert len(retry_prompts(run.messages)) == OUTPUT_RETRIES


def test_completed_run_returns_a_triage_result() -> None:
    script = ScriptedModel(call("get_offense", offense_id=4711), answer(triage_output(alias(1))))

    run = run_triage(build(script, gateway()))

    result = run.result
    assert run.status is RunStatus.COMPLETED
    assert isinstance(result, TriageResult)
    assert TriageResult.model_validate(result.model_dump()) == result
    assert (result.task_id, result.status) == ("task-4711-1", RunStatus.COMPLETED)
    assert result.usage == run.usage
    assert (result.usage.tool_calls, result.usage.seconds) == (1, 1.5)
    assert (
        result.model_dump(exclude=RUN_FIELDS)
        == TriageOutput.model_validate(triage_output(OFFENSE_EVIDENCE)).model_dump()
    )
    assert (run.prompt_version, run.prompt_hash) == ("triage/v3", triage_prompt().sha256)


def test_model_output_is_triage_result_without_the_run_fields() -> None:
    assert set(TriageOutput.model_fields) == set(TriageResult.model_fields) - RUN_FIELDS
    for name, field in TriageOutput.model_fields.items():
        expected = TriageResult.model_fields[name]
        assert (field.annotation, field.metadata) == (expected.annotation, expected.metadata)
    assert TriageOutput.model_config.get("extra") == "forbid"


def test_model_sees_the_output_schema_as_triage_result() -> None:
    script = ScriptedModel(answer(triage_output()))

    run_triage(build(script, gateway()))

    [(_, info)] = script.requests
    [output_tool] = info.output_tools
    assert output_tool.parameters_json_schema["title"] == "TriageResult"
    assert output_tool.description == "Return the TriageResult for this offense."
