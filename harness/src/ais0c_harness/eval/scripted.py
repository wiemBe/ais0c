"""A scripted model that plays a Triage scenario through the runner, without a real model.

It calls each tool the scenario has results for, once per result and in the scenario's order,
with arguments that fit the tool's schema, then returns an answer that meets the scenario's
expectation. The suite tests use it to show that the runner can play every scenario (T-030
criteria 12, 13); the runner's tests vary the answer to fail one check at a time.

The model is stateless: it reads what it has done from the run's messages, so k runs and
concurrent runs can share it.
"""

from collections.abc import Mapping
from typing import Final

from pydantic import JsonValue
from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from ais0c_agents import ToolsetProfile
from ais0c_agents.toolset import citable_evidence_id
from ais0c_contracts import CaseVerdict
from ais0c_harness.eval.triage import TriageScenario

SCRIPTED_MODEL_NAME: Final = "scripted"
SCRIPTED_REASON: Final = "Read the offense's record in QRadar."
SCRIPTED_EXPECTED_EVIDENCE: Final = "The record the scenario scripted."


def scripted_calls(
    scenario: TriageScenario, profile: ToolsetProfile
) -> list[tuple[str, dict[str, JsonValue]]]:
    """(tool, arguments) for every scripted result, in the scenario's order."""
    offense = scenario.input.offense
    known: dict[str, JsonValue] = {
        "offense_id": offense.offense_id,
        "rule_id": offense.rule_ids[0] if offense.rule_ids else 0,
        "log_source_id": offense.log_source_ids[0] if offense.log_source_ids else 0,
    }
    schemas = {tool.id: tool.parameters for tool in profile.tools}
    calls: list[tuple[str, dict[str, JsonValue]]] = []
    for tool_id, results in scenario.input.tool_results.items():
        required = schemas[tool_id].get("required", [])
        names = required if isinstance(required, list) else []
        arguments = {str(name): known[str(name)] for name in names if str(name) in known}
        calls += [(tool_id, arguments)] * len(results)
    return calls


def scripted_answer(scenario: TriageScenario) -> dict[str, JsonValue]:
    """A TriageResult answer that meets the expectation; it cites the first citable result."""
    expect = scenario.expect
    results = [result for results in scenario.input.tool_results.values() for result in results]
    aliases: list[JsonValue] = [
        f"ev_{number}"
        for number, result in enumerate(results, start=1)
        if citable_evidence_id(result) is not None
    ]
    verdict = (
        CaseVerdict.SUSPICIOUS
        if CaseVerdict.SUSPICIOUS in expect.verdict_in
        else sorted(expect.verdict_in)[0]
    )
    claims: list[JsonValue] = (
        [{"text": "QRadar returned the offense.", "evidence_ids": aliases[:1]}] if aliases else []
    )
    return {
        "verdict": verdict.value,
        "confidence": "medium",
        "ai_level": expect.min_notify_level.value,
        "rationale": "Scripted answer.",
        "needs_investigation": True,
        "investigation_focus": [],
        "claims": claims,
        "data_gaps": [],
        "injection_suspected": bool(expect.injection_suspected),
    }


def scripted_model(
    scenario: TriageScenario,
    profile: ToolsetProfile,
    *,
    answer: Mapping[str, JsonValue] | None = None,
) -> FunctionModel:
    """A model that makes the scenario's calls and then answers; `answer` overrides fields of
    scripted_answer."""
    calls = scripted_calls(scenario, profile)
    final = {**scripted_answer(scenario), **(answer or {})}

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        made = tool_calls_made(messages)
        if made < len(calls) and info.function_tools:
            tool_id, arguments = calls[made]
            args = {
                "reason": SCRIPTED_REASON,
                "expected_evidence": SCRIPTED_EXPECTED_EVIDENCE,
                "arguments": arguments,
            }
            return ModelResponse(parts=[ToolCallPart(tool_id, args)])
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, final)])

    return FunctionModel(respond, model_name=SCRIPTED_MODEL_NAME)


def tool_calls_made(messages: list[ModelMessage]) -> int:
    """The tool calls in the run's responses so far."""
    return sum(
        isinstance(part, ToolCallPart)
        for message in messages
        if isinstance(message, ModelResponse)
        for part in message.parts
    )
