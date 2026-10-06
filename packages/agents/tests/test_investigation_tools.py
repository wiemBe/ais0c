"""T-023 criterion 3: only the investigate profile and the complete Ariel lifecycle."""

from ais0c_agents import load_aql_rules
from ais0c_agents.investigation import render_time_window
from ais0c_contracts import RunStatus

from .helpers import GATEWAY_POLICY, ScriptedModel, alias, answer, call, retry_prompts
from .investigation_helpers import (
    INVESTIGATION_PROFILE,
    INVESTIGATION_RUN_ID,
    TOOL_EVIDENCE,
    VALID_AQL,
    build_investigation,
    instruction_text,
    investigation_gateway,
    investigation_output,
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
    assert "Put LIMIT before the time range" in text
    assert "UTF8(payload)" in text
    assert "Budget: at most 22 tool calls." in text


def test_prompt_gives_the_window_in_milliseconds_and_a_guard_clean_example() -> None:
    script = ScriptedModel(answer(investigation_output("ev_c1", ranks=(1,))))

    run_investigation(build_investigation(script))

    text = instruction_text(script)
    window = investigation_task().task.time_window
    assert render_time_window(window) in text
    assert f"{int(window.start.timestamp()) * 1000} to {int(window.end.timestamp()) * 1000}" in text
    [example] = [
        line.split("Example: ", 1)[1] for line in text.splitlines() if "Example: SELECT" in line
    ]
    rules = load_aql_rules(GATEWAY_POLICY, profile=INVESTIGATION_PROFILE.name)
    assert rules.check(example).allowed
    assert '"' not in example


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
