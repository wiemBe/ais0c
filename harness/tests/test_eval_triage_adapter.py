"""The Triage adapter (T-030 criterion 3): the agent is built from the worker's files, the
gateway's profile and the registry entry's model settings; the task follows the worker's rule;
every run gets a fresh nonce, its own run ID and the manifest's wall clock budget."""

import asyncio
import dataclasses
import re
from datetime import UTC, datetime, timedelta

from pydantic_ai.messages import ModelMessage, ModelResponse, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from ais0c_activities.model_release import load_model_releases
from ais0c_activities.triage import evaluation_window
from ais0c_agents import (
    LITELLM_API_KEY_ENV,
    LITELLM_BASE_URL_ENV,
    load_agent_prompt,
    load_manifest,
    load_model_registry,
)
from ais0c_contracts import Budget, RunStatus
from ais0c_harness.eval import TriageScenario, litellm_model, scripted_model
from ais0c_harness.eval.config import DEFAULT_LITELLM_BASE_URL
from ais0c_mcp_gateway.registry import load_registry

from .eval_helpers import (
    REGISTRY,
    REPO_ROOT,
    TRIAGE_PROFILE,
    answer_of,
    first_request,
    gateway_profile_of,
    profile_of,
    scenario,
    triage_adapter,
    triage_config,
)

ENV = {LITELLM_API_KEY_ENV: "test-key-not-a-secret", LITELLM_BASE_URL_ENV: "http://litellm:4000"}
NONCE = re.compile(r"<untrusted_([0-9a-f]+) ")


def test_the_profile_is_the_one_the_gateway_serves() -> None:
    served = load_registry(REPO_ROOT / "config", ["qradar"]).profiles[TRIAGE_PROFILE].tool_list()
    config = triage_config()

    assert profile_of(config).model_dump(mode="json") == served
    assert gateway_profile_of(config).name == config.manifest.toolset_profile == TRIAGE_PROFILE
    # Descriptions and schemas come from the registry, not from stand-ins.
    get_offense = next(tool for tool in profile_of(config).tools if tool.id == "get_offense")
    assert get_offense.parameters["required"] == ["offense_id"]
    assert not get_offense.description.startswith("Read get_offense")


def test_manifest_prompt_and_release_come_from_the_worker_files() -> None:
    registry = load_model_registry(REGISTRY)
    manifest = load_manifest(REPO_ROOT / "config/agents/triage.yaml", registry)
    config = triage_config()

    assert config.manifest == manifest
    assert config.prompt == load_agent_prompt(REPO_ROOT, manifest)
    assert config.registry_entry == registry[manifest.model_alias]
    assert config.model_release == load_model_releases(REGISTRY)[manifest.model_alias]


def test_the_model_is_the_alias_through_litellm_with_the_entry_settings() -> None:
    config = triage_config()

    model = litellm_model(config, ENV)

    assert model.model_name == config.manifest.model_alias == "soc-fast"
    assert model.settings == config.registry_entry.model_settings()
    assert model.profile.get("supports_forced_tool_choice") is (
        config.registry_entry.forced_tool_choice
    )
    assert model.base_url == "http://litellm:4000/v1/"


def test_an_entry_without_forced_tool_choice_sends_auto() -> None:
    registry = load_model_registry(REGISTRY)
    verifier = registry["soc-verifier"].model_copy(update={"forced_tool_choice": False})
    config = dataclasses.replace(triage_config(), registry_entry=verifier)

    model = litellm_model(config, ENV)

    assert model.profile.get("supports_forced_tool_choice") is False


def test_litellm_base_url_defaults_to_the_local_proxy() -> None:
    model = litellm_model(triage_config(), {LITELLM_API_KEY_ENV: "test-key-not-a-secret"})

    assert model.base_url == f"{DEFAULT_LITELLM_BASE_URL}/v1/"


def test_the_task_follows_the_worker_rule() -> None:
    adapter = triage_adapter()
    played = scenario("tl-02-runbook-instruction")
    agent = adapter.build(
        gateway=_unused_gateway(), model=scripted_model(played, profile_of(adapter.config))
    )

    task = adapter.task(played, agent, run_id="harness-tl-02-runbook-instruction-3")

    budgets = adapter.config.manifest.budgets
    offense = played.input.offense
    assert task.task.task_id == "harness-tl-02-runbook-instruction-3"
    assert task.task.case_id == f"case-{offense.offense_id}"
    assert task.task.time_window == evaluation_window(offense, played.evaluated_at)
    assert task.task.budget == Budget(
        tokens=budgets.tokens, tool_calls=budgets.tool_calls, seconds=budgets.wall_clock_seconds
    )
    assert task.task.objective == f"Triage QRadar offense {offense.offense_id} (evaluation 1)."
    assert (task.offense, task.enrichment, task.knowledge) == (
        offense,
        played.input.enrichment,
        played.input.knowledge,
    )


def test_a_later_evaluation_moves_the_window() -> None:
    adapter = triage_adapter()
    played = scenario("tl-01-catalog-note-fp")
    later = played.model_copy(
        update={
            "input": played.input.model_copy(
                update={"evaluated_at": datetime(2026, 10, 5, 9, 0, tzinfo=UTC)}
            )
        }
    )
    agent = adapter.build(
        gateway=_unused_gateway(), model=scripted_model(played, profile_of(adapter.config))
    )

    window = adapter.task(later, agent, run_id="harness-x-1").task.time_window

    assert window.end == datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
    assert window.start == played.input.offense.start_time


def test_every_run_gets_a_fresh_nonce_and_its_own_run_id() -> None:
    adapter = triage_adapter()
    played = scenario("tl-01-catalog-note-fp")
    nonces: list[str] = []
    inner = scripted_model(played, profile_of(adapter.config))

    def respond(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if first_request(messages):
            match = NONCE.search(info.instructions or "")
            assert match is not None
            nonces.append(match[1])
        return answer_of(inner, messages, info)

    model = FunctionModel(respond, model_name="scripted")
    attempts = [
        asyncio.run(
            adapter.attempt(played, run_id=f"harness-{played.id}-{n}", model=model, time_limit=30)
        )
        for n in (1, 2)
    ]

    assert len(set(nonces)) == 2
    for number, attempt in enumerate(attempts, start=1):
        assert attempt.status is RunStatus.COMPLETED
        assert {exchange.intent.run_id for exchange in attempt.exchanges} == {
            f"harness-{played.id}-{number}"
        }


def test_a_run_past_its_time_limit_is_a_timeout_and_keeps_its_messages() -> None:
    adapter = triage_adapter()
    played = scenario("tl-01-catalog-note-fp")

    async def slow(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not first_request(messages):
            await asyncio.sleep(5)
        args = {
            "reason": "Read it.",
            "expected_evidence": "Its record.",
            "arguments": {"offense_id": 9101},
        }
        return ModelResponse(parts=[ToolCallPart("get_offense", args)])

    attempt = asyncio.run(
        adapter.attempt(played, run_id="harness-x-1", model=FunctionModel(slow), time_limit=0.5)
    )

    assert attempt.status is None
    assert attempt.result is None
    assert attempt.infra_error == "timeout"
    assert attempt.error is not None
    assert "timeout" in attempt.error
    assert len(attempt.exchanges) == 1
    assert any(isinstance(message, ModelResponse) for message in attempt.messages)
    assert attempt.seconds < 5


def test_the_time_limit_is_the_manifest_wall_clock_budget() -> None:
    adapter = triage_adapter()

    assert adapter.wall_clock_seconds == adapter.config.manifest.budgets.wall_clock_seconds == 420


def _unused_gateway():  # noqa: ANN202 - a gateway the agent never calls in these tests
    from ais0c_harness.eval import FixtureGateway

    played: TriageScenario = scenario("tl-01-catalog-note-fp")
    return FixtureGateway(
        gateway_profile_of(triage_config()), {}, now=played.evaluated_at + timedelta(0)
    )
