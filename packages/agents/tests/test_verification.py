"""T-024 criteria 1 to 4: the manifest, the input's trust layers, the code's pre-check and
re-reading the evidence at the source.

The ScriptedModel records every request, so the tests read exactly what a model would see, and
the fake gateway records every ToolIntent the run makes. Criterion 1 is the manifest file, 2 is
what the prompt holds, 3 is the deterministic pre-check, 4 is the Ariel lifecycle.
"""

import inspect
import json
from datetime import timedelta

import pytest
import yaml

from ais0c_agents import (
    ReviewedClaim,
    VerificationTask,
    load_aql_rules,
    load_manifest,
    load_model_registry,
    render_claims,
    render_reviewed,
    verification,
)
from ais0c_agents.verification import (
    CLAIM_SOURCE,
    MAX_CLAIMS,
    MAX_EVIDENCE,
    NO_CLAIMS,
    NO_EVIDENCE,
    OFFENSE_SOURCE,
    PLACEHOLDERS,
)
from ais0c_contracts import CaseVerdict, Level, RunStatus, VerificationResult
from ais0c_policy import neutralize_tags

from .helpers import (
    BLOCK,
    CONFIRMED_CLAIM,
    CONTEXT_EVIDENCE,
    DOUBTED_CLAIM,
    GATEWAY_POLICY,
    MISSING_EVIDENCE,
    MODEL_REGISTRY,
    NONCE,
    SHARED_RULES,
    TRIAGE_PROMPT,
    VERIFICATION_MANIFEST,
    VERIFICATION_PROMPT,
    VERIFICATION_RUN_ID,
    VERIFICATION_WINDOW,
    VERIFY_PROFILE,
    ScriptedModel,
    answer,
    build_verification,
    call,
    context_evidence,
    denied,
    model_inputs,
    offense,
    retry_prompts,
    reviewed_claim,
    run_verification,
    tool_returns,
    verification_gateway,
    verification_instructions,
    verification_manifest,
    verification_output,
    verification_prompt,
    verification_task,
)

# The query the model reads the logon with, under the profile's 2-hour window and row limit.
LOGON_QUERY = (
    "SELECT username, sourceip FROM events WHERE username = 'svc_backup_7731' LIMIT 50 LAST 2 HOURS"
)


# --- criterion 1: the manifest ------------------------------------------------------------------


def test_the_manifest_says_what_the_task_file_names() -> None:
    manifest = yaml.safe_load(VERIFICATION_MANIFEST.read_text(encoding="utf-8"))

    assert manifest["id"] == "verification"
    assert manifest["version"] == "1.0.0"
    assert manifest["workflow_types"] == ["case"]
    assert manifest["model_alias"] == "soc-verifier"
    assert manifest["input_schema"] == "VerificationTask"
    assert manifest["output_schema"] == "VerificationResult"
    assert manifest["toolset_profile"] == "qradar-verify-read"
    assert manifest["max_steps"] == 16
    # T-051: 120 000, raised from 80 000 after the lab e2e's run of 82 377 tokens lost its result.
    assert manifest["budgets"] == {"tokens": 120000, "tool_calls": 12, "wall_clock_seconds": 180}


def test_the_manifest_loads_against_the_model_registry() -> None:
    loaded = load_manifest(VERIFICATION_MANIFEST, load_model_registry(MODEL_REGISTRY))

    assert (loaded.prompt, loaded.shared_rules) == (VERIFICATION_PROMPT, SHARED_RULES)
    assert verification_prompt().shared_rules_path == SHARED_RULES
    # A different model family from the agents whose decision it checks (D-21, D-39).
    assert loaded.model_alias == "soc-verifier"
    assert loaded.required_model_capabilities == {"tool_calling", "structured_output"}
    assert (loaded.autonomy, loaded.can_delegate) == ("L0", False)
    assert loaded.toolset_profile == VERIFY_PROFILE.name


def test_the_manifest_and_the_prompt_are_checked_together_at_build_time() -> None:
    # The manifest must name this agent's own prompt; a different one stops the build.
    wrong = verification_manifest().model_copy(update={"prompt": TRIAGE_PROMPT})

    with pytest.raises(ValueError, match=r"manifest 'verification' uses prompts/triage/v3\.md"):
        build_verification(ScriptedModel(), verification_gateway(), manifest=wrong)


# --- criterion 2: the input ----------------------------------------------------------------------


def test_the_input_carries_the_reviewed_decision_claims_evidence_and_offense() -> None:
    task = verification_task()

    assert (task.reviewed.verdict, task.reviewed.confidence, task.reviewed.ai_level) == (
        CaseVerdict.FP,
        "medium",
        Level.LOW,
    )
    assert [item.claim.text for item in task.claims] == [CONFIRMED_CLAIM, DOUBTED_CLAIM]
    assert [item.critical for item in task.claims] == [True, True]
    assert [ref.evidence_id for ref in task.evidence] == list(CONTEXT_EVIDENCE)
    assert task.offense == offense()
    assert task.task.agent_id == "verification"


def test_the_input_has_no_free_text_of_the_earlier_agents() -> None:
    assert set(VerificationTask.model_fields) == {
        "task",
        "reviewed",
        "claims",
        "evidence",
        "offense",
    }
    assert set(ReviewedClaim.model_fields) == {"claim", "critical"}
    for name in ("rationale", "summary_tr", "hypotheses", "investigation_focus"):
        assert name not in VerificationTask.model_fields, name


def test_the_input_is_bounded() -> None:
    claims = VerificationTask.model_fields["claims"]
    evidence = VerificationTask.model_fields["evidence"]

    assert MAX_CLAIMS == 20
    assert MAX_EVIDENCE == 40
    assert claims.metadata[0].max_length == MAX_CLAIMS
    assert evidence.metadata[0].max_length == MAX_EVIDENCE


def test_the_prompt_takes_exactly_the_inputs_the_agent_fills() -> None:
    assert verification_prompt().placeholders - {"shared_rules"} == PLACEHOLDERS
    assert PLACEHOLDERS == {
        "objective",
        "reviewed",
        "claims",
        "offense",
        "evidence",
        "time_window",
        "tools",
        "tool_budget",
    }


def test_the_reviewed_decision_reaches_the_model_as_three_enum_values() -> None:
    assert render_reviewed(verification_task().reviewed) == (
        "verdict: fp\nconfidence: medium\nai_level: low"
    )
    assert "verdict: fp\nconfidence: medium\nai_level: low" in verification_instructions()


def test_each_claim_is_its_own_untrusted_block_with_its_evidence_aliases() -> None:
    text = verification_instructions()

    blocks = [item for item in BLOCK.finditer(text) if item["source"] == CLAIM_SOURCE]
    assert [item["evidence_id"] for item in blocks] == ["ev_none", "ev_none"]
    assert [json.loads(item["content"]) for item in blocks] == [
        {"claim": CONFIRMED_CLAIM, "critical": True, "evidence": ["ev_c1"]},
        {"claim": DOUBTED_CLAIM, "critical": True, "evidence": ["ev_c2"]},
    ]


def test_claim_texts_are_earlier_agent_text_not_case_knowledge() -> None:
    text = verification_instructions()

    sources = [item["source"] for item in BLOCK.finditer(text)]
    assert CLAIM_SOURCE == "agent.claim"
    assert sources.count("agent.claim") == 2
    # Claim texts are neither "past cases" knowledge (T-20) nor QRadar data (T-48).
    assert 'source="kb.' not in text
    assert "qradar.claim" not in text
    assert "kb.case" not in inspect.getsource(verification)


def test_render_claims_puts_the_claim_in_the_block_and_nothing_else() -> None:
    rendered = render_claims(
        [reviewed_claim("A claim.", "ev_a", critical=False)], {"ev_a": "ev_c1"}, nonce=NONCE
    )

    block = BLOCK.search(rendered)
    assert block is not None
    assert block["source"] == "agent.claim"
    assert json.loads(block["content"]) == {
        "claim": "A claim.",
        "critical": False,
        "evidence": ["ev_c1"],
    }
    assert render_claims([], {}, nonce=NONCE) == NO_CLAIMS


def test_the_offense_reaches_the_model_without_its_free_text() -> None:
    text = verification_instructions()

    [block] = [item for item in BLOCK.finditer(text) if item["source"] == OFFENSE_SOURCE]
    row = json.loads(block["content"])
    assert block["evidence_id"] == "ev_none"
    # description and rule_names are free text an attacker can write; they stay out (T-45).
    assert "description" not in row
    assert "rule_names" not in row
    for field in ("offense_id", "rule_ids", "source_ips", "usernames", "event_count"):
        # The wrapper neutralizes tag-like text, so a value with an injected tag is neutralized.
        expected = json.loads(neutralize_tags(json.dumps(offense().model_dump()[field])))
        assert row[field] == expected, field
    assert "Multiple Login Failures" not in text
    assert "Excessive logon failures" not in text


def test_the_evidence_of_the_claims_reaches_the_model_as_aliases() -> None:
    text = verification_instructions()

    blocks = [item for item in BLOCK.finditer(text) if item["source"].endswith(".evidence")]
    assert [item["source"] for item in blocks] == ["qradar.evidence", "falcon.evidence"]
    assert [item["evidence_id"] for item in blocks] == ["ev_c1", "ev_c2"]
    # The gateway's evidence IDs are never in the prompt (T-38).
    assert not any(ref.evidence_id in text for ref in context_evidence())


def test_a_case_without_evidence_says_so_in_the_prompt() -> None:
    assert NO_EVIDENCE in verification_instructions(verification_task(evidence=[]))


def test_the_model_sees_the_agent_input_and_no_free_text_of_the_earlier_agents() -> None:
    script = ScriptedModel(answer(verification_output()))

    run_verification(build_verification(script, verification_gateway()))

    everything = "\n".join(model_inputs(*script.requests[-1]))
    assert CONFIRMED_CLAIM in everything
    assert DOUBTED_CLAIM in everything
    for absent in ("rationale", "summary_tr", "This is a false positive because"):
        assert absent not in everything, absent


def test_a_task_that_carries_a_rationale_is_refused() -> None:
    task = verification_task()

    with pytest.raises(ValueError, match="rationale"):
        VerificationTask.model_validate(
            task.model_dump() | {"rationale": "The logon failures look like a scanner."}
        )


# --- criterion 3: the deterministic pre-check ----------------------------------------------------


def test_a_claim_whose_evidence_is_not_in_the_task_never_reaches_the_model() -> None:
    task = verification_task(
        claims=[
            reviewed_claim(CONFIRMED_CLAIM, CONTEXT_EVIDENCE[0]),
            reviewed_claim("A claim about evidence nobody kept.", MISSING_EVIDENCE),
        ]
    )
    script = ScriptedModel(answer(verification_output()))

    run = run_verification(build_verification(script, verification_gateway()), task)

    assert run.status is RunStatus.COMPLETED
    assert run.result is not None
    # The code adds it to the result and agrees becomes false, although the model said true.
    assert run.result.agrees is False
    assert len(run.result.disagreements) == 1
    assert run.result.disagreements[0].claim_text == "A claim about evidence nobody kept."
    assert run.result.disagreements[0].reason.startswith("evidence not found in this case")
    assert MISSING_EVIDENCE in run.result.disagreements[0].reason
    # And the model never saw the claim or the evidence ID.
    seen = "\n".join(model_inputs(*script.requests[-1]))
    assert "nobody kept" not in seen
    assert MISSING_EVIDENCE not in seen


def test_when_no_claim_survives_the_pre_check_the_model_is_never_called() -> None:
    task = verification_task(
        claims=[reviewed_claim("A claim about evidence nobody kept.", MISSING_EVIDENCE)]
    )
    script = ScriptedModel(answer(verification_output()))

    run = run_verification(build_verification(script, verification_gateway()), task)

    assert script.requests == []  # no model request at all
    assert run.status is RunStatus.COMPLETED
    result = run.result
    assert isinstance(result, VerificationResult)
    # The code answers: agrees=false, with the reason why.
    assert result.agrees is False
    assert len(result.disagreements) == 1
    assert result.disagreements[0].claim_text == "A claim about evidence nobody kept."
    # It could read nothing, so it states no verdict of its own and lowers confidence.
    assert result.verdict is CaseVerdict.SUSPICIOUS
    assert result.confidence == "low"
    assert result.claims == []
    assert result.usage == run.usage
    assert (run.usage.tokens, run.usage.tool_calls) == (0, 0)
    assert (run.prompt_version, run.prompt_hash) == (
        "verification/v1",
        verification_prompt().sha256,
    )
    assert (result.task_id, result.status) == (task.task.task_id, RunStatus.COMPLETED)


def test_a_claim_whose_evidence_is_all_in_the_task_stays_with_the_model() -> None:
    script = ScriptedModel(answer(verification_output()))

    run = run_verification(build_verification(script, verification_gateway()))

    assert run.status is RunStatus.COMPLETED
    assert run.result is not None
    assert run.result.disagreements == []
    assert run.result.agrees is True
    seen = "\n".join(model_inputs(*script.requests[-1]))
    assert CONFIRMED_CLAIM in seen
    assert DOUBTED_CLAIM in seen


def test_a_task_without_claims_is_answered_by_code_without_a_model_request() -> None:
    script = ScriptedModel(answer(verification_output()))

    run = run_verification(
        build_verification(script, verification_gateway()), verification_task(claims=[])
    )

    assert script.requests == []
    assert run.status is RunStatus.COMPLETED
    assert run.result is not None
    # There is no claim to disagree about, so the result carries no disagreement either.
    assert run.result.agrees is False
    assert run.result.disagreements == []


# --- criterion 4: re-reading the evidence at the source -------------------------------------------


def test_the_agent_has_only_the_verify_profile_read_tools() -> None:
    script = ScriptedModel(answer(verification_output()))

    run_verification(build_verification(script, verification_gateway()))

    [(_, info)] = script.requests
    assert [tool.name for tool in info.function_tools] == [
        "create_ariel_search",
        "get_ariel_search_status",
        "get_ariel_search_results",
        "delete_ariel_search",
    ]
    assert all(spec.risk == "read" for spec in VERIFY_PROFILE.tools)


@pytest.mark.parametrize("tool", ["add_offense_note", "get_offense", "get_offense_notes"])
def test_a_call_to_a_tool_outside_the_profile_is_not_made(tool: str) -> None:
    fake = verification_gateway()
    script = ScriptedModel(call(tool, offense_id=4711), answer(verification_output()))

    run = run_verification(build_verification(script, fake))

    assert run.status is RunStatus.COMPLETED
    assert fake.intents == []
    [retry] = retry_prompts(run.messages)
    assert retry.tool_name == tool
    assert f"Unknown tool name: {tool!r}" in retry.model_response()


def test_re_reading_the_evidence_calls_the_gateway_with_the_run_id_and_window() -> None:
    script = ScriptedModel(
        call("create_ariel_search", query_expression=LOGON_QUERY),
        call("get_ariel_search_status", search_id="search-1", wait_seconds=5),
        call("get_ariel_search_results", search_id="search-1", limit=200),
        call("delete_ariel_search", search_id="search-1"),
        answer(verification_output("ev_3")),
    )
    fake = verification_gateway()

    run = run_verification(build_verification(script, fake))

    assert run.status is RunStatus.COMPLETED
    assert [intent.tool_id for intent in fake.intents] == [
        "create_ariel_search",
        "get_ariel_search_status",
        "get_ariel_search_results",
        "delete_ariel_search",
    ]
    for intent in fake.intents:
        assert intent.run_id == VERIFICATION_RUN_ID
        assert intent.time_window == VERIFICATION_WINDOW == verification_task().task.time_window
        assert intent.toolset_profile == "qradar-verify-read"
        assert intent.agent_id == "verification"
        assert intent.case_id == "case-4711"
    # The query the model wrote is what the gateway's AQL Guard sees, unaltered.
    assert fake.intents[0].arguments["query_expression"] == LOGON_QUERY
    # The result carries the gateway's evidence IDs behind the aliases the model cited.
    assert run.result is not None
    assert run.result.checked_evidence_ids == [CONTEXT_EVIDENCE[0]]
    assert run.result.claims[0].evidence_ids == ["ev_01JB3K4M5N6P7Q8R9W"]


def test_the_prompt_states_the_profile_limits_and_asks_for_a_data_gap() -> None:
    rules = load_aql_rules(GATEWAY_POLICY, profile="qradar-verify-read")
    text = verification_instructions()

    # qradar-verify-read: at most a 2-hour window and 200 rows (architecture §11.2).
    assert rules.aql.max_window == timedelta(hours=2)
    assert rules.aql.max_limit == 200
    assert "at most 2 hours between START and STOP and return at most 200 rows" in text
    for tool in VERIFY_PROFILE.tools:
        assert tool.id in text
    assert "record a data gap" in text
    assert "delete_ariel_search" in text


def test_a_denied_query_reaches_the_model_wrapped_and_citable_nothing() -> None:
    fake = verification_gateway(create_ariel_search=denied("AQL Guard: window_exceeds_profile"))
    script = ScriptedModel(
        call("create_ariel_search", query_expression=LOGON_QUERY), answer(verification_output())
    )

    run = run_verification(build_verification(script, fake))

    assert run.status is RunStatus.COMPLETED
    assert [intent.tool_id for intent in fake.intents] == ["create_ariel_search"]
    # The denial reaches the model as untrusted data carrying ev_none, which cannot be cited,
    # so the checked evidence is the context evidence only.
    [returned] = list(tool_returns(script.requests[-1][0]))
    block = BLOCK.fullmatch(returned.model_response_str())
    assert block is not None
    assert (block["source"], block["evidence_id"]) == ("qradar.create_ariel_search", "ev_none")
    assert "window_exceeds_profile" in block["content"]
    assert run.result is not None
    assert run.result.checked_evidence_ids == [CONTEXT_EVIDENCE[0]]


def test_a_run_for_another_agent_is_refused() -> None:
    task = verification_task()
    other = task.model_copy(update={"task": task.task.model_copy(update={"agent_id": "triage"})})

    with pytest.raises(ValueError, match=r"task is for agent 'triage', not 'verification'"):
        run_verification(build_verification(ScriptedModel(), verification_gateway()), other)
