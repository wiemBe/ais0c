"""Shared pieces of the eval runner's tests (T-030): the repository's Triage agent, its
scenarios, scripted models that vary one thing per run, and suites written to a temporary
directory."""

import asyncio
from collections.abc import Callable, Mapping, Sequence
from datetime import timedelta
from functools import cache
from pathlib import Path
from typing import Final

import yaml
from pydantic import JsonValue
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolCallPart
from pydantic_ai.models import Model
from pydantic_ai.models.function import AgentInfo, FunctionModel

from ais0c_agents import ToolsetProfile
from ais0c_contracts import CostClass, TimeWindow, ToolIntent
from ais0c_harness.eval import (
    AgentConfig,
    EvalRun,
    RunOptions,
    ScenarioBase,
    Suite,
    TriageAdapter,
    TriageScenario,
    load_agent_config,
    load_scenario,
    load_suite,
    run_eval,
    scripted_model,
)
from ais0c_harness.eval.runner import DEFAULT_MAX_TOTAL_TOKENS
from ais0c_harness.eval.scripted import scripted_answer, scripted_calls, tool_calls_made
from ais0c_mcp_gateway.registry import Profile

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
SUITES: Final = REPO_ROOT / "harness" / "suites"
REGISTRY: Final = REPO_ROOT / "config" / "models" / "registry.dev.yaml"
TRIAGE_PROFILE: Final = "qradar-triage-read"


@cache
def triage_config() -> AgentConfig:
    return load_agent_config(REPO_ROOT, TriageAdapter.manifest_path, REGISTRY)


def profile_of(config: AgentConfig) -> ToolsetProfile:
    assert config.profile is not None
    return config.profile


def gateway_profile_of(config: AgentConfig) -> Profile:
    assert config.gateway_profile is not None
    return config.gateway_profile


def triage_adapter() -> TriageAdapter:
    return TriageAdapter(triage_config())


def scenario(scenario_id: str) -> TriageScenario:
    suite = "trust-layers" if scenario_id.startswith("tl-") else "adversarial-fn"
    loaded = load_scenario(SUITES / suite / f"{scenario_id}.yaml", root=REPO_ROOT).scenario
    assert isinstance(loaded, TriageScenario)
    return loaded


def suite(suite_id: str) -> Suite:
    return load_suite(SUITES / suite_id, root=REPO_ROOT)


def write_suite(
    directory: Path,
    *,
    suite_id: str,
    kind: str,
    prefix: str,
    sources: Sequence[str],
    agent: str = "triage",
) -> Path:
    """A suite under `directory` with copies of the repository's scenarios `sources`, renamed
    into the suite (`<prefix><nn>-copy`)."""
    path = directory / suite_id
    path.mkdir(parents=True)
    definition = {
        "id": suite_id,
        "title": f"Test suite {suite_id}",
        "kind": kind,
        "agent": agent,
        "scenario_prefix": prefix,
    }
    (path / "suite.yaml").write_text(yaml.safe_dump(definition), encoding="utf-8")
    for number, source in enumerate(sources, start=1):
        data = scenario_data(source)
        data["id"] = f"{prefix}{number:02d}-copy"
        data["suite"] = suite_id
        (path / f"{data['id']}.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def scenario_data(scenario_id: str) -> dict[str, object]:
    suite_dir = "trust-layers" if scenario_id.startswith("tl-") else "adversarial-fn"
    text = (SUITES / suite_dir / f"{scenario_id}.yaml").read_text(encoding="utf-8")
    data = yaml.safe_load(text)
    assert isinstance(data, dict)
    return data


def first_request(messages: Sequence[ModelMessage]) -> bool:
    """Whether the model is answering a run's first request."""
    return len(messages) == 1 and isinstance(messages[0], ModelRequest)


def answer_of(model: FunctionModel, messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """What a synchronous FunctionModel answers, for a test model that wraps it."""
    function = model.function
    assert function is not None
    response = function(messages, info)
    assert isinstance(response, ModelResponse)
    return response


def varying_model(
    scenario: TriageScenario,
    answers: Sequence[Mapping[str, JsonValue] | None],
    *,
    failures: Mapping[int, int] | None = None,
) -> FunctionModel:
    """A scripted model whose n-th run (from 0, in the order runs start) answers with
    `answers[n]` over the scripted answer; `failures` maps a run to the HTTP status its first
    request fails with. Runs must not overlap (concurrency 1)."""
    calls = scripted_calls(scenario, profile_of(triage_config()))
    started = -1

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal started
        if first_request(messages):
            started += 1
            status = (failures or {}).get(started)
            if status is not None:
                raise ModelHTTPError(status_code=status, model_name="scripted", body=None)
        made = tool_calls_made(messages)
        if made < len(calls) and info.function_tools:
            tool_id, arguments = calls[made]
            args = {
                "reason": "Read QRadar.",
                "expected_evidence": "Its record.",
                "arguments": arguments,
            }
            return ModelResponse(parts=[ToolCallPart(tool_id, args)])
        override = answers[started % len(answers)] or {}
        answer = {**scripted_answer(scenario), **override}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, answer)])

    return FunctionModel(respond, model_name="scripted")


def scripted(config: AgentConfig, played: ScenarioBase) -> Model:
    """The runner's model factory for scripted runs: each scenario's scripted model."""
    assert isinstance(played, TriageScenario)
    return scripted_model(played, profile_of(config))


def run(
    suites: Sequence[Suite],
    model_factory: Callable[[AgentConfig, ScenarioBase], Model],
    *,
    options: RunOptions,
    scenario_ids: Sequence[str] | None = None,
) -> EvalRun:
    return asyncio.run(
        run_eval(
            root=REPO_ROOT,
            suites=suites,
            scenario_ids=scenario_ids,
            registry_path=REGISTRY,
            model_factory=model_factory,
            options=options,
        )
    )


def intent(
    tool_id: str,
    arguments: Mapping[str, JsonValue],
    *,
    played: TriageScenario,
    reason: str = "Read the offense.",
    expected_evidence: str = "The offense record.",
    schema_version: str | None = None,
) -> ToolIntent:
    """An intent as the Triage agent sends it for `played`, with the tool's schema version."""
    profile = profile_of(triage_config())
    spec = next((tool for tool in profile.tools if tool.id == tool_id), None)
    offense = played.input.offense
    return ToolIntent(
        run_id=f"harness-{played.id}-1",
        case_id=f"case-{offense.offense_id}",
        agent_id="triage",
        toolset_profile=TRIAGE_PROFILE,
        tool_id=tool_id,
        tool_schema_version=schema_version or (spec.schema_version if spec else "0"),
        arguments=dict(arguments),
        reason=reason,
        expected_evidence=expected_evidence,
        time_window=TimeWindow(
            start=offense.start_time, end=offense.last_updated_time + timedelta(minutes=5)
        ),
        cost_class=spec.cost_class if spec else CostClass.LOW,
    )


def fast(
    k: int = 1,
    *,
    concurrency: int = 1,
    max_total_tokens: int = DEFAULT_MAX_TOTAL_TOKENS,
    wall_clock_seconds: float | None = None,
) -> RunOptions:
    """Run options for tests: no pause before a retry, one run at a time by default."""
    return RunOptions(
        k=k,
        concurrency=concurrency,
        max_total_tokens=max_total_tokens,
        retry_delay_seconds=0.0,
        wall_clock_seconds=wall_clock_seconds,
    )
