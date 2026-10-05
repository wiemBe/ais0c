"""T-044 criterion 2: what the Orchestrator gets and where it sits in the prompt (decision T-45).

- Triage's rationale and claim texts never reach the model, nor do the offense's free-text
  fields (`description`, `rule_names`).
- The candidate skills' manifest information is approved content: part of the prompt, not
  wrapped.
- Triage's investigation focus and data gaps, and the offense's fields, are inside the
  `untrusted_*` wrapper.
"""

import json

import pytest
from pydantic import ValidationError

from ais0c_agents import (
    Budgets,
    CandidateSkill,
    OrchestratorTask,
    PlanAgent,
    TriageDecision,
)
from ais0c_agents.orchestrator import NO_CANDIDATES, OFFENSE_SOURCE, TRIAGE_SOURCE

from .helpers import (
    BLOCK,
    ESCAPE,
    INJECTION,
    NONCE,
    ScriptedModel,
    answer,
    lenient_tags,
    model_inputs,
)
from .orchestrator_helpers import (
    CLAIM_MARKER,
    DCSYNC_EVIDENCE,
    DESCRIPTION_MARKER,
    FOCUS,
    RATIONALE_MARKER,
    RULE_NAME_MARKER,
    build_orchestrator,
    dcsync_candidate,
    orchestrator_task,
    plan_agents,
    plan_output,
    plan_step,
    run_orchestrator,
    triage_result,
)


def instructions(task: OrchestratorTask | None = None) -> str:
    agent = build_orchestrator(ScriptedModel(answer(plan_output())))
    return agent.render_instructions(task or orchestrator_task(), nonce=NONCE)


def blocks(text: str) -> dict[str, str]:
    """The content of each untrusted block, by source."""
    found = {block["source"]: block["content"] for block in BLOCK.finditer(text)}
    assert len(found) == len(list(BLOCK.finditer(text)))
    return found


def outside_blocks(text: str) -> str:
    return BLOCK.sub("", text)


# --- the input model ---------------------------------------------------------------------------


def test_the_task_carries_the_fields_of_t45() -> None:
    assert set(OrchestratorTask.model_fields) == {
        "task",
        "triage",
        "offense",
        "candidates",
        "agents",
        "plan_budget",
    }
    assert set(TriageDecision.model_fields) == {
        "verdict",
        "confidence",
        "ai_level",
        "needs_investigation",
        "investigation_focus",
        "data_gaps",
        "injection_suspected",
    }
    assert set(CandidateSkill.model_fields) == {
        "ref",
        "agent_role",
        "required_evidence",
        "budgets",
    }


def test_triages_decision_leaves_out_the_rationale_and_the_claims() -> None:
    result = triage_result()

    decision = TriageDecision.from_result(result)

    dumped = decision.model_dump_json()
    assert RATIONALE_MARKER not in dumped
    assert CLAIM_MARKER not in dumped
    assert (decision.verdict, decision.confidence, decision.ai_level) == (
        result.verdict,
        result.confidence,
        result.ai_level,
    )
    assert decision.investigation_focus == (FOCUS,)
    assert decision.data_gaps == tuple(result.data_gaps)
    assert decision.needs_investigation
    assert decision.injection_suspected


@pytest.mark.parametrize("field", ["rationale", "claims", "task_id", "usage"])
def test_triages_decision_refuses_the_other_result_fields(field: str) -> None:
    data = TriageDecision.from_result(triage_result()).model_dump()
    data[field] = "x"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        TriageDecision.model_validate(data)


def test_a_candidate_must_be_for_a_plan_agent() -> None:
    triage_only = dcsync_candidate().model_copy(update={"agent_role": "triage"})

    with pytest.raises(ValidationError, match=r"not plan agents: \['triage'\]"):
        orchestrator_task(candidates=(triage_only,))


def test_an_agent_is_listed_once() -> None:
    data = orchestrator_task().model_dump()
    data["agents"] = [
        *plan_agents(),
        PlanAgent(
            agent_id="verification", budgets=Budgets(tokens=1, tool_calls=1, wall_clock_seconds=1)
        ),
    ]

    with pytest.raises(ValidationError, match="listed twice"):
        OrchestratorTask.model_validate(data)


# --- what the model sees -------------------------------------------------------------------------


def test_the_model_never_sees_triages_rationale_or_claim_text() -> None:
    # A retry makes the model see the prompt twice; every request is checked.
    script = ScriptedModel(
        answer({"steps": [], "injection_suspected": True}), answer(plan_output())
    )

    run = run_orchestrator(build_orchestrator(script))

    assert run.result is not None
    assert len(script.requests) == 2
    for messages, info in script.requests:
        for text in model_inputs(messages, info):
            assert RATIONALE_MARKER not in text
            assert CLAIM_MARKER not in text


def test_the_model_never_sees_the_offenses_free_text() -> None:
    script = ScriptedModel(answer(plan_output()))

    run_orchestrator(build_orchestrator(script))

    [(messages, info)] = script.requests
    for text in model_inputs(messages, info):
        assert DESCRIPTION_MARKER not in text
        assert RULE_NAME_MARKER not in text


def test_the_offense_is_untrusted_data_with_its_structured_fields() -> None:
    text = instructions()

    offense = json.loads(blocks(text)[OFFENSE_SOURCE])
    assert "description" not in offense
    assert "rule_names" not in offense
    assert offense["offense_id"] == 4711
    assert offense["rule_ids"] == [100234]
    assert offense["source_ips"] == ["203.0.113.77"]
    # The user name came with a closing tag; it is neutralized inside the block.
    assert offense["usernames"][0].startswith("svc_backup_7731")
    assert "203.0.113.77" not in outside_blocks(text)


def test_triages_focus_and_data_gaps_are_untrusted_data() -> None:
    text = instructions()

    notes = json.loads(blocks(text)[TRIAGE_SOURCE])
    assert list(notes) == ["investigation_focus", "data_gaps"]
    assert notes["investigation_focus"][0].startswith("Successful logons by svc_backup_7731")
    assert notes["data_gaps"][0]["reason"] == "not_parsed"
    outside = outside_blocks(text)
    assert "Successful logons" not in outside
    assert INJECTION not in outside
    assert "Security Event Log" not in outside


def test_the_attackers_text_cannot_leave_its_block() -> None:
    text = instructions()

    found = blocks(text)
    assert set(found) == {TRIAGE_SOURCE, OFFENSE_SOURCE}
    for content in found.values():
        assert lenient_tags(content) == []
    # The escape's closing tag only closes the real blocks: one per block.
    assert text.count(f"</untrusted_{NONCE}>") == 2
    assert ESCAPE not in text
    assert all(block["evidence_id"] == "ev_none" for block in BLOCK.finditer(text))


def test_triages_verdict_and_flags_are_platform_values_in_the_prompt() -> None:
    outside = outside_blocks(instructions())

    assert (
        "- verdict: suspicious\n- confidence: medium\n- level: high\n"
        "- needs investigation: yes\n- injection suspected: yes"
    ) in outside


def test_the_candidates_manifest_information_is_part_of_the_prompt() -> None:
    text = instructions()

    outside = outside_blocks(text)
    assert (
        "- skill_id windows-dcsync, skill_version 1.0.0, for investigation; budget tokens "
        "120000, tool_calls 24, seconds 300. Required evidence:\n"
        f"  - replication-events: {DCSYNC_EVIDENCE[0].description}\n"
        f"  - request-source: {DCSYNC_EVIDENCE[1].description}"
    ) in outside
    # Not data: no block holds it.
    assert all("windows-dcsync" not in content for content in blocks(text).values())
    # The content hash is the workflow's to check; the model does not need it.
    assert "sha256:" not in text


def test_without_candidates_the_prompt_says_so() -> None:
    text = instructions(orchestrator_task(candidates=()))

    assert NO_CANDIDATES in outside_blocks(text)
    assert "windows-dcsync" not in text


def test_the_agents_budgets_the_plan_budget_and_the_window_are_in_the_prompt() -> None:
    outside = outside_blocks(instructions())

    assert "The evaluation window is 2026-10-02T13:00:00Z to 2026-10-02T14:00:00Z." in outside
    assert (
        "- investigation: tokens 150000, tool_calls 24, seconds 300\n"
        "- verification: tokens 80000, tool_calls 12, seconds 180"
    ) in outside
    assert "for all the steps together: tokens 250000, tool_calls 40, seconds 480." in outside


def test_the_user_prompt_is_the_tasks_objective() -> None:
    script = ScriptedModel(answer(plan_output(plan_step("verification"))))

    run_orchestrator(build_orchestrator(script))

    [(messages, info)] = script.requests
    assert model_inputs(messages, info)[1:] == ["Plan the rest of case case-4711."]
