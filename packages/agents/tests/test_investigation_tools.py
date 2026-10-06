"""T-023 criterion 3: only the investigate profile and the complete Ariel lifecycle."""

import yaml

from ais0c_agents import ToolsetProfile, ToolSpec, build_investigation_agent
from ais0c_contracts import RunStatus

from .helpers import GATEWAY_POLICY, REPO_ROOT, ScriptedModel, alias, answer, call, retry_prompts
from .investigation_helpers import (
    INVESTIGATION_PROFILE,
    INVESTIGATION_RUN_ID,
    TOOL_EVIDENCE,
    VALID_AQL,
    build_investigation,
    instruction_text,
    investigation_gateway,
    investigation_manifest,
    investigation_output,
    investigation_prompt,
    investigation_task,
    run_investigation,
)


def test_one_ariel_search_is_four_calls_with_the_runs_id() -> None:
    script = ScriptedModel(
        call("create_ariel_search", query_expression=VALID_AQL),
        call("get_ariel_search_status", search_id="search-1", wait_seconds=20),
        call("get_ariel_search_results", search_id="search-1", start=0, limit=100),
        call("delete_ariel_search", search_id="search-1"),
        answer(investigation_output(alias(3), ranks=(1,))),
    )
    gateway = investigation_gateway()

    run = run_investigation(build_investigation(script, gateway))

    assert run.status is RunStatus.COMPLETED
    assert [intent.tool_id for intent in gateway.intents] == [
        "create_ariel_search",
        "get_ariel_search_status",
        "get_ariel_search_results",
        "delete_ariel_search",
    ]
    assert {intent.run_id for intent in gateway.intents} == {INVESTIGATION_RUN_ID}
    assert run.result is not None
    assert run.result.claims[0].evidence_ids == [TOOL_EVIDENCE]


def test_model_sees_exactly_the_profile_and_no_write_tool() -> None:
    script = ScriptedModel(answer(investigation_output("ev_c1", ranks=(1,))))

    run_investigation(build_investigation(script))

    info = script.requests[0][1]
    seen = [tool.name for tool in info.function_tools]
    assert seen == [tool.id for tool in INVESTIGATION_PROFILE.tools]
    assert all(tool.risk == "read" for tool in INVESTIGATION_PROFILE.tools)
    assert not any("write" in name or "note" in name or "close" in name for name in seen)
    text = instruction_text(script)
    assert (
        "create_ariel_search, get_ariel_search_status, get_ariel_search_results, delete_ariel_search"
        in text
    )
    assert "get_ariel_search_status with wait_seconds" in text
    assert "Keep only one search active" in text
    assert "the START/STOP part goes after LIMIT" in text
    assert "UTF8(payload)" in text
    assert "Budget: at most 22 tool calls." in text


def test_a_tool_outside_the_profile_cannot_be_called() -> None:
    script = ScriptedModel(
        call("add_offense_note", offense_id=4711, note_text="closed"),
        answer(investigation_output()),
    )
    gateway = investigation_gateway()

    run = run_investigation(build_investigation(script, gateway), investigation_task(context=False))

    assert run.status is RunStatus.COMPLETED
    assert gateway.intents == []
    [retry] = retry_prompts(run.messages)
    assert "Unknown tool name: 'add_offense_note'" in retry.model_response()


# T-048 criterion 3 (decision T-52): the catalog listings left qradar-investigate-read.
CATALOG_LISTINGS = {"list_rules", "list_log_sources", "list_log_source_types", "list_offense_types"}


def connector_profile(name: str) -> ToolsetProfile:
    """A profile of config/connectors/qradar.yaml as the gateway hands it to an agent."""
    connector = yaml.safe_load(
        (REPO_ROOT / "config/connectors/qradar.yaml").read_text(encoding="utf-8")
    )
    registry = connector["tools"]
    return ToolsetProfile(
        name=name,
        connector="qradar",
        tools=tuple(
            ToolSpec(
                id=tool["id"],
                description=registry[tool["id"]]["description"],
                schema_version="1",
                cost_class=registry[tool["id"]]["cost_class"],
                parameters=registry[tool["id"]]["input_schema"],
            )
            for tool in connector["profiles"][name]["tools"]
        ),
    )


def test_the_model_is_not_offered_the_catalog_listings() -> None:
    profile = connector_profile("qradar-investigate-read")
    script = ScriptedModel(answer(investigation_output("ev_c1", ranks=(1,))))
    agent = build_investigation_agent(
        manifest=investigation_manifest(),
        prompt=investigation_prompt(),
        profiles={profile.name: profile},
        gateway=investigation_gateway(),
        model=script.model,
        aql_rules_path=GATEWAY_POLICY,
    )

    run = run_investigation(agent)

    assert run.status is RunStatus.COMPLETED
    offered = {tool.name for tool in script.requests[0][1].function_tools}
    assert offered == {tool.id for tool in profile.tools}
    assert not offered & CATALOG_LISTINGS
    assert {"get_rule", "get_log_source", "create_ariel_search"} <= offered
    # The triage profile keeps them.
    assert CATALOG_LISTINGS <= {tool.id for tool in connector_profile("qradar-triage-read").tools}
