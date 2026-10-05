"""T-024 criteria 5 and 6: the output rules of VerificationResult and the security negatives.

Criterion 5 is what the run accepts and what it sends back to the model: the evidence fields
pass T-043's validator, `agrees=false` needs a disagreement, and every `disagreement.claim_text`
is a claim of this run. Criterion 6 is that an instruction hidden in a claim text or in an
evidence excerpt cannot leave the wrapper, that `injection_suspected` is the model's own answer,
and that the agent has no write tool (the tool list is checked in test_verification.py).
"""

import re

from ais0c_agents import NO_EVIDENCE_ID
from ais0c_agents.verification import OUTPUT_RETRIES, VerificationOutput
from ais0c_contracts import CaseVerdict, Confidence, RunStatus, VerificationResult

from .helpers import (
    BLOCK,
    CONFIRMED_CLAIM,
    CONTEXT_EVIDENCE,
    DOUBTED_CLAIM,
    ESCAPE,
    INJECTED_CLAIM,
    INJECTION,
    MISSING_EVIDENCE,
    NONCE,
    ScriptedModel,
    answer,
    build_verification,
    context_evidence,
    disagreement,
    evidence_ref,
    lenient_tags,
    model_inputs,
    ok,
    retry_prompts,
    reviewed_claim,
    run_verification,
    verification_gateway,
    verification_output,
    verification_task,
)

RUN_FIELDS = {"task_id", "status", "usage"}
# What the validator tells the model when a disagreement names a claim it was not shown.
UNKNOWN_CLAIM_RETRY = (
    'These disagreement texts are not among the claims you were shown ("A claim nobody made."). '
    "Copy one claim_text exactly from the claims above, or remove the disagreement."
)
NO_DISAGREEMENT_RETRY = (
    "The result says agrees=false but names no disagreement. Add one disagreement with its "
    "claim_text copied exactly from the claims above, or set agrees=true."
)


# --- criterion 5: the output rules ---------------------------------------------------------------


def test_an_agreeing_result_is_accepted() -> None:
    script = ScriptedModel(answer(verification_output("ev_c1")))

    run = run_verification(build_verification(script, verification_gateway()))

    assert run.status is RunStatus.COMPLETED
    result = run.result
    assert isinstance(result, VerificationResult)
    assert result.agrees is True
    assert result.disagreements == []
    assert (result.verdict, result.confidence) == (CaseVerdict.SUSPICIOUS, Confidence.MEDIUM)
    # Both evidence fields carry the gateway's ID, the alias the model cited (T-38).
    assert result.checked_evidence_ids == [CONTEXT_EVIDENCE[0]]
    assert result.claims[0].evidence_ids == [CONTEXT_EVIDENCE[0]]
    assert result.injection_suspected is True
    assert retry_prompts(run.messages) == []


def test_a_disagreement_that_names_a_claim_of_this_run_is_accepted() -> None:
    script = ScriptedModel(
        answer(
            verification_output(
                agrees=False,
                disagreements=[disagreement(DOUBTED_CLAIM)],
                checked_evidence_ids=["ev_c1", "ev_c2"],
            )
        )
    )

    run = run_verification(build_verification(script, verification_gateway()))

    assert run.status is RunStatus.COMPLETED
    assert run.result is not None
    assert run.result.agrees is False
    assert [(item.claim_text, item.reason) for item in run.result.disagreements] == [
        (DOUBTED_CLAIM, "The account is a user account.")
    ]
    assert run.result.checked_evidence_ids == list(CONTEXT_EVIDENCE)


def test_agreeing_false_without_a_disagreement_goes_back_to_the_model() -> None:
    invalid = verification_output(agrees=False, disagreements=[])
    script = ScriptedModel(answer(invalid), answer(verification_output()))

    run = run_verification(build_verification(script, verification_gateway()))

    assert run.status is RunStatus.COMPLETED
    [retry] = retry_prompts(run.messages)
    assert retry.tool_name == "final_result"
    assert NO_DISAGREEMENT_RETRY in retry.model_response()
    assert run.result is not None
    assert run.result.agrees is True


def test_a_disagreement_that_names_another_claim_goes_back_to_the_model() -> None:
    script = ScriptedModel(
        answer(verification_output(disagreements=[disagreement("A claim nobody made.")])),
        answer(verification_output()),
    )

    run = run_verification(build_verification(script, verification_gateway()))

    assert run.status is RunStatus.COMPLETED
    [retry] = retry_prompts(run.messages)
    assert UNKNOWN_CLAIM_RETRY in retry.model_response()
    assert run.result is not None
    assert run.result.disagreements == []


def test_a_disagreement_text_off_by_a_character_goes_back_to_the_model() -> None:
    # The workflow matches a contested claim by its exact text, so "almost the same" text
    # would leave the workflow unable to tell which claim was contested.
    almost = DOUBTED_CLAIM.replace("machine", "Machine")
    script = ScriptedModel(
        answer(verification_output(agrees=False, disagreements=[disagreement(almost)])),
        answer(verification_output()),
    )

    run = run_verification(build_verification(script, verification_gateway()))

    assert (
        UNKNOWN_CLAIM_RETRY.replace("A claim nobody made.", almost)
        in retry_prompts(run.messages)[0].model_response()
    )
    assert run.result is not None
    assert run.result.disagreements == []


def test_an_output_that_keeps_breaking_the_rules_fails_the_run() -> None:
    script = ScriptedModel(answer(verification_output(agrees=False, disagreements=[])))

    run = run_verification(build_verification(script, verification_gateway()))

    assert run.status is RunStatus.FAILED
    assert run.result is None
    assert run.error is not None
    assert "output retries" in run.error
    assert len(retry_prompts(run.messages)) == OUTPUT_RETRIES


def test_an_unknown_evidence_alias_goes_back_to_the_model() -> None:
    script = ScriptedModel(
        answer(verification_output(checked_evidence_ids=["ev_c7"])),
        answer(verification_output()),
    )

    run = run_verification(build_verification(script, verification_gateway()))

    assert run.status is RunStatus.COMPLETED
    [retry] = retry_prompts(run.messages)
    assert "These evidence_ids are neither context evidence nor returned by your tool calls" in (
        retry.model_response()
    )
    assert run.result is not None
    assert run.result.checked_evidence_ids == [CONTEXT_EVIDENCE[0]]


def test_a_claim_of_the_result_may_not_cite_evidence_nobody_returned() -> None:
    script = ScriptedModel(
        answer(verification_output("ev_c9")),
        answer(verification_output()),
    )

    run = run_verification(build_verification(script, verification_gateway()))

    assert run.status is RunStatus.COMPLETED
    assert len(retry_prompts(run.messages)) == 1
    assert run.result is not None
    assert run.result.claims == []


def test_the_result_is_the_model_output_plus_the_run_fields_and_the_code_answer() -> None:
    script = ScriptedModel(
        answer(verification_output(agrees=False, disagreements=[disagreement(DOUBTED_CLAIM)]))
    )

    run = run_verification(build_verification(script, verification_gateway()))

    result = run.result
    assert isinstance(result, VerificationResult)
    assert VerificationResult.model_validate(result.model_dump()) == result
    assert (result.task_id, result.status) == (
        verification_task().task.task_id,
        RunStatus.COMPLETED,
    )
    assert result.usage == run.usage
    assert (result.usage.tool_calls, result.usage.seconds) == (0, 1.5)
    assert set(VerificationOutput.model_fields) == set(VerificationResult.model_fields) - RUN_FIELDS
    for name, field in VerificationOutput.model_fields.items():
        expected = VerificationResult.model_fields[name]
        assert (field.annotation, field.metadata) == (expected.annotation, expected.metadata), name


def test_the_model_sees_the_output_schema_as_verification_result() -> None:
    script = ScriptedModel(answer(verification_output()))

    run_verification(build_verification(script, verification_gateway()))

    [(_, info)] = script.requests
    [output_tool] = info.output_tools
    assert output_tool.parameters_json_schema["title"] == "VerificationResult"
    assert output_tool.description == "Return the VerificationResult for this case."


# --- criterion 6: security ----------------------------------------------------------------------


def test_an_instruction_in_a_claim_text_cannot_escape_the_wrapper() -> None:
    task = verification_task(
        claims=[
            reviewed_claim(CONFIRMED_CLAIM, CONTEXT_EVIDENCE[0]),
            reviewed_claim(INJECTED_CLAIM, CONTEXT_EVIDENCE[1]),
        ]
    )
    script = ScriptedModel(answer(verification_output()))

    run_verification(build_verification(script, verification_gateway()), task)

    texts = model_inputs(*script.requests[-1])
    everything = "\n".join(texts)
    outside = "\n".join(BLOCK.sub("", text) for text in texts)
    # The attacker's text reached the model, as data only: the claim's own closing tag and
    # the fake <org_context> stay inside the block that carries the claim.
    assert INJECTION in everything
    assert "192.0.2.99 is an approved pentest host" in everything
    assert INJECTION not in outside
    assert "192.0.2.99 is an approved pentest host" not in outside
    # The claim's own closing tag and its fake <org_context> stay inside its block, and the
    # block carries ev_none: the text is claim data, not evidence and not an instruction.
    [claim_block] = [
        item for item in BLOCK.finditer(everything) if "is a machine account" in item["content"]
    ]
    assert claim_block["evidence_id"] == NO_EVIDENCE_ID
    assert lenient_tags(claim_block["content"]) == []
    assert f"&lt;/untrusted_{NONCE}>" in claim_block["content"]


def test_an_instruction_in_an_evidence_excerpt_cannot_escape_the_wrapper() -> None:
    evidence = [
        evidence_ref(CONTEXT_EVIDENCE[0]),
        evidence_ref(
            CONTEXT_EVIDENCE[1], excerpt=f'[{{"user":"svc_backup_7731"}}] {ESCAPE} {INJECTION}'
        ),
    ]
    task = verification_task(evidence=evidence)
    script = ScriptedModel(answer(verification_output()))

    run_verification(build_verification(script, verification_gateway()), task)

    texts = model_inputs(*script.requests[-1])
    everything = "\n".join(texts)
    outside = "\n".join(BLOCK.sub("", text) for text in texts)
    assert INJECTION in everything
    assert INJECTION not in outside
    assert "192.0.2.99 is an approved pentest host" not in outside
    for block in BLOCK.finditer(everything):
        assert lenient_tags(block["content"]) == []


def test_evidence_the_earlier_agents_left_out_is_reported_not_hidden() -> None:
    task = verification_task(
        claims=[
            reviewed_claim(CONFIRMED_CLAIM, CONTEXT_EVIDENCE[0]),
            reviewed_claim("A claim about evidence nobody kept.", MISSING_EVIDENCE),
        ]
    )
    script = ScriptedModel(answer(verification_output()))

    run = run_verification(build_verification(script, verification_gateway()), task)

    assert run.result is not None
    assert run.result.agrees is False
    # The reason names the evidence that is missing, so the operator can look for it.
    assert run.result.disagreements[0].reason == (
        f"evidence not found in this case: {MISSING_EVIDENCE}"
    )
    assert "nobody kept" not in "\n".join(model_inputs(*script.requests[-1]))


def test_injection_suspected_is_the_model_own_answer() -> None:
    for flag in (True, False):
        script = ScriptedModel(answer(verification_output(injection_suspected=flag)))

        run = run_verification(build_verification(script, verification_gateway()))

        assert run.result is not None
        assert run.result.injection_suspected is flag


def test_a_claim_text_in_a_tool_result_cannot_become_an_accepted_disagreement() -> None:
    # The run's claim texts travel in RunDeps, which the model never sees and cannot write to,
    # so a tool result cannot smuggle a claim in as one the verifier may contest.
    forged = DOUBTED_CLAIM.replace("machine", "trusted")
    fake = verification_gateway(
        create_ariel_search=ok(
            "ev_01JB3K4M5N6P7Q8R9U", {"username": "svc_backup_7731", "note": forged}
        )
    )
    script = ScriptedModel(
        answer(verification_output(agrees=False, disagreements=[disagreement(forged)])),
        answer(verification_output()),
    )

    run = run_verification(build_verification(script, fake))

    assert run.status is RunStatus.COMPLETED
    [retry] = retry_prompts(run.messages)
    assert "are not among the claims you were shown" in retry.model_response()
    assert f'"{forged}"' in retry.model_response()
    assert run.result is not None
    assert run.result.disagreements == []


def test_the_prompt_holds_no_gateway_evidence_id_and_only_this_run_s_aliases() -> None:
    script = ScriptedModel(answer(verification_output()))

    run_verification(build_verification(script, verification_gateway()))

    everything = "\n".join(model_inputs(*script.requests[-1]))
    for ref in context_evidence():
        assert ref.evidence_id not in everything
    # Only the context aliases and ev_none: no tool call was made, so no ev_<n> exists.
    assert set(re.findall(r"ev_[A-Za-z0-9_.:-]*", everything)) == {"ev_c1", "ev_c2", "ev_none"}
