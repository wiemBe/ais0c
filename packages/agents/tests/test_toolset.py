"""Acceptance criterion 6: the agent sees only its profile's tools; other calls never run."""

import asyncio

import pytest
from pydantic import ValidationError

from ais0c_agents import ToolsetProfile, ToolSpec, build_triage_agent
from ais0c_agents.toolset import build_gateway_toolset, result_source, tool_parameters_schema
from ais0c_contracts import CostClass, RunStatus

from .helpers import (
    INVESTIGATE_PROFILE,
    NONCE,
    PROFILES,
    RUN_ID,
    TRIAGE_PROFILE,
    FakeClock,
    ScriptedModel,
    answer,
    build,
    call,
    gateway,
    retry_prompts,
    run_triage,
    tool_spec,
    triage_manifest,
    triage_output,
    triage_prompt,
    triage_task,
)


def test_agent_sees_exactly_the_tools_of_its_manifest_profile() -> None:
    script = ScriptedModel(answer(triage_output()))

    run_triage(build(script, gateway()))

    [(_, info)] = script.requests
    assert triage_manifest().toolset_profile == TRIAGE_PROFILE.name
    assert [tool.name for tool in info.function_tools] == [
        "get_offense",
        "get_rule",
        "list_log_sources",
        "list_assets",
    ]
    assert [tool.name for tool in info.output_tools] == ["final_result"]


def test_tool_descriptions_and_parameters_come_from_the_profile() -> None:
    script = ScriptedModel(answer(triage_output()))

    run_triage(build(script, gateway()))

    [(_, info)] = script.requests
    for tool, spec in zip(info.function_tools, TRIAGE_PROFILE.tools, strict=True):
        assert tool.description == spec.description
        assert tool.parameters_json_schema == tool_parameters_schema(spec)
        assert tool.parameters_json_schema["properties"]["arguments"] == spec.parameters
        assert tool.parameters_json_schema["required"] == [
            "reason",
            "expected_evidence",
            "arguments",
        ]


def test_definitions_of_the_tool_schema_move_to_the_top_level() -> None:
    spec = ToolSpec(
        id="create_ariel_search",
        description="Start an Ariel search.",
        schema_version="1",
        cost_class=CostClass.HIGH,
        parameters={
            "type": "object",
            "properties": {"window": {"$ref": "#/$defs/Window"}},
            "$defs": {"Window": {"type": "object", "properties": {"days": {"type": "integer"}}}},
        },
    )

    schema = tool_parameters_schema(spec)

    assert schema["$defs"] == spec.parameters["$defs"]
    assert schema["properties"]["arguments"] == {
        "type": "object",
        "properties": {"window": {"$ref": "#/$defs/Window"}},
    }


@pytest.mark.parametrize("tool", ["add_offense_note", "create_ariel_search"])
def test_call_to_a_tool_outside_the_profile_is_not_run_and_the_model_is_told(tool: str) -> None:
    # add_offense_note is in no agent profile; create_ariel_search is in another profile.
    fake = gateway(create_ariel_search=[])
    script = ScriptedModel(call(tool, offense_id=4711), answer(triage_output()))

    run = run_triage(build(script, fake))

    assert run.status is RunStatus.COMPLETED
    assert fake.intents == []
    [retry] = retry_prompts(run.messages)
    assert retry.tool_name == tool
    assert f"Unknown tool name: {tool!r}" in retry.model_response()


def test_model_that_keeps_calling_a_tool_outside_the_profile_fails_the_run() -> None:
    fake = gateway()
    script = ScriptedModel(call("add_offense_note", offense_id=4711))

    run = run_triage(build(script, fake))

    assert run.status is RunStatus.FAILED
    assert run.result is None
    assert run.error is not None
    assert "add_offense_note" in run.error
    assert fake.intents == []


# --- building -----------------------------------------------------------------------------------


def test_gateway_toolset_has_an_id_for_temporal_activities() -> None:
    toolset = build_gateway_toolset(TRIAGE_PROFILE, gateway(), agent_id="triage")

    assert toolset.id == "gateway-qradar-triage-read"


def test_unknown_profile_stops_the_build() -> None:
    manifest = triage_manifest().model_copy(update={"toolset_profile": "qradar-hunt-read"})

    with pytest.raises(ValueError, match="qradar-hunt-read"):
        build(ScriptedModel(), gateway(), manifest)


def test_profile_filed_under_another_name_stops_the_build() -> None:
    with pytest.raises(ValueError, match="qradar-triage-read"):
        build_triage_agent(
            manifest=triage_manifest(),
            prompt=triage_prompt(),
            profiles={TRIAGE_PROFILE.name: INVESTIGATE_PROFILE},
            gateway=gateway(),
            model=ScriptedModel().model,
        )


@pytest.mark.parametrize(
    "update",
    [
        {"input_schema": "InvestigationTask"},
        {"output_schema": "InvestigationResult"},
        {"prompt": "prompts/triage/v1.md"},
        {"shared_rules": "prompts/_shared/rules/v1.md"},
        {"toolset_profile": None},
    ],
)
def test_manifest_that_is_not_this_triage_agent_stops_the_build(update: dict[str, object]) -> None:
    manifest = triage_manifest().model_copy(update=update)

    with pytest.raises(ValueError, match=r"manifest|profile"):
        build(ScriptedModel(), gateway(), manifest)


@pytest.mark.parametrize("nonce", ["", "7f3a9c", "7F3A9C01D2E4", "7f3a9c01 x"])
def test_run_refuses_an_invalid_nonce(nonce: str) -> None:
    agent = build(ScriptedModel(answer(triage_output())), gateway())

    with pytest.raises(ValidationError, match="nonce"):
        asyncio.run(agent.run(triage_task(), run_id=RUN_ID, nonce=nonce, clock=FakeClock()))


@pytest.mark.parametrize("run_id", ["", "r" * 201])
def test_run_refuses_an_invalid_run_id(run_id: str) -> None:
    script = ScriptedModel(call("get_offense", offense_id=4711), answer(triage_output()))
    fake = gateway()
    agent = build(script, fake)

    with pytest.raises(ValidationError, match="run_id"):
        asyncio.run(agent.run(triage_task(), run_id=run_id, nonce=NONCE, clock=FakeClock()))
    assert script.requests == []
    assert fake.intents == []


def test_task_for_another_agent_is_refused() -> None:
    task = triage_task()
    task = task.model_copy(
        update={"task": task.task.model_copy(update={"agent_id": "investigation"})}
    )
    agent = build_triage_agent(
        manifest=triage_manifest(),
        prompt=triage_prompt(),
        profiles=PROFILES,
        gateway=gateway(),
        model=ScriptedModel().model,
    )

    with pytest.raises(ValueError, match="investigation"):
        asyncio.run(agent.run(task, run_id=RUN_ID, nonce=NONCE, clock=FakeClock()))


# --- profile and tool definitions ---------------------------------------------------------------


def test_agent_tools_are_read_tools_only() -> None:
    with pytest.raises(ValidationError, match="risk"):
        ToolSpec.model_validate(
            tool_spec("add_offense_note").model_dump() | {"risk": "write"},
        )


def test_tool_parameters_must_be_an_object_schema() -> None:
    with pytest.raises(ValidationError, match="type object"):
        ToolSpec(
            id="get_offense",
            description="Read one offense.",
            schema_version="1",
            cost_class=CostClass.LOW,
            parameters={"type": "string"},
        )


@pytest.mark.parametrize("tool_id", ["Get-Offense", "get offense", "final result", ""])
def test_tool_names_are_plain_identifiers(tool_id: str) -> None:
    with pytest.raises(ValidationError):
        tool_spec(tool_id)


def test_profile_lists_each_tool_once() -> None:
    with pytest.raises(ValidationError, match="twice"):
        ToolsetProfile(
            name="qradar-triage-read",
            connector="qradar",
            tools=(tool_spec("get_offense"), tool_spec("get_offense")),
        )


@pytest.mark.parametrize(
    ("connector", "tool_id"),
    [("qradar", "t" * 58), ("misp", "get_event"), ("platform", "enrichment"), ("kb", "runbook")],
)
def test_profile_tool_source_must_be_one_the_wrapper_accepts(connector: str, tool_id: str) -> None:
    # T-015 criterion 2: result blocks are named qradar.<tool> or falcon.<tool>, at most 64
    # characters; an unknown connector fails when the profile loads, before any run.
    with pytest.raises(ValidationError, match="not a source the wrapper accepts"):
        ToolsetProfile(name="p", connector=connector, tools=(tool_spec(tool_id),))


def test_profile_of_a_known_connector_loads() -> None:
    profile = ToolsetProfile(name="p", connector="falcon", tools=(tool_spec("t" * 57),))

    assert result_source(profile, "t" * 57) == "falcon." + "t" * 57


def test_profile_needs_a_tool() -> None:
    with pytest.raises(ValidationError):
        ToolsetProfile(name="qradar-triage-read", connector="qradar", tools=())
