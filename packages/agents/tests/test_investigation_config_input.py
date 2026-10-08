"""T-023 criteria 1 and 2: manifest, input and prompt trust layers."""

import json

import pytest
import yaml
from pydantic import ValidationError
from pydantic_ai.messages import ModelRequest, UserPromptPart

from ais0c_agents import InvestigationTask, InvestigationTriage, load_manifest, load_model_registry
from ais0c_agents.investigation import RUN_PROMPT
from ais0c_contracts import Claim

from .helpers import (
    CONTEXT_EVIDENCE,
    ESCAPE,
    INJECTION,
    MODEL_REGISTRY,
    NONCE,
    REPO_ROOT,
    ScriptedModel,
    answer,
    context_evidence,
    lenient_tags,
    runbook,
)
from .investigation_helpers import (
    INVESTIGATION_MANIFEST,
    build_investigation,
    instruction_text,
    investigation_output,
    investigation_prompt,
    investigation_task,
    prompt_blocks,
    run_investigation,
)


def test_manifest_has_the_required_values_and_loads_against_the_registry() -> None:
    raw = yaml.safe_load(INVESTIGATION_MANIFEST.read_text(encoding="utf-8"))

    assert raw["id"] == "investigation"
    assert raw["version"] == "1.1.0"
    assert raw["workflow_types"] == ["case"]
    assert raw["model_alias"] == "soc-reasoning"
    assert raw["input_schema"] == "InvestigationTask"
    assert raw["output_schema"] == "InvestigationResult"
    assert raw["toolset_profile"] == "qradar-investigate-read"
    assert raw["max_steps"] == 30
    assert raw["budgets"] == {
        "tokens": 600000,
        "tool_calls": 24,
        "wall_clock_seconds": 360,
    }
    assert raw["prompt"] == "prompts/investigation/v2.md"
    assert raw["shared_rules"] == "prompts/_shared/rules/v2.md"

    loaded = load_manifest(INVESTIGATION_MANIFEST, load_model_registry(REPO_ROOT / MODEL_REGISTRY))
    assert loaded.model_alias == "soc-reasoning"
    assert investigation_prompt().path == loaded.prompt


def test_input_is_local_bounded_and_has_no_triage_rationale() -> None:
    assert InvestigationTask.__module__ == "ais0c_agents.investigation"
    assert set(InvestigationTask.model_fields) == {
        "task",
        "offense",
        "enrichment",
        "triage",
        "context_evidence",
        "skill",
        "knowledge",
    }
    assert "rationale" not in InvestigationTriage.model_fields
    with pytest.raises(ValidationError, match="rationale"):
        InvestigationTriage.model_validate(
            investigation_task().triage.model_dump() | {"rationale": "MUST_NOT_REACH_MODEL"}
        )

    task = investigation_task()
    with pytest.raises(ValidationError, match="context_evidence"):
        InvestigationTask.model_validate(
            task.model_dump() | {"context_evidence": [context_evidence()[0]] * 31}
        )
    with pytest.raises(ValidationError, match="knowledge"):
        InvestigationTask.model_validate(task.model_dump() | {"knowledge": [runbook()] * 11})


@pytest.mark.parametrize(
    ("skill_id", "tokens"),
    [
        # T-062: the measured skill (its suite sk-dcs-01-03) follows the investigation's guard.
        ("windows-dcsync", 600000),
        # T-062 (decision T-85): the unmeasured drafts carry the guide's starting values
        # (docs/impl/skill-authoring.md, section 3) until their suites measure them.
        ("password-spraying", 600000),
        ("vpn-new-country", 600000),
    ],
)
def test_the_draft_skills_token_budget_fits_one_investigation(skill_id: str, tokens: int) -> None:
    # T-062 (decision T-85): a skill's token limit is a runaway guard like the manifest's own;
    # a skill run never exceeds the investigation run's token budget.
    path = REPO_ROOT / "skills" / skill_id / "1.0.0" / "skill.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest = load_manifest(
        INVESTIGATION_MANIFEST, load_model_registry(REPO_ROOT / MODEL_REGISTRY)
    )

    assert raw["status"] == "draft"
    assert raw["budgets"]["tokens"] == tokens
    assert raw["budgets"]["tokens"] <= manifest.budgets.tokens == 600000


def test_every_input_reaches_the_model_in_its_own_trust_layer() -> None:
    script = ScriptedModel(answer(investigation_output("ev_c1", ranks=(1,))))

    run = run_investigation(build_investigation(script), investigation_task())

    assert run.result is not None
    instructions = instruction_text(script)
    blocks = prompt_blocks(instructions)
    sources = [source for source, _, _ in blocks]
    assert sources == [
        "agent.objective",
        "qradar.offense",
        "qradar.entity_resolution",
        "agent.claim",
        "agent.data_gap",
        "agent.focus",
        "qradar.evidence",
        "falcon.evidence",
        "kb.ioc",
        "kb.runbook",
    ]
    claim = json.loads(next(content for source, _, content in blocks if source == "agent.claim"))
    assert claim["evidence"] == ["ev_c1"]
    assert [alias for source, alias, _ in blocks if source.endswith(".evidence")] == [
        "ev_c1",
        "ev_c2",
    ]
    assert all(evidence_id not in instructions for evidence_id in CONTEXT_EVIDENCE)
    assert "MUST_NOT_REACH_MODEL" not in instructions
    assert "floor_level" not in instructions
    assert "group_id" not in instructions
    assert "<org_context>" in instructions
    assert "Rule 100234:" in instructions
    assert "Critical asset 198.51.100.20:" in instructions

    outside = instructions
    for source, alias, content in blocks:
        tag = (
            f'<untrusted_{NONCE} source="{source}" evidence_id="{alias}">\n'
            f"{content}\n</untrusted_{NONCE}>"
        )
        outside = outside.replace(tag, "")
        assert lenient_tags(content) == []
    for untrusted in (INJECTION, "svc_backup_7731", "Find the source of replication"):
        assert untrusted in instructions
        assert untrusted not in outside
    assert ESCAPE not in instructions

    user_prompts = [
        str(part.content)
        for message in script.requests[0][0]
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, UserPromptPart)
    ]
    assert user_prompts == [RUN_PROMPT]
    assert "Investigate possible DCSync" not in user_prompts[0]


def test_a_triage_claim_cannot_name_evidence_missing_from_the_input() -> None:
    task = investigation_task(context=False)
    triage = task.triage.model_copy(
        update={"claims": [Claim(text="A claim.", evidence_ids=[CONTEXT_EVIDENCE[0]])]}
    )

    with pytest.raises(ValidationError, match="evidence of every Triage claim"):
        InvestigationTask.model_validate(task.model_dump() | {"triage": triage})
