"""T-025: the Reporting agent (architecture §7, §9).

The agent has no tools. Its input is the workflow's decision, the verified claims with their
evidence, the urgent event candidates and the data gaps; its output is the Turkish summary,
the urgent events and the recommendations.

Each test names the acceptance criterion it shows:

- 1 the manifest,
- 2 the input and its trust layers,
- 3 the decision comes from the input, never from the model,
- 4 the summary's limit and the Turkish report rules in the prompt,
- 5 the urgent events copy their candidate's identifiers, and rank 1..n,
- 6 the recommendations and the data gaps,
- 7 no log text is copied into the report.
"""

import asyncio
import json
import re
from typing import Any, cast

import pytest
from pydantic import ValidationError
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import UserPromptPart
from pydantic_ai.models import Model
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from ais0c_agents import AgentRun, RunDeps, check_agent_config, load_manifest, load_prompt
from ais0c_agents.evidence import check_evidence_fields
from ais0c_agents.reporting import (
    NO_CANDIDATES,
    NO_CLAIMS,
    NO_DATA_GAPS,
    NO_EVIDENCE,
    CandidatesUnchanged,
    CaseDecision,
    NoLogText,
    ReportingAgent,
    ReportingOutput,
    ReportingTask,
    build_reporting_agent,
)
from ais0c_agents.reporting import SPEC as REPORTING_SPEC
from ais0c_contracts import (
    AgentTask,
    Budget,
    CaseReport,
    CaseVerdict,
    Claim,
    Confidence,
    DataGap,
    DataGapReason,
    EvidenceRef,
    Level,
    RunStatus,
    TimeWindow,
    UrgentEvent,
)
from ais0c_contracts.common import SUMMARY_MAX_LENGTH

from .helpers import (
    BLOCK,
    CONTEXT_EVIDENCE,
    CONTEXT_QUERY,
    END,
    ESCAPE,
    NONCE,
    PROFILES,
    REPO_ROOT,
    SHARED_RULES,
    START,
    FakeClock,
    ScriptedModel,
    answer,
    context_evidence,
    enrichment,
    evidence_ref,
    offense,
    registry,
    retry_prompts,
)

REPORTING_MANIFEST_PATH = REPO_ROOT / "config/agents/reporting.yaml"
REPORTING_PROMPT_PATH = "prompts/reporting/v1.md"
REPORTING_RUN_ID = "case-4711-reporting-1"

# The synthetic case of the helpers: 412 logon failures for svc_backup_7731 from 203.0.113.77,
# an IOC address, against a critical asset.
CANDIDATE = UrgentEvent(
    rank=1,
    time=START,
    log_source="412 Windows Security Event Log",
    event_name="Failed Logon",
    qid=4625,
    source="203.0.113.77",
    destination="198.51.100.20",
    username="svc_backup_7731",
    reason="IOC adresinden art arda gelen basarisiz girisler.",
    checklist=["Bu kullanicinin basarili girisleri var mi?"],
    aql=(
        "SELECT sourceip FROM events WHERE sourceip = '203.0.113.77' LIMIT 50 "
        "START '2026-10-02 13:00' STOP '2026-10-02 14:00'"
    ),
    evidence_id=CONTEXT_EVIDENCE[0],
)
SECOND_CANDIDATE = UrgentEvent(
    rank=2,
    time=END,
    log_source="412 Windows Security Event Log",
    event_name="Successful Logon",
    qid=4624,
    source="203.0.113.77",
    destination="198.51.100.20",
    username="svc_backup_7731",
    reason="Basarisiz girislerden hemen sonra basarili giris.",
    checklist=["Bu oturum normal mi?"],
    aql=(
        "SELECT eventid FROM events WHERE eventid = 4624 LIMIT 50 "
        "START '2026-10-02 13:00' STOP '2026-10-02 14:00'"
    ),
    evidence_id=CONTEXT_EVIDENCE[1],
)
VERIFIED_CLAIM = Claim(
    text="svc_backup_7731 hesabi icin 412 basarisiz giris kaydi var.",
    evidence_ids=[CONTEXT_EVIDENCE[0]],
)
GAP = DataGap(
    source="Microsoft Windows Security Event Log",
    period_start=START,
    period_end=END,
    reason=DataGapReason.NOT_PARSED,
)
DECISION = CaseDecision(
    verdict=CaseVerdict.SUSPICIOUS, confidence=Confidence.MEDIUM, notify_level=Level.HIGH
)
SUMMARY_TR = (
    "2026-10-02 16:05'te svc_backup_7731 icin 412 basarisiz giris goruldu. "
    "Karar supheli, kaynak IP bir IOC ile eslesti. "
    "Operator once 203.0.113.77 kaynakli basarili girisleri kontrol etmeli."
)


def reporting_task(
    *,
    decision: CaseDecision = DECISION,
    claims: list[Claim] | None = None,
    evidence: list[EvidenceRef] | None = None,
    candidates: list[UrgentEvent] | None = None,
    data_gaps: list[DataGap] | None = None,
) -> ReportingTask:
    """A synthetic ReportingTask: every field T-45 lists, with the helpers' case."""
    return ReportingTask(
        task=AgentTask(
            task_id="task-4711-reporting-1",
            parent_run_id="case-4711-triage-1",
            case_id="case-4711",
            agent_id="reporting",
            agent_version="1.0.0",
            objective="Case 4711 icin Turkce rapor uret.",
            context_refs=[ref.evidence_id for ref in (evidence or context_evidence())],
            time_window=TimeWindow(start=START, end=END),
            budget=Budget(tokens=60000, tool_calls=0, seconds=120),
        ),
        decision=decision,
        claims=[VERIFIED_CLAIM] if claims is None else claims,
        evidence=context_evidence() if evidence is None else evidence,
        urgent_event_candidates=[CANDIDATE] if candidates is None else candidates,
        data_gaps=[] if data_gaps is None else data_gaps,
        offense=offense(),
        enrichment=enrichment(),
    )


def reported_event(
    candidate: UrgentEvent, *, rank: int = 1, **overrides: object
) -> dict[str, object]:
    """A valid model answer for one candidate: its identifiers, its own rank, reason, checklist."""
    event = candidate.model_dump(mode="json")
    return event | {"rank": rank, "checklist": ["Bu kullanicinin VPN girisi var mi?"]} | overrides


def reporting_output(*events: dict[str, object], **overrides: object) -> dict[str, object]:
    """A valid CaseReport as the model returns it; no event if none is given."""
    output: dict[str, object] = {
        "summary_tr": SUMMARY_TR,
        "urgent_events": list(events),
        "recommendations": [
            {
                "action_type": "investigate_further",
                "target": "203.0.113.77",
                "rationale": "IOC ile eslesen kaynaktan gelen girisleri incele.",
                "evidence_ids": ["ev_c1"],
            }
        ],
        "injection_suspected": True,
    }
    return output | overrides


def run_reporting(
    agent: ReportingAgent,
    task: ReportingTask | None = None,
    *,
    run_id: str = REPORTING_RUN_ID,
    nonce: str = NONCE,
) -> AgentRun[CaseReport]:
    """Run the Reporting agent once, as a workflow activity would."""
    return asyncio.run(
        agent.run(task or reporting_task(), run_id=run_id, nonce=nonce, clock=FakeClock())
    )


def _agent(model: Model) -> ReportingAgent:
    return build_reporting_agent(
        manifest=load_manifest(REPORTING_MANIFEST_PATH, registry()),
        prompt=load_prompt(REPO_ROOT, REPORTING_PROMPT_PATH, shared_rules=SHARED_RULES),
        model=model,
    )


def _ctx() -> RunContext[RunDeps]:
    """The run context a validator sees; Reporting has no tools, so no tool results."""
    return RunContext(
        deps=RunDeps(
            run_id=REPORTING_RUN_ID,
            case_id="case-4711",
            hunt_id=None,
            time_window=TimeWindow(start=START, end=END),
            nonce=NONCE,
        ),
        model=TestModel(),
        usage=RunUsage(),
        messages=[],
    )


# --- criterion 1: the manifest ---------------------------------------------------------------


def test_the_manifest_holds_the_values_the_task_lists() -> None:
    manifest = load_manifest(REPORTING_MANIFEST_PATH, registry())

    assert manifest.id == "reporting"
    assert manifest.version == "1.0.0"
    assert manifest.workflow_types == frozenset({"case"})
    assert manifest.model_alias == "soc-report"
    assert manifest.input_schema == "ReportingTask"
    assert manifest.output_schema == "CaseReport"
    assert manifest.toolset_profile is None
    assert manifest.max_steps == 4
    assert (manifest.budgets.tokens, manifest.budgets.tool_calls) == (60000, 0)
    assert manifest.budgets.wall_clock_seconds == 120


def test_the_manifest_runs_the_reporting_prompt_with_the_shared_rules_v2() -> None:
    manifest = load_manifest(REPORTING_MANIFEST_PATH, registry())

    assert manifest.prompt == REPORTING_PROMPT_PATH
    assert manifest.shared_rules == SHARED_RULES
    assert manifest.autonomy == "L0"
    assert manifest.can_delegate is False
    assert "structured_output" in manifest.required_model_capabilities


def test_the_model_alias_is_a_known_alias() -> None:
    from ais0c_agents.llm import MODEL_ALIASES

    assert "soc-report" in MODEL_ALIASES


# --- criterion 2: the input -------------------------------------------------------------------


def test_the_task_carries_the_fields_t45_lists() -> None:
    task = reporting_task(candidates=[CANDIDATE, SECOND_CANDIDATE], data_gaps=[GAP])

    assert task.task.agent_id == "reporting"
    assert task.decision.verdict is CaseVerdict.SUSPICIOUS
    assert task.decision.confidence is Confidence.MEDIUM
    assert task.decision.notify_level is Level.HIGH
    assert task.claims == [VERIFIED_CLAIM]
    assert task.urgent_event_candidates == [CANDIDATE, SECOND_CANDIDATE]
    assert task.data_gaps == [GAP]
    assert task.offense.offense_id == 4711
    assert task.enrichment.catalog.rules[0].rule_id == 100234
    assert [ref.evidence_id for ref in task.evidence] == list(CONTEXT_EVIDENCE)


def test_an_unknown_field_in_the_task_is_refused() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        ReportingTask.model_validate(reporting_task().model_dump() | {"verdict": "fp"})


def test_the_aliases_cover_every_citation_in_the_task() -> None:
    # T-38: the model cites `ev_c<n>`, `n` the item's place in the input's evidence. A claim's
    # evidence IDs and a candidate's evidence_id are therefore both in `evidence`, and the
    # prompt's aliases line up with them.
    task = reporting_task(candidates=[CANDIDATE, SECOND_CANDIDATE])

    text = _agent(TestModel()).render_instructions(task, nonce=NONCE)

    cited = {*task.claims[0].evidence_ids, *(e.evidence_id for e in task.urgent_event_candidates)}
    assert cited == {ref.evidence_id for ref in task.evidence}
    assert [
        (block["evidence_id"], block["source"])
        for block in BLOCK.finditer(text)
        if block["source"].endswith(".evidence")
    ] == [("ev_c1", "qradar.evidence"), ("ev_c2", "falcon.evidence")]


def test_the_prompt_shows_the_evidence_under_its_context_alias() -> None:
    text = _agent(TestModel()).render_instructions(
        reporting_task(candidates=[CANDIDATE, SECOND_CANDIDATE]), nonce=NONCE
    )

    assert [
        (block["evidence_id"], block["source"])
        for block in BLOCK.finditer(text)
        if block["source"].endswith(".evidence")
    ] == [("ev_c1", "qradar.evidence"), ("ev_c2", "falcon.evidence")]
    # A block shows neither the gateway's ID nor the query hash (decision T-27).
    for block in BLOCK.finditer(text):
        if block["source"].endswith(".evidence"):
            for evidence_id in CONTEXT_EVIDENCE:
                assert evidence_id not in block["content"]
    assert CONTEXT_QUERY in text


def test_the_candidate_evidence_id_is_copied_not_cited() -> None:
    # Criterion 5 lists `evidence_id` among the identifiers the model copies, so it appears in
    # the candidate block; the model never guesses it, and `CandidatesUnchanged` compares it.
    text = _agent(TestModel()).render_instructions(reporting_task(), nonce=NONCE)

    [block] = [b for b in BLOCK.finditer(text) if b["source"] == "qradar.urgent_event"]
    assert json.loads(block["content"])["evidence_id"] == CONTEXT_EVIDENCE[0]


def test_the_claims_evidence_ids_do_not_reach_the_model() -> None:
    text = _agent(TestModel()).render_instructions(reporting_task(), nonce=NONCE)

    claim_block = next(
        block["content"] for block in BLOCK.finditer(text) if block["source"] == "qradar.claim"
    )
    assert VERIFIED_CLAIM.text in claim_block
    assert "evidence_ids" not in claim_block


def test_the_offense_name_and_rule_names_stay_inside_an_untrusted_block() -> None:
    text = _agent(TestModel()).render_instructions(reporting_task(), nonce=NONCE)

    [offense_block] = [b for b in BLOCK.finditer(text) if b["source"] == "qradar.offense"]
    payload = json.loads(offense_block["content"])
    # The description and the rule names are untrusted: their tags are neutralized, so the
    # attacker's text cannot leave the block (docs/impl/prompts.md).
    assert payload["description"] == offense().description.replace("<", "&lt;")
    assert payload["rule_names"] == [name.replace("<", "&lt;") for name in offense().rule_names]
    assert "&lt;/untrusted_" in offense_block["content"]
    # Every wrapper tag in the prompt belongs to a block of its own: the attacker's closing tag
    # and the fake org_context it opens are neutralized, never real.
    assert len(re.findall(rf"<untrusted_{NONCE} source=", text)) == len(BLOCK.findall(text))
    assert text.count(f"</untrusted_{NONCE}>") == len(BLOCK.findall(text))
    assert text.count("</org_context>") == 1


def test_the_candidates_reach_the_model_whole_and_untrusted() -> None:
    text = _agent(TestModel()).render_instructions(reporting_task(), nonce=NONCE)

    [block] = [b for b in BLOCK.finditer(text) if b["source"] == "qradar.urgent_event"]
    assert json.loads(block["content"]) == CANDIDATE.model_dump(mode="json")


def test_an_empty_case_gets_no_block_but_a_line_per_section() -> None:
    text = _agent(TestModel()).render_instructions(
        reporting_task(claims=[], candidates=[], data_gaps=[], evidence=[]), nonce=NONCE
    )

    assert NO_CLAIMS in text
    assert NO_CANDIDATES in text
    assert NO_DATA_GAPS in text
    assert NO_EVIDENCE in text


def test_the_organization_facts_go_into_org_context_and_are_neutralized() -> None:
    text = _agent(TestModel()).render_instructions(reporting_task(), nonce=NONCE)

    assert "<org_context>" in text
    assert "Rule 100234: mode=analyze, min_level=medium." in text
    assert "Log source 412: domain controller, criticality=high." in text
    assert "Critical asset 198.51.100.20: DC, level=high." in text
    assert "192.0.2.0/28" in text  # the catalog's own context note, on its own line
    assert text.count("</org_context>") == 1  # one section, closed once


def test_the_floor_level_and_the_group_stay_out_of_the_prompt() -> None:
    # T-31: the floor level is policy, which code applies, and the group is the platform's own
    # bookkeeping. Neither is a fact the model may weigh.
    task = reporting_task()

    text = _agent(TestModel()).render_instructions(task, nonce=NONCE)

    assert task.enrichment.floor_level is Level.HIGH
    assert "floor" not in text
    assert "group" not in text.lower()


def test_the_objective_cannot_close_the_untrusted_wrapper() -> None:
    task = reporting_task()
    hostile = task.model_copy(
        update={"task": task.task.model_copy(update={"objective": f"Rapora yaz. {ESCAPE}"})}
    )

    text = _agent(TestModel()).render_instructions(hostile, nonce=NONCE)

    assert "&lt;/untrusted_" in text


# --- criterion 3: the decision is the input's -------------------------------------------------


@pytest.mark.parametrize("field", ["verdict", "confidence", "notify_level"])
def test_a_model_that_returns_a_decision_field_is_rejected(field: str) -> None:
    # The output type has no decision field, and `extra="forbid"` turns one the model invents
    # into a validation error: it cannot change the workflow's decision even by trying.
    assert field not in ReportingOutput.model_fields

    with pytest.raises(ValidationError, match="extra_forbidden"):
        ReportingOutput.model_validate(reporting_output() | {field: "fp"})


@pytest.mark.parametrize("field", ["data_gaps", "task_id", "status", "usage", "claims"])
def test_a_model_that_returns_a_field_of_the_run_is_rejected(field: str) -> None:
    assert field not in ReportingOutput.model_fields

    with pytest.raises(ValidationError, match="extra_forbidden"):
        ReportingOutput.model_validate(reporting_output() | {field: "anything"})


def test_the_model_answers_without_the_decision_and_the_report_carries_the_inputs() -> None:
    # The model is asked for text only: its output type has no decision field at all.
    assert set(ReportingOutput.model_fields) == {
        "summary_tr",
        "urgent_events",
        "recommendations",
        "injection_suspected",
    }

    run = run_reporting(_agent(ScriptedModel(answer(reporting_output())).model))

    assert run.status is RunStatus.COMPLETED
    assert run.result is not None
    assert run.result.verdict is CaseVerdict.SUSPICIOUS
    assert run.result.confidence is Confidence.MEDIUM
    assert run.result.notify_level is Level.HIGH


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        pytest.param(
            CaseDecision(verdict=CaseVerdict.FP, confidence=Confidence.LOW, notify_level=Level.LOW),
            CaseVerdict.FP,
            id="fp",
        ),
        pytest.param(
            CaseDecision(
                verdict=CaseVerdict.TP, confidence=Confidence.HIGH, notify_level=Level.CRITICAL
            ),
            CaseVerdict.TP,
            id="tp",
        ),
        pytest.param(
            CaseDecision(
                verdict=CaseVerdict.SUSPICIOUS,
                confidence=Confidence.MEDIUM,
                notify_level=Level.MEDIUM,
            ),
            CaseVerdict.SUSPICIOUS,
            id="suspicious",
        ),
    ],
)
def test_whatever_the_workflow_decided_the_report_says_that(
    decision: CaseDecision, expected: CaseVerdict
) -> None:
    run = run_reporting(
        _agent(ScriptedModel(answer(reporting_output())).model),
        reporting_task(decision=decision),
    )

    assert run.result is not None
    assert run.result.verdict is expected
    assert run.result.confidence is decision.confidence
    assert run.result.notify_level is decision.notify_level


def test_the_decision_reaches_the_model_as_a_fact() -> None:
    text = _agent(TestModel()).render_instructions(
        reporting_task(
            decision=CaseDecision(
                verdict=CaseVerdict.TP, confidence=Confidence.HIGH, notify_level=Level.CRITICAL
            )
        ),
        nonce=NONCE,
    )

    assert "verdict: tp" in text
    assert "confidence: high" in text
    assert "notify_level: critical" in text
    assert "it is a fact, not evidence" in text


def test_the_decision_section_is_not_wrapped_as_untrusted() -> None:
    # The decision is the workflow's own, so it is plain text: it is not log data and carries
    # no evidence_id (T-20's policy layer is not in the prompt either).
    text = _agent(TestModel()).render_instructions(reporting_task(), nonce=NONCE)

    section = text.split("verdict: suspicious")[0].rsplit("\n\n", 1)[-1]
    assert "Verdict" in section or "it is a fact, not evidence" in section
    assert "<untrusted_" not in section
    assert "<org_context>" not in section
    assert not any("verdict: suspicious" in block["content"] for block in BLOCK.finditer(text))


# --- criterion 4: the summary -----------------------------------------------------------------


def test_a_summary_over_400_characters_is_rejected() -> None:
    long = "Girisler " * 60  # 480 characters: it would not fit the QRadar note
    model = TestModel(custom_output_args=reporting_output(summary_tr=long))

    run = run_reporting(_agent(model))

    assert run.status is RunStatus.FAILED  # TestModel keeps answering too long
    assert run.result is None
    assert len(long) > 400
    assert retry_prompts(run.messages)  # the limit came back to the model each time
    # CaseReport.summary_tr is the looser contract field; the agent applies the note's 400, so
    # the same summary always fits both (contracts.md, NoteContent.summary_tr).
    assert SUMMARY_MAX_LENGTH == 600


def test_a_summary_of_exactly_400_characters_is_accepted() -> None:
    model = TestModel(custom_output_args=reporting_output(summary_tr="a" * 400))

    run = run_reporting(_agent(model))

    assert run.status is RunStatus.COMPLETED
    assert run.result is not None
    assert len(run.result.summary_tr) == 400


def test_the_prompt_tells_the_model_the_400_character_limit() -> None:
    text = _agent(TestModel()).prompt.template

    assert "at most 400 characters" in text
    assert "at most three sentences" in text


def test_the_prompt_carries_the_turkish_report_rules_of_prompts_md() -> None:
    text = _agent(TestModel()).prompt.template

    # The rules docs/impl/prompts.md lists under "Türkçe rapor kuralları".
    assert "Short, plain sentences" in text  # simple, short sentences
    assert "Do not translate technical terms" in text  # no translated technical terms
    assert "Europe/Istanbul" in text  # local time
    assert "2026-10-02 14:05" in text  # the local time format of prompts.md
    assert "Say uncertainty out loud" in text  # uncertainty stated
    assert "Never copy log text" in text  # no log text copied


def test_the_prompt_says_what_the_summary_must_contain() -> None:
    text = _agent(TestModel()).prompt.template

    assert "what happened, what you decided and why, what the operator does first" in text
    assert "Every sentence rests on a claim, an evidence block or a data gap" in text


def test_a_summary_within_the_limit_is_accepted() -> None:
    run = run_reporting(_agent(ScriptedModel(answer(reporting_output())).model))

    assert run.status is RunStatus.COMPLETED
    assert run.result is not None
    assert run.result.summary_tr == SUMMARY_TR
    assert len(run.result.summary_tr) <= 400


# --- criterion 5: the urgent events -----------------------------------------------------------


def test_the_events_carry_the_candidates_identifiers_verbatim() -> None:
    run = run_reporting(
        _agent(ScriptedModel(answer(reporting_output(reported_event(CANDIDATE)))).model)
    )

    assert run.status is RunStatus.COMPLETED
    assert run.result is not None
    [event] = run.result.urgent_events
    copied = CANDIDATE.model_dump(exclude={"reason", "checklist"})
    assert event.model_dump(exclude={"reason", "checklist"}) == copied
    assert event.aql == CANDIDATE.aql
    assert event.evidence_id == CANDIDATE.evidence_id
    assert event.qid == CANDIDATE.qid
    assert event.username == CANDIDATE.username


def test_the_candidates_aql_is_copied_and_not_rewritten_or_rechecked() -> None:
    # T-047 (1): the AQL passed the Guard as a candidate's, so Reporting copies it as it is.
    # Reporting creates its agent with `aql=None`: it does not re-run the Guard.
    run = run_reporting(
        _agent(ScriptedModel(answer(reporting_output(reported_event(CANDIDATE)))).model)
    )

    assert run.result is not None
    assert run.result.urgent_events[0].aql == CANDIDATE.aql


def test_a_fabricated_event_is_sent_back_to_the_model() -> None:
    invented = reported_event(CANDIDATE, event_name="Successful Logon", qid=4624)
    script = ScriptedModel(
        answer(reporting_output(invented)),
        answer(reporting_output(reported_event(CANDIDATE))),
    )

    run = run_reporting(_agent(script.model))

    [retry] = retry_prompts(run.messages)
    assert "No urgent event candidate has these identifiers" in retry.model_response()
    assert "Add no event of your own" in retry.model_response()
    assert run.status is RunStatus.COMPLETED
    assert run.result is not None
    assert run.result.urgent_events[0].event_name == CANDIDATE.event_name


def test_a_changed_identifier_is_sent_back_with_the_candidates_own_value() -> None:
    changed = reported_event(CANDIDATE, source="198.51.100.99")
    script = ScriptedModel(
        answer(reporting_output(changed)),
        answer(reporting_output(reported_event(CANDIDATE))),
    )

    run = run_reporting(_agent(script.model))

    [retry] = retry_prompts(run.messages)
    assert "differ from its candidate in source" in retry.model_response()
    assert "source='203.0.113.77'" in retry.model_response()
    assert run.status is RunStatus.COMPLETED
    assert run.result is not None
    assert run.result.urgent_events[0].source == "203.0.113.77"


def test_a_reformatted_time_is_rejected_too() -> None:
    changed = reported_event(CANDIDATE, time="2026-10-02T15:00:00Z")
    script = ScriptedModel(
        answer(reporting_output(changed)),
        answer(reporting_output(reported_event(CANDIDATE))),
    )

    run = run_reporting(_agent(script.model))

    assert retry_prompts(run.messages)
    assert run.result is not None
    assert run.result.urgent_events[0].time == CANDIDATE.time


@pytest.mark.parametrize(
    ("field", "value"),
    [
        pytest.param(
            "aql",
            "SELECT * FROM events LIMIT 1 START '2026-10-02 13:00' STOP '2026-10-02 14:00'",
            id="aql",
        ),
        pytest.param("log_source", "413 Başka Log Kaynağı", id="log_source"),
        pytest.param("username", "svc_backup_9999", id="username"),
        pytest.param("destination", "198.51.100.99", id="destination"),
        pytest.param("qid", 4624, id="qid"),
        pytest.param("source", "198.51.100.99", id="source"),
        pytest.param("time", "2026-10-02T15:00:00Z", id="time"),
    ],
)
def test_every_identifier_field_is_compared(field: str, value: str | int) -> None:
    changed = reported_event(CANDIDATE, **cast("dict[str, Any]", {field: value}))

    with pytest.raises(ModelRetry, match=f"differ from its candidate in {field}"):
        CandidatesUnchanged([CANDIDATE])(
            _ctx(), ReportingOutput.model_validate(reporting_output(changed))
        )


def test_a_changed_evidence_id_is_refused() -> None:
    changed = reported_event(CANDIDATE, evidence_id=CONTEXT_EVIDENCE[1])

    with pytest.raises(ModelRetry, match="differ from its candidate in evidence_id"):
        CandidatesUnchanged([CANDIDATE])(
            _ctx(), ReportingOutput.model_validate(reporting_output(changed))
        )


def test_a_changed_event_name_is_refused_as_an_unknown_candidate() -> None:
    # With no candidate of that name, the model invented an event; the message says so instead
    # of listing identifiers of a candidate it did not choose.
    changed = reported_event(CANDIDATE, event_name="Successful Logon")

    with pytest.raises(ModelRetry, match="No urgent event candidate has these identifiers"):
        CandidatesUnchanged([CANDIDATE])(
            _ctx(), ReportingOutput.model_validate(reporting_output(changed))
        )


@pytest.mark.parametrize(
    "ranks",
    [
        pytest.param([1, 3], id="a gap"),
        pytest.param([1, 1], id="a repeated rank"),
        pytest.param([0, 1], id="not starting at one"),
        pytest.param([2, 1], id="out of order"),
    ],
)
def test_rank_starts_at_one_and_is_consecutive_and_unique(ranks: list[int]) -> None:
    events = [
        reported_event(CANDIDATE, rank=ranks[0]),
        reported_event(SECOND_CANDIDATE, rank=ranks[1]),
    ]

    # rank 0 is refused by the field's own `ge=1`; the rest by the output's own validator.
    match = "greater than or equal to 1" if 0 in ranks else "ranked 1, 2"
    with pytest.raises(ValidationError, match=match):
        ReportingOutput.model_validate(reporting_output(*events))


def test_consecutive_ranks_in_order_are_accepted() -> None:
    output = ReportingOutput.model_validate(
        reporting_output(
            reported_event(SECOND_CANDIDATE, rank=1), reported_event(CANDIDATE, rank=2)
        )
    )

    assert [event.rank for event in output.urgent_events] == [1, 2]


def test_at_most_fifteen_urgent_events() -> None:
    many = [reported_event(CANDIDATE, rank=n) for n in range(1, 17)]

    with pytest.raises(ValidationError, match="at most 15"):
        ReportingOutput.model_validate(reporting_output(*many))

    assert len(ReportingOutput.model_validate(reporting_output(*many[:15])).urgent_events) == 15


def test_the_model_may_keep_a_subset_and_reorder_it() -> None:
    script = ScriptedModel(
        answer(
            reporting_output(
                reported_event(SECOND_CANDIDATE, rank=1),
                reported_event(CANDIDATE, rank=2),
            )
        )
    )

    run = run_reporting(
        _agent(script.model), reporting_task(candidates=[CANDIDATE, SECOND_CANDIDATE])
    )

    assert run.status is RunStatus.COMPLETED
    assert run.result is not None
    assert [event.rank for event in run.result.urgent_events] == [1, 2]
    assert [event.event_name for event in run.result.urgent_events] == [
        SECOND_CANDIDATE.event_name,
        CANDIDATE.event_name,
    ]


def test_the_model_may_return_no_events_at_all() -> None:
    run = run_reporting(_agent(ScriptedModel(answer(reporting_output())).model))

    assert run.status is RunStatus.COMPLETED
    assert run.result is not None
    assert run.result.urgent_events == []


def test_a_candidate_cannot_be_returned_twice() -> None:
    doubled = reporting_output(reported_event(CANDIDATE, rank=1), reported_event(CANDIDATE, rank=2))
    script = ScriptedModel(answer(doubled))

    run = run_reporting(_agent(script.model))

    assert run.status is RunStatus.FAILED  # no retry can fix a doubled event
    assert run.result is None


def test_reason_and_checklist_are_the_models_own_fields() -> None:
    script = ScriptedModel(
        answer(reporting_output(reported_event(CANDIDATE, reason="Operatore ilk bakilacak kayit.")))
    )

    run = run_reporting(_agent(script.model))

    assert run.result is not None
    # `reason` and `checklist` are the model's: the candidate's own text is not carried over.
    assert run.result.urgent_events[0].reason == "Operatore ilk bakilacak kayit."
    assert run.result.urgent_events[0].checklist == ["Bu kullanicinin VPN girisi var mi?"]
    assert run.result.urgent_events[0].checklist != CANDIDATE.checklist


def test_the_candidates_own_reason_never_reaches_the_report() -> None:
    # Only rank, reason and checklist are Reporting's, so the candidate's `reason` is input text
    # the model may read but must not copy (docs/impl/prompts.md).
    script = ScriptedModel(
        answer(reporting_output(reported_event(CANDIDATE, reason="Kontrol edilecek kayit.")))
    )

    run = run_reporting(_agent(script.model))

    assert run.result is not None
    assert CANDIDATE.reason not in run.result.urgent_events[0].reason


def test_at_most_five_checklist_items() -> None:
    six = reported_event(CANDIDATE, checklist=["a", "b", "c", "d", "e", "f"])

    with pytest.raises(ValidationError, match="at most 5"):
        ReportingOutput.model_validate(reporting_output(six))


# --- criterion 6: recommendations and data gaps ------------------------------------------------


def test_the_recommendations_are_the_models_own_and_turkish() -> None:
    script = ScriptedModel(
        answer(
            reporting_output(
                recommendations=[
                    {
                        "action_type": "block_ioc_manual",
                        "target": "203.0.113.77",
                        "rationale": "Kaynak IP bir IOC ile eslesti.",
                        "evidence_ids": ["ev_c1"],
                    }
                ]
            )
        )
    )

    run = run_reporting(_agent(script.model))

    assert run.result is not None
    [recommendation] = run.result.recommendations
    assert recommendation.action_type.value == "block_ioc_manual"
    assert recommendation.rationale == "Kaynak IP bir IOC ile eslesti."
    assert recommendation.evidence_ids == [CONTEXT_EVIDENCE[0]]


def test_a_recommendation_cites_only_the_input_evidence() -> None:
    # ev_c99 is no alias of the run: check_evidence sends it back (criterion 6, decision T-38).
    script = ScriptedModel(
        answer(
            reporting_output(
                recommendations=[
                    {
                        "action_type": "investigate_further",
                        "target": "203.0.113.77",
                        "rationale": "Kaynak IP bir IOC ile eslesti.",
                        "evidence_ids": ["ev_c99"],
                    }
                ]
            )
        ),
        answer(reporting_output()),
    )

    run = run_reporting(_agent(script.model))

    [retry] = retry_prompts(run.messages)
    assert "in this run: ev_c99" in retry.model_response()
    assert "You can cite only ev_c1, ev_c2" in retry.model_response()
    assert run.status is RunStatus.COMPLETED
    assert run.result is not None
    assert run.result.recommendations[0].evidence_ids == [CONTEXT_EVIDENCE[0]]


def test_an_action_type_outside_the_fixed_list_is_rejected() -> None:
    invented = reporting_output(
        recommendations=[
            {
                "action_type": "isolate_host",
                "target": "203.0.113.77",
                "rationale": "Sunucuyu izole et.",
                "evidence_ids": ["ev_c1"],
            }
        ]
    )

    with pytest.raises(ValidationError):
        ReportingOutput.model_validate(invented)


def test_at_most_eight_recommendations() -> None:
    many = [
        {
            "action_type": "investigate_further",
            "target": f"203.0.113.{n}",
            "rationale": "Incele.",
            "evidence_ids": ["ev_c1"],
        }
        for n in range(9)
    ]

    with pytest.raises(ValidationError, match="at most 8"):
        ReportingOutput.model_validate(reporting_output(recommendations=many))


def test_a_recommendation_is_optional() -> None:
    run = run_reporting(_agent(ScriptedModel(answer(reporting_output(recommendations=[]))).model))

    assert run.result is not None
    assert run.result.recommendations == []


def test_the_data_gaps_are_the_inputs_and_the_model_invents_none() -> None:
    run = run_reporting(
        _agent(ScriptedModel(answer(reporting_output())).model), reporting_task(data_gaps=[GAP])
    )

    assert run.result is not None
    assert run.result.data_gaps == [GAP]
    # The model has no data_gaps field to fill at all.
    assert "data_gaps" not in ReportingOutput.model_fields


def test_a_gapless_case_reports_no_gaps() -> None:
    run = run_reporting(_agent(ScriptedModel(answer(reporting_output())).model))

    assert run.result is not None
    assert run.result.data_gaps == []


def test_the_data_gaps_reach_the_model_untrusted() -> None:
    text = _agent(TestModel()).render_instructions(reporting_task(data_gaps=[GAP]), nonce=NONCE)

    [block] = [b for b in BLOCK.finditer(text) if b["source"] == "qradar.data_gap"]
    assert json.loads(block["content"]) == GAP.model_dump(mode="json")


# --- criterion 7: no log text is copied -------------------------------------------------------


def test_a_quoted_excerpt_in_the_summary_is_rejected() -> None:
    excerpt = evidence_ref(CONTEXT_EVIDENCE[0]).excerpt
    script = ScriptedModel(
        answer(reporting_output(summary_tr=f"Olay {excerpt} görüldü.")),
        answer(reporting_output()),
    )

    run = run_reporting(_agent(script.model))

    [retry] = retry_prompts(run.messages)
    assert "copies 20 or more characters" in retry.model_response()
    assert "evidence excerpt of ev_c1" in retry.model_response()
    assert "Summarize it in your own Turkish words" in retry.model_response()
    assert run.status is RunStatus.COMPLETED
    assert run.result is not None
    assert run.result.summary_tr == SUMMARY_TR


@pytest.mark.parametrize("field", ["reason", "rationale", "checklist"])
def test_a_quoted_excerpt_in_any_free_text_field_is_rejected(field: str) -> None:
    excerpt = evidence_ref(CONTEXT_EVIDENCE[0]).excerpt
    if field == "checklist":
        bad = reporting_output(reported_event(CANDIDATE, checklist=[f"Log: {excerpt}"]))
    elif field == "rationale":
        bad = reporting_output(
            recommendations=[
                {
                    "action_type": "investigate_further",
                    "target": "203.0.113.77",
                    "rationale": f"Log {excerpt}",
                    "evidence_ids": ["ev_c1"],
                }
            ]
        )
    else:
        bad = reporting_output(reported_event(CANDIDATE, reason=f"Log {excerpt}"))
    script = ScriptedModel(answer(bad), answer(reporting_output()))

    run = run_reporting(_agent(script.model))

    retries = retry_prompts(run.messages)
    assert retries, f"{field} copy accepted"
    assert f"Your {field}" in retries[0].model_response()
    assert run.status is RunStatus.COMPLETED


def test_output_without_the_quoted_text_is_accepted() -> None:
    run = run_reporting(
        _agent(ScriptedModel(answer(reporting_output(reported_event(CANDIDATE)))).model)
    )

    assert run.status is RunStatus.COMPLETED
    assert retry_prompts(run.messages) == []


def test_structural_fields_may_be_repeated() -> None:
    # IPs, user names and event names are the report's subject, not a quote.
    run = run_reporting(
        _agent(
            ScriptedModel(
                answer(reporting_output(reported_event(CANDIDATE, reason="203.0.113.77 kaynakli.")))
            ).model
        )
    )

    assert run.status is RunStatus.COMPLETED
    assert run.result is not None
    assert run.result.urgent_events[0].reason == "203.0.113.77 kaynakli."


def test_the_check_finds_a_quote_wherever_in_the_excerpt_it_sits() -> None:
    # The model may quote the middle of the excerpt, not only its start.
    excerpt = evidence_ref(CONTEXT_EVIDENCE[0]).excerpt
    output = ReportingOutput.model_validate(
        reporting_output(summary_tr=f"Kayit: {excerpt[10:34]}.")
    )

    with pytest.raises(ModelRetry, match="copies 20 or more characters"):
        NoLogText([evidence_ref(CONTEXT_EVIDENCE[0])])(_ctx(), output)


def test_a_quote_spanning_a_space_is_still_a_quote() -> None:
    # The excerpt's words are each under the limit, so the check compares windows of the whole
    # excerpt, not one whitespace-free run: quoting across words is a copy too.
    ref = evidence_ref(CONTEXT_EVIDENCE[0], excerpt="user svc_backup_7731 locked out")
    output = ReportingOutput.model_validate(
        reporting_output(summary_tr="svc_backup_7731 locked out")
    )

    with pytest.raises(ModelRetry, match="copies 20 or more characters"):
        NoLogText([ref])(_ctx(), output)


def test_a_short_structural_repeat_is_not_a_quote() -> None:
    # Under twenty characters is a field value, not log text.
    short = evidence_ref(CONTEXT_EVIDENCE[0]).excerpt[:10]
    output = ReportingOutput.model_validate(reporting_output(summary_tr=f"IP {short}."))

    assert NoLogText([evidence_ref(CONTEXT_EVIDENCE[0])])(_ctx(), output) is output


def test_evidence_without_an_excerpt_copies_nothing() -> None:
    # A 30-day-old evidence has an empty excerpt (T-047 (2)): there is nothing to quote.
    old = evidence_ref(CONTEXT_EVIDENCE[0], excerpt="")
    output = ReportingOutput.model_validate(reporting_output(summary_tr="Bir iki cumle."))

    assert NoLogText([old])(_ctx(), output) is output


def test_the_check_looks_at_every_evidence_of_the_run() -> None:
    # The second evidence of the run is checked too, not only the first.
    excerpt = "Falcon tarafindan toplandı ve maskelendi"
    second = evidence_ref(CONTEXT_EVIDENCE[1], excerpt=f'[{{"note":"{excerpt}"}}]')
    output = ReportingOutput.model_validate(reporting_output(summary_tr=f"Kayit: {excerpt}."))

    with pytest.raises(ModelRetry, match="ev_c2"):
        NoLogText([evidence_ref(CONTEXT_EVIDENCE[0]), second])(_ctx(), output)


def test_the_rejection_does_not_repeat_the_quoted_log_text() -> None:
    # The message names the evidence and the field, so the model is corrected without the log
    # text being sent back into the conversation.
    ref = evidence_ref(CONTEXT_EVIDENCE[0], excerpt="saldirgan metni burada uzun bir ornek")
    output = ReportingOutput.model_validate(
        reporting_output(summary_tr="saldirgan metni burada uzun bir ornek.")
    )

    with pytest.raises(ModelRetry) as raised:
        NoLogText([ref])(_ctx(), output)

    assert "saldirgan metni" not in str(raised.value)


def test_the_prompt_tells_the_model_about_the_twenty_character_rule() -> None:
    text = _agent(TestModel()).prompt.template

    assert "twenty or more characters" in text
    assert "rejected" in text


# --- the run -----------------------------------------------------------------------------------


def test_a_complete_run_returns_a_case_report_and_records_the_run() -> None:
    script = ScriptedModel(answer(reporting_output(reported_event(CANDIDATE))))

    run = run_reporting(_agent(script.model))

    assert run.status is RunStatus.COMPLETED
    assert run.error is None
    assert isinstance(run.result, CaseReport)
    assert run.result.task_id == "task-4711-reporting-1"
    assert run.result.status is RunStatus.COMPLETED
    assert run.result.claims == [VERIFIED_CLAIM]
    assert run.result.injection_suspected is True
    assert run.usage.tool_calls == 0
    assert run.usage.tokens > 0
    assert run.prompt_version == "reporting/v1"
    assert run.prompt_hash == _agent(TestModel()).prompt.sha256


def test_the_model_sees_no_tools_and_one_output_tool() -> None:
    script = ScriptedModel(answer(reporting_output()))

    run_reporting(_agent(script.model))

    [(_, info)] = script.requests
    assert info.function_tools == []
    assert [tool.name for tool in info.output_tools] == ["final_result"]


def test_the_objective_is_the_user_prompt() -> None:
    script = ScriptedModel(answer(reporting_output()))

    run_reporting(_agent(script.model))

    [messages, _] = script.requests[0]
    [request] = messages
    [part] = request.parts
    assert isinstance(part, UserPromptPart)
    assert "Case 4711" in part.content


def test_a_task_for_another_agent_is_refused() -> None:
    task = reporting_task()
    wrong = task.model_copy(update={"task": task.task.model_copy(update={"agent_id": "triage"})})

    with pytest.raises(ValueError, match="task is for agent 'triage', not 'reporting'"):
        run_reporting(_agent(TestModel()), wrong)


def test_output_that_stays_invalid_fails_the_run() -> None:
    run = run_reporting(_agent(ScriptedModel(answer(reporting_output(summary_tr="x" * 900))).model))

    assert run.status is RunStatus.FAILED
    assert run.result is None
    assert run.error is not None
    assert "output retries" in run.error


def test_the_output_model_passes_the_evidence_field_check() -> None:
    # UrgentEvent.evidence_id and Recommendation.evidence_ids are typed EvidenceId, so
    # check_evidence can map the model's `ev_c<n>` to the gateway's ID (decision T-38).
    check_evidence_fields(ReportingOutput)


def test_the_evidence_id_of_an_event_reaches_the_report_unchanged() -> None:
    run = run_reporting(
        _agent(ScriptedModel(answer(reporting_output(reported_event(CANDIDATE)))).model)
    )

    assert run.result is not None
    assert run.result.urgent_events[0].evidence_id == CONTEXT_EVIDENCE[0]


def test_a_case_without_evidence_runs_and_cites_nothing() -> None:
    script = ScriptedModel(answer(reporting_output(recommendations=[])))

    run = run_reporting(_agent(script.model), reporting_task(claims=[], evidence=[], candidates=[]))

    assert run.status is RunStatus.COMPLETED
    assert run.result is not None
    assert run.result.claims == []
    assert run.result.urgent_events == []
    assert run.result.recommendations == []


def test_a_citation_in_a_case_without_evidence_is_sent_back_to_the_model() -> None:
    # There is nothing to cite when the run has no evidence, so the model is told to drop the
    # recommendation rather than to keep an alias that does not exist (T-043).
    script = ScriptedModel(
        answer(reporting_output()),  # its recommendation cites ev_c1
        answer(reporting_output(recommendations=[])),
    )

    run = run_reporting(_agent(script.model), reporting_task(claims=[], evidence=[], candidates=[]))

    [retry] = retry_prompts(run.messages)
    assert "returned no evidence you can cite" in retry.model_response()
    assert "remove the recommendation" in retry.model_response()
    assert run.status is RunStatus.COMPLETED
    assert run.result is not None
    assert run.result.recommendations == []


# --- configuration ------------------------------------------------------------------------------


def test_the_spec_declares_the_reporting_schemas() -> None:
    assert REPORTING_SPEC.input_schema == "ReportingTask"
    assert REPORTING_SPEC.output_schema == "CaseReport"
    assert REPORTING_SPEC.placeholders == frozenset(
        {
            "decision",
            "claims",
            "evidence",
            "urgent_event_candidates",
            "data_gaps",
            "offense",
            "org_context",
        }
    )
    assert REPORTING_SPEC.retries == {"tools": 0, "output": 3}


def test_the_prompt_takes_exactly_the_placeholders_the_agent_fills() -> None:
    prompt = load_prompt(REPO_ROOT, REPORTING_PROMPT_PATH, shared_rules=SHARED_RULES)

    assert (
        check_agent_config(
            REPORTING_SPEC, load_manifest(REPORTING_MANIFEST_PATH, registry()), prompt
        )
        is None
    )


@pytest.mark.parametrize(
    ("update", "message"),
    [
        pytest.param(
            {"input_schema": "TriageTask"},
            r"declares TriageTask -> CaseReport, not ReportingTask",
            id="another input schema",
        ),
        pytest.param(
            {"output_schema": "CasePlan"},
            r"declares ReportingTask -> CasePlan",
            id="another output schema",
        ),
        pytest.param(
            {"prompt": "prompts/triage/v2.md"},
            "uses prompts/triage/v2.md, not",
            id="another prompt",
        ),
        pytest.param(
            {"shared_rules": "prompts/_shared/rules/v1.md"},
            "uses prompts/_shared/rules/v1.md",
            id="another shared rules version",
        ),
    ],
)
def test_the_config_check_refuses_another_agents_manifest(
    update: dict[str, str], message: str
) -> None:
    manifest = load_manifest(REPORTING_MANIFEST_PATH, registry())
    prompt = load_prompt(REPO_ROOT, REPORTING_PROMPT_PATH, shared_rules=SHARED_RULES)

    with pytest.raises(ValueError, match=message):
        check_agent_config(REPORTING_SPEC, manifest.model_copy(update=update), prompt)


def test_the_config_check_refuses_a_toolset_profile() -> None:
    manifest = load_manifest(REPORTING_MANIFEST_PATH, registry())
    prompt = load_prompt(REPO_ROOT, REPORTING_PROMPT_PATH, shared_rules=SHARED_RULES)

    with pytest.raises(ValueError, match="names toolset profile 'qradar-triage-read'"):
        check_agent_config(
            REPORTING_SPEC,
            manifest.model_copy(update={"toolset_profile": "qradar-triage-read"}),
            prompt,
        )
    assert set(PROFILES) == {"qradar-triage-read", "qradar-investigate-read"}
