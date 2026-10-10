"""The Investigation and Verification adapters and their gold suites (T-052 criteria 8, 9, 10):
every scenario passes with a scripted model, k=2; each check fails when its fact is wrong; the
agent's task is built as the worker builds it; a run's file is written when the run ends."""

import asyncio
import hashlib
import io
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import JsonValue
from pydantic_ai.models import Model
from pydantic_ai.models.test import TestModel

from ais0c_activities.triage import evaluation_window
from ais0c_agents.investigation import MAX_CONTEXT_EVIDENCE
from ais0c_harness.eval import (
    AgentConfig,
    EvalRun,
    InvestigationAdapter,
    ScenarioBase,
    SuiteError,
    VerificationAdapter,
    agents_by_alias,
    load_agent_config,
    load_suite,
    load_suites,
)
from ais0c_harness.eval import cli as cli_module
from ais0c_harness.eval.config import agent_aliases
from ais0c_harness.eval.investigation import ExpectedEvent, InvestigationScenario, event_found
from ais0c_harness.eval.report import write_run_file
from ais0c_harness.eval.runner import RunOptions, run_eval
from ais0c_harness.eval.scripted_replay import scripted_replay_model
from ais0c_harness.eval.verification import VerificationScenario
from ais0c_harness.replay.gateway import ReplayGateway
from ais0c_harness.replay.recording import MANIFEST_FILE, recording_of, recording_path

from .eval_helpers import REGISTRY, REPO_ROOT, SUITES, gateway_profile_of

INVESTIGATION = "investigation-gold"
VERIFICATION = "verification-gold"
INV_01 = "inv-01-dcsync"
INV_02 = "inv-02-dcsync-no-skill"
VER_01 = "ver-01-refutable-ip"
VER_02 = "ver-02-all-correct"
INV_03 = "inv-03-waf-xss"
INV_04 = "inv-04-waf-scan-blocked"
INV_05 = "inv-05-approved-scanner"
INV_06 = "inv-06-kerberoasting"
VER_03 = "ver-03-xss-wrong-family"
VER_04 = "ver-04-scanner-claims-hold"
VER_05 = "ver-05-kerberoasting-logon"
RECORDING = "lab-30-dcsync"


def scenario_of(suite_id: str, scenario_id: str) -> InvestigationScenario | VerificationScenario:
    found = load_suite(SUITES / suite_id, root=REPO_ROOT)
    played = next(item.scenario for item in found.scenarios if item.id == scenario_id)
    assert isinstance(played, InvestigationScenario | VerificationScenario)
    return played


def investigation() -> InvestigationScenario:
    played = scenario_of(INVESTIGATION, INV_01)
    assert isinstance(played, InvestigationScenario)
    return played


def verification(scenario_id: str) -> VerificationScenario:
    played = scenario_of(VERIFICATION, scenario_id)
    assert isinstance(played, VerificationScenario)
    return played


def recording() -> object:
    return recording_of(REPO_ROOT.resolve(), RECORDING)


def scripted_factory(
    answers: Mapping[str, Mapping[str, JsonValue]] | None = None,
) -> Callable[[AgentConfig, ScenarioBase], Model]:
    def factory(config: AgentConfig, played: ScenarioBase) -> Model:
        assert isinstance(played, InvestigationScenario | VerificationScenario)
        return scripted_replay_model(
            played,
            played.recorded(REPO_ROOT),
            answer=(answers or {}).get(played.id),
        )

    return factory


def play(
    suite_ids: list[str],
    *,
    k: int = 2,
    answers: Mapping[str, Mapping[str, JsonValue]] | None = None,
    scenario_ids: list[str] | None = None,
) -> EvalRun:
    return asyncio.run(
        run_eval(
            root=REPO_ROOT,
            suites=load_suites(REPO_ROOT, suite_ids),
            scenario_ids=scenario_ids,
            registry_path=REGISTRY,
            model_factory=scripted_factory(answers),
            options=RunOptions(k=k, concurrency=1, retry_delay_seconds=0),
        )
    )


def only(scenario_id: str, **fields: JsonValue) -> dict[str, dict[str, JsonValue]]:
    """Answers that override `fields` of one scenario's scripted answer."""
    return {scenario_id: dict(fields)}


def failed(run: EvalRun, scenario_id: str) -> set[str]:
    return {
        check.name
        for record in run.report.runs
        if record.envelope.scenario_id == scenario_id
        for check in record.checks
        if not check.passed
    }


# --- the suites -------------------------------------------------------------------------------


def test_the_gold_suites_are_quality_suites_with_the_planned_scenarios() -> None:
    suites = {suite.id: suite for suite in load_suites(REPO_ROOT, [INVESTIGATION, VERIFICATION])}

    assert [item.id for item in suites[INVESTIGATION].scenarios] == [
        INV_01,
        INV_02,
        INV_03,
        INV_04,
        INV_05,
        INV_06,
    ]
    assert [item.id for item in suites[VERIFICATION].scenarios] == [
        VER_01,
        VER_02,
        VER_03,
        VER_04,
        VER_05,
    ]
    assert {suite.kind for suite in suites.values()} == {"quality"}
    assert [suites[INVESTIGATION].agent, suites[VERIFICATION].agent] == [
        "investigation",
        "verification",
    ]


def test_every_gold_scenario_passes_with_a_scripted_model_k_2() -> None:
    result = play([INVESTIGATION, VERIFICATION])
    report = result.report

    assert report.settings.execution_mode == "replay"
    assert {suite.id: (suite.passes, suite.runs) for suite in report.suites} == {
        INVESTIGATION: (12, 12),
        VERIFICATION: (10, 10),
    }
    for record in report.runs:
        assert record.outcome == "pass", (record.envelope.scenario_id, record.checks, record.error)
        assert record.envelope.execution_mode == "replay"
        assert record.metrics.replay_unsupported == 0
        assert record.metrics.unknown_tool_name == 0
        assert record.metrics.denied_calls == 0
        assert record.metrics.tool_calls == 3
        assert record.metrics.ungrounded_evidence == 0
    assert report.passed


def test_a_scenarios_version_covers_the_recording_it_names() -> None:
    found = load_suite(SUITES / INVESTIGATION, root=REPO_ROOT)
    only = found.scenarios[0]
    manifest = (recording_path(REPO_ROOT, RECORDING) / MANIFEST_FILE).read_bytes()
    own = (SUITES / INVESTIGATION / f"{INV_01}.yaml").read_bytes()

    assert only.version == hashlib.sha256(own + manifest).hexdigest()


def test_a_scenario_overlay_adds_and_removes_events_without_changing_the_recording() -> None:
    base = investigation()
    stored = recording_of(REPO_ROOT.resolve(), RECORDING)
    added = stored.events[0].model_copy(
        update={
            "sourceip": "198.51.100.77",
            "destinationip": "198.51.100.77",
            "payload": "synthetic overlay event",
        }
    )
    data = base.model_dump(mode="json")
    data["input"]["overlay"] = {
        "remove_events": [{"qid": 5000849, "username": "svc_backup"}],
        "add_events": [added.model_dump(mode="json")],
    }
    played = InvestigationScenario.model_validate(data)

    layered = played.recorded(REPO_ROOT)

    assert len(stored.events) == 15270
    assert (
        sum(event.qid == 5000849 and event.username == "svc_backup" for event in stored.events) == 3
    )
    assert (
        sum(event.qid == 5000849 and event.username == "svc_backup" for event in layered.events)
        == 0
    )
    assert any(event.sourceip == "198.51.100.77" for event in layered.events)


def test_an_overlay_event_with_a_non_documentation_address_is_rejected() -> None:
    base = investigation()
    stored = recording_of(REPO_ROOT.resolve(), RECORDING)
    added = stored.events[0].model_copy(update={"sourceip": "10.0.0.7"})
    data = base.model_dump(mode="json")
    data["input"]["overlay"] = {"add_events": [added.model_dump(mode="json")]}
    played = InvestigationScenario.model_validate(data)

    with pytest.raises(ValueError, match="outside the documentation ranges"):
        played.recorded(REPO_ROOT)


def test_the_manifests_list_the_gold_suites_and_releases_names_them() -> None:
    for manifest, suite in (("investigation", INVESTIGATION), ("verification", VERIFICATION)):
        data = yaml.safe_load((REPO_ROOT / f"config/agents/{manifest}.yaml").read_text())
        assert suite in data["eval_suites"]

    agents = agents_by_alias(agent_aliases(REPO_ROOT), load_suites(REPO_ROOT))

    assert [user.suites for user in agents["soc-verifier"]] == [(VERIFICATION,)]
    assert INVESTIGATION in next(
        user.suites for user in agents["soc-reasoning"] if user.agent_id == "investigation"
    )
    assert "skill-windows-dcsync" in next(
        user.suites for user in agents["soc-reasoning"] if user.agent_id == "investigation"
    )


# --- each check fails when its fact is wrong ----------------------------------------------------


def test_an_fp_verdict_fails_the_investigation() -> None:
    result = play([INVESTIGATION], answers=only(INV_01, verdict="fp"), scenario_ids=[INV_01])

    assert failed(result, INV_01) == {"verdict_in"}
    assert result.report.suites[0].passes == 0


def test_an_investigation_that_finds_no_event_fails() -> None:
    result = play(
        [INVESTIGATION],
        answers=only(INV_01, urgent_event_candidates=[], claims=[], timeline=[]),
    )

    assert failed(result, INV_01) == {"events_found"}


def test_an_investigation_that_finds_one_of_three_events_fails() -> None:
    played = investigation()
    one: JsonValue = [
        {
            "rank": 1,
            "time": "2026-10-05T14:57:43.905Z",
            "log_source": "Windows",
            "event_name": "Object access",
            "source": played.expect.find_events[0].address,
            "username": "svc_backup",
            "reason": "Replication.",
            "checklist": [],
            "evidence_id": "ev_3",
        }
    ]

    result = play([INVESTIGATION], answers=only(INV_01, urgent_event_candidates=one))

    assert failed(result, INV_01) == set()  # the other two are still in the cited rows
    only_one = play(
        [INVESTIGATION],
        answers=only(INV_01, urgent_event_candidates=one, claims=[], timeline=[]),
    )
    assert failed(only_one, INV_01) == {"events_found"}


def test_an_agreement_with_a_decision_that_has_a_false_claim_fails() -> None:
    result = play(
        [VERIFICATION],
        answers=only(VER_01, agrees=True, disagreements=[]),
        scenario_ids=[VER_01],
    )

    assert failed(result, VER_01) == {"agrees", "disputed_claims"}


def test_contesting_a_claim_that_holds_fails() -> None:
    texts = [claim.text for claim in verification(VER_02).input.claims]
    wrong: JsonValue = [{"claim_text": texts[0], "reason": "No."}]

    result = play(
        [VERIFICATION],
        answers=only(VER_02, agrees=False, disagreements=wrong),
        scenario_ids=[VER_02],
    )

    assert failed(result, VER_02) == {"agrees", "disputed_claims"}


def test_contesting_the_wrong_claim_fails_only_the_claim_check() -> None:
    texts = [claim.text for claim in verification(VER_01).input.claims]
    wrong: JsonValue = [{"claim_text": texts[0], "reason": "No."}]

    result = play([VERIFICATION], answers=only(VER_01, disagreements=wrong), scenario_ids=[VER_01])

    assert failed(result, VER_01) == {"disputed_claims"}


def test_an_event_is_found_by_a_backed_candidate_or_a_cited_row() -> None:
    from ais0c_contracts import UrgentEvent

    expected = ExpectedEvent(address="192.0.2.11", username="svc_backup")
    candidate = UrgentEvent(
        rank=1,
        time=datetime(2026, 10, 5, 14, 57, 43, tzinfo=UTC),
        log_source="x",
        event_name="y",
        source="192.0.2.11",
        username="svc_backup",
        reason="r",
        checklist=[],
        evidence_id="ev_x",
    )
    holds = {"ev_x": [{"sourceip": "192.0.2.11", "username": "svc_backup"}]}
    wrong_user = {"ev_x": [{"sourceip": "192.0.2.11", "username": "someone"}]}

    assert event_found(expected, [candidate], holds, [])
    assert event_found(expected, [], holds, ["ev_x"])
    assert not event_found(expected, [], wrong_user, ["ev_x"])
    assert not event_found(
        expected, [candidate.model_copy(update={"source": "192.0.2.12"})], holds, []
    )


# --- the scenario set's recordings (T-080) -----------------------------------------------------------

NEW_INVESTIGATIONS = (INV_03, INV_04, INV_05, INV_06)


def investigation_of(scenario_id: str) -> InvestigationScenario:
    played = scenario_of(INVESTIGATION, scenario_id)
    assert isinstance(played, InvestigationScenario)
    return played


def test_the_new_gold_scenarios_name_events_their_recordings_hold() -> None:
    for scenario_id in NEW_INVESTIGATIONS:
        played = investigation_of(scenario_id)
        played.check_files(REPO_ROOT)  # raises when the recording, an event or the skill is missing
        stored = recording_of(REPO_ROOT.resolve(), played.input.recording)
        for expected in played.expect.find_events:
            assert any(
                expected.address in (event.sourceip, event.destinationip)
                and (expected.username is None or event.username == expected.username)
                for event in stored.events
            ), (scenario_id, expected)


def test_a_blocked_attack_called_fp_fails() -> None:
    result = play([INVESTIGATION], answers=only(INV_04, verdict="fp"), scenario_ids=[INV_04])

    assert failed(result, INV_04) == {"verdict_in"}


def test_the_approved_scanner_called_tp_fails() -> None:
    result = play([INVESTIGATION], answers=only(INV_05, verdict="tp"), scenario_ids=[INV_05])

    assert failed(result, INV_05) == {"verdict_in"}


def test_agreeing_with_the_wrong_attack_family_fails() -> None:
    result = play(
        [VERIFICATION],
        answers=only(VER_03, agrees=True, disagreements=[]),
        scenario_ids=[VER_03],
    )

    assert failed(result, VER_03) == {"agrees", "disputed_claims"}


def test_agreeing_with_an_invented_logon_fails() -> None:
    result = play(
        [VERIFICATION],
        answers=only(VER_05, agrees=True, disagreements=[]),
        scenario_ids=[VER_05],
    )

    assert failed(result, VER_05) == {"agrees", "disputed_claims"}


def _excerpt_rows(scenario: VerificationScenario, evidence_id: str) -> tuple[str, list[Any], int]:
    ref = next(item for item in scenario.input.evidence if item.evidence_id == evidence_id)
    return ref.query_text, json.loads(ref.excerpt), int(ref.identifiers["rows"])


def _recomputed(query: str, events: list[Any]) -> list[dict[str, Any]]:
    """Run the simple AQL of the verification scenarios over recorded events."""
    import re

    parsed = re.match(
        r"SELECT (.*?) FROM events WHERE (.*?) LIMIT (\d+) START (\d+) STOP (\d+)$", query
    )
    assert parsed, query
    select, rest, limit, start, stop = parsed.groups()
    where, _, group = rest.partition(" GROUP BY ")
    matching = [event for event in events if int(start) <= event.starttime <= int(stop)]  # type: ignore[attr-defined]
    for column, quoted, number in re.findall(r"(\w+) = (?:'([^']*)'|(\d+))", where):
        wanted: object = quoted if quoted else int(number)
        matching = [event for event in matching if getattr(event, column) == wanted]

    def value(event: object, column: str) -> object:
        return getattr(event, "qidname" if column == "QIDNAME(qid)" else column)

    columns = [part.strip() for part in select.split(", ")]
    if group:
        key = group.strip()
        groups: dict[object, list[Any]] = {}
        for event in matching:
            groups.setdefault(getattr(event, key), []).append(event)
        rows: list[dict[str, Any]] = []
        for members in groups.values():
            row: dict[str, Any] = {}
            for column in columns:
                name = column.split(" AS ")[-1]
                row[name] = (
                    len(members)
                    if column.startswith("COUNT(*)")
                    else value(members[0], column.split(" AS ")[0])
                )
            rows.append(row)
        return rows[: int(limit)]
    return [
        {column.split(" AS ")[-1]: value(event, column.split(" AS ")[0]) for column in columns}
        for event in matching
    ][: int(limit)]


def _canonical(rows: list[Any]) -> list[str]:
    return sorted(json.dumps(row, sort_keys=True) for row in rows)


def test_the_verification_excerpts_match_their_recordings() -> None:
    cases = {
        VER_03: ["ev_ver03_xss"],
        VER_04: ["ev_ver04_requests", "ev_ver04_source"],
        VER_05: ["ev_ver05_tickets", "ev_ver05_source"],
    }
    for scenario_id, evidence_ids in cases.items():
        played = verification(scenario_id)
        stored = recording_of(REPO_ROOT.resolve(), played.input.recording)
        for evidence_id in evidence_ids:
            query, excerpt, rows = _excerpt_rows(played, evidence_id)
            computed = _recomputed(query, list(stored.events))  # type: ignore[attr-defined]
            assert computed, evidence_id
            assert _canonical(excerpt) == _canonical(computed), evidence_id
            assert rows == len(excerpt)

    for scenario_id, evidence_id in ((VER_03, "ev_ver03_wrong"), (VER_05, "ev_ver05_wrong")):
        played = verification(scenario_id)
        stored = recording_of(REPO_ROOT.resolve(), played.input.recording)
        query, excerpt, rows = _excerpt_rows(played, evidence_id)
        existing = _canonical(_recomputed(query, list(stored.events)))  # type: ignore[attr-defined]
        assert rows == len(excerpt) == 1
        assert not set(_canonical(excerpt)) & set(existing), evidence_id


# --- the scenario files ---------------------------------------------------------------------------


def suite_with(
    tmp_path: Path, source: str, suite_id: str, change: Callable[[dict[str, Any]], object]
) -> Path:
    """A copy of one gold scenario in a temporary suite, changed by `change(data)`."""
    original = SUITES / suite_id
    directory = tmp_path / suite_id
    directory.mkdir()
    (directory / "suite.yaml").write_text((original / "suite.yaml").read_text(), encoding="utf-8")
    data = yaml.safe_load((original / f"{source}.yaml").read_text(encoding="utf-8"))
    change(data)
    (directory / f"{source}.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    return directory


def rejected(directory: Path) -> str:
    with pytest.raises(SuiteError) as error:
        load_suite(directory, root=REPO_ROOT)
    return str(error.value)


def test_a_scenario_naming_no_recording_is_rejected(tmp_path: Path) -> None:
    directory = suite_with(
        tmp_path, INV_01, INVESTIGATION, lambda data: data["input"].update(recording="lab-99-none")
    )

    assert "recording lab-99-none" in rejected(directory)


def test_a_claim_citing_evidence_the_scenario_lacks_is_rejected(tmp_path: Path) -> None:
    directory = suite_with(
        tmp_path,
        INV_01,
        INVESTIGATION,
        lambda data: data["input"].update(context_evidence=[]),
    )

    assert "cite evidence the scenario does not carry" in rejected(directory)


def test_an_expected_event_the_recording_lacks_is_rejected(tmp_path: Path) -> None:
    directory = suite_with(
        tmp_path,
        INV_01,
        INVESTIGATION,
        lambda data: data["expect"]["find_events"].append({"address": "192.0.2.254"}),
    )

    assert "the recording has no event" in rejected(directory)


def test_a_skill_that_is_not_in_skills_is_rejected(tmp_path: Path) -> None:
    directory = suite_with(
        tmp_path,
        INV_01,
        INVESTIGATION,
        lambda data: data["input"]["skill"].update(id="no-such-skill"),
    )

    assert "no skill no-such-skill" in rejected(directory)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda data: data["expect"].update(disputed_claims=[9]), "names a claim the scenario"),
        (lambda data: data["expect"].update(agrees=True), "agrees is true exactly when"),
        (
            lambda data: data["input"]["claims"][0].update(text=data["input"]["claims"][1]["text"]),
            "same text",
        ),
        (
            lambda data: data["input"]["claims"][0].update(evidence_ids=["ev_missing"]),
            "cite evidence the scenario does not carry",
        ),
        (
            lambda data: data["input"].update(evaluated_at="2020-01-01T00:00:00Z"),
            "before the offense",
        ),
    ],
)
def test_a_verification_scenario_that_contradicts_itself_is_rejected(
    tmp_path: Path,
    change: Callable[[dict[str, Any]], object],
    message: str,
) -> None:
    directory = suite_with(tmp_path, VER_01, VERIFICATION, change)

    assert message in rejected(directory)


def test_a_tool_the_profile_lacks_cannot_be_required(tmp_path: Path) -> None:
    directory = suite_with(
        tmp_path,
        VER_01,
        VERIFICATION,
        lambda data: data["expect"].update(required_tools=["get_offense"]),
    )

    assert "requires get_offense" in rejected(directory)


# --- the task is built as the worker builds it ----------------------------------------------------


def adapter(manifest: str) -> InvestigationAdapter | VerificationAdapter:
    config = load_agent_config(REPO_ROOT, manifest, REGISTRY)
    return (
        InvestigationAdapter(config) if "investigation" in manifest else VerificationAdapter(config)
    )


def test_the_investigation_task_is_the_workers() -> None:
    played = investigation()
    found = adapter("config/agents/investigation.yaml")
    assert isinstance(found, InvestigationAdapter)
    stored = recording_of(REPO_ROOT.resolve(), RECORDING)

    task = found.task(played, run_id="harness-inv-01-dcsync-1")

    assert task.task.agent_id == "investigation"
    assert task.task.objective == played.input.objective
    assert task.task.case_id == "case-30"
    assert task.task.time_window == evaluation_window(
        stored.offense,
        played.evaluated_moment(stored),  # type: ignore[attr-defined]
    )
    assert task.task.budget == found.budget_for(played)
    assert (task.task.budget.tokens, task.task.budget.tool_calls) == (600000, 24)
    assert task.skill is not None
    assert task.skill.ref.skill_id == "windows-dcsync"
    assert [claim.text for claim in task.triage.claims] == [
        claim.text for claim in played.input.triage.claims
    ]
    assert [ref.evidence_id for ref in task.context_evidence] == ["ev_inv01_offense"]
    assert len(task.context_evidence) <= MAX_CONTEXT_EVIDENCE
    assert task.offense == stored.offense  # type: ignore[attr-defined]


def test_the_investigation_prompt_carries_the_plan_steps_objective_and_the_epoch_window() -> None:
    played = investigation()
    found = adapter("config/agents/investigation.yaml")
    assert isinstance(found, InvestigationAdapter)
    recording_ = recording_of(REPO_ROOT.resolve(), RECORDING)
    gateway = ReplayGateway(
        gateway_profile_of(found.config),
        recording_,
        now=played.evaluated_moment(recording_),
        run_id="harness-x-1",
    )
    agent = found.build(gateway, TestModel())
    task = found.task(played, run_id="harness-x-1")

    text = agent.render_instructions(task, nonce="abcdef0123456789", tool_budget=10)

    assert 'source="agent.objective"' in text
    assert played.input.objective in text
    assert 'source="agent.claim"' in text
    assert "Ready AQL time bound" in text
    assert str(int(task.task.time_window.start.timestamp() * 1000)) in text


def test_the_verification_window_is_the_union_of_the_claims_evidence_windows() -> None:
    played = verification(VER_01)
    found = adapter("config/agents/verification.yaml")
    assert isinstance(found, VerificationAdapter)
    stored = recording_of(REPO_ROOT.resolve(), RECORDING)

    task = found.task(played, run_id="harness-ver-01-refutable-ip-1")

    windows = [(ref.time_start, ref.time_end) for ref in played.input.evidence]
    assert task.task.time_window.end == max(end for _, end in windows)
    assert task.task.time_window.start == max(
        stored.offense.start_time,
        min(start for start, _ in windows),  # type: ignore[attr-defined]
    )
    assert [item.critical for item in task.claims] == [True, True, True]
    assert len(task.evidence) == 2
    assert task.reviewed.verdict.value == "tp"


# --- run files (criterion 9) --------------------------------------------------------------------------


def test_an_interrupted_run_keeps_the_runs_it_finished(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The command dies while the third run ends (the disk is full, the operator stops it): the
    first two runs' files are on disk and there is no report."""
    writes: list[str] = []
    real = write_run_file

    def dying(directory: Path, run_file: object) -> None:
        if len(writes) == 2:
            raise OSError("the command dies here")
        real(directory, run_file)  # type: ignore[arg-type]
        writes.append("written")

    monkeypatch.setattr(cli_module, "write_run_file", dying)

    def factory(config: AgentConfig, played: ScenarioBase, env: Mapping[str, str]) -> Model:
        return scripted_factory()(config, played)

    out = tmp_path / "report"
    with pytest.raises(OSError, match="dies here"):
        cli_module.main(
            [
                "--root",
                str(REPO_ROOT),
                "run",
                "--suite",
                VERIFICATION,
                "--k",
                "2",
                "--concurrency",
                "1",
                "--out",
                str(out),
            ],
            environ={"LITELLM_API_KEY": "test-key-not-a-secret"},
            deps=cli_module.Dependencies(model_factory=factory, retry_delay_seconds=0),
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )

    files = sorted(path.relative_to(out).as_posix() for path in out.rglob("*.json"))
    assert files == [f"runs/{VER_01}/1.json", f"runs/{VER_01}/2.json"]
    assert not (out / "report.json").exists()
    record = json.loads((out / "runs" / VER_01 / "1.json").read_text(encoding="utf-8"))["record"]
    assert record["outcome"] == "pass"
    assert record["envelope"]["run_number"] == 1


def test_run_files_arrive_in_the_order_the_runs_end() -> None:
    seen: list[tuple[str, int]] = []

    result = asyncio.run(
        run_eval(
            root=REPO_ROOT,
            suites=load_suites(REPO_ROOT, [VERIFICATION]),
            scenario_ids=None,
            registry_path=REGISTRY,
            model_factory=scripted_factory(),
            options=RunOptions(k=2, concurrency=1, retry_delay_seconds=0),
            on_run=lambda run_file: seen.append(
                (run_file.record.envelope.scenario_id, run_file.record.envelope.run_number)
            ),
        )
    )

    assert seen == [
        (scenario_id, run_number)
        for scenario_id in (VER_01, VER_02, VER_03, VER_04, VER_05)
        for run_number in (1, 2)
    ]
    assert len(result.files) == 10
