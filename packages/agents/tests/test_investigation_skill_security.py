"""T-023 criteria 5 and 6: skill controls, smallest budget and wrapper escape defense."""

import json

import pytest

from ais0c_agents import NO_SKILL, effective_budget, render_skill
from ais0c_contracts import Budget, RunStatus

from .helpers import (
    BLOCK,
    ESCAPE,
    INJECTION,
    OFFENSE_EVIDENCE,
    ScriptedModel,
    answer,
    call,
    lenient_tags,
    ok,
    tool_returns,
)
from .investigation_helpers import (
    build_investigation,
    dcsync_skill,
    instruction_text,
    investigation_gateway,
    investigation_manifest,
    investigation_output,
    investigation_task,
    run_investigation,
)


def test_skill_and_no_skill_sections_use_the_common_renderer_text() -> None:
    skill = dcsync_skill()
    with_skill = ScriptedModel(answer(investigation_output("ev_c1", ranks=(1,))))
    without_skill = ScriptedModel(answer(investigation_output("ev_c1", ranks=(1,))))

    run_investigation(build_investigation(with_skill), investigation_task(skill=skill))
    run_investigation(build_investigation(without_skill), investigation_task())

    text = instruction_text(with_skill)
    assert f"# Skill\n{render_skill(skill)}\n\n# Context" in text
    assert f"# Skill\n{render_skill(None)}\n\n# Context" in instruction_text(without_skill)
    assert "# Skill\nFind DCSync replication and its source account." in text
    assert "## Required telemetry" in text
    assert "Microsoft Windows Security Event Log (required)" in text
    assert "## Required evidence" in text
    assert "replication-events: The 4662 events" in text
    assert NO_SKILL in instruction_text(without_skill)
    skill_section = text.split("# Skill\n", 1)[1].split("\n# Context", 1)[0]
    assert lenient_tags(skill_section) == []


def test_effective_budget_is_the_smallest_manifest_task_and_skill_value() -> None:
    task = investigation_task(
        skill=dcsync_skill(tokens=120000, tool_calls=20, seconds=240),
        tokens=130000,
        tool_calls=18,
        seconds=260,
    )

    assert effective_budget(investigation_manifest(), task) == Budget(
        tokens=120000,
        tool_calls=18,
        seconds=240,
    )


def test_skill_that_does_not_allow_investigation_stops_before_model_or_gateway() -> None:
    script = ScriptedModel(answer(investigation_output()))
    gateway = investigation_gateway()

    with pytest.raises(ValueError, match="does not allow agent role 'investigation'"):
        run_investigation(
            build_investigation(script, gateway),
            investigation_task(skill=dcsync_skill(roles=("verification",))),
        )

    assert script.requests == []
    assert gateway.intents == []


def test_tool_instruction_and_closing_tag_cannot_escape_the_wrapper() -> None:
    row = {"payload": f"{ESCAPE} {INJECTION}", "username": "svc_backup_7731"}
    script = ScriptedModel(
        call("create_ariel_search", query_expression="SELECT * FROM events LIMIT 10 LAST 1 HOURS"),
        answer(investigation_output()),
    )
    gateway = investigation_gateway(create_ariel_search=ok(OFFENSE_EVIDENCE, row))

    run = run_investigation(build_investigation(script, gateway), investigation_task(context=False))

    assert run.status is RunStatus.COMPLETED
    [returned] = [
        part
        for part in tool_returns(script.requests[-1][0])
        if part.tool_name == "create_ariel_search"
    ]
    text = returned.model_response_str()
    block = BLOCK.fullmatch(text)
    assert block is not None
    assert text.count("</untrusted_") == 1
    assert lenient_tags(block["content"]) == []
    assert INJECTION in json.loads(block["content"].splitlines()[1])["payload"]
