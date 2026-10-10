"""The grounding of a found event and the scoring of Verification's verdict (T-082)."""

from datetime import UTC, datetime

from ais0c_contracts import CaseVerdict, UrgentEvent, VerificationResult
from ais0c_harness.eval import load_suite
from ais0c_harness.eval.investigation import ExpectedEvent, event_found
from ais0c_harness.eval.scripted_replay import search_query, verification_answer
from ais0c_harness.eval.verification import (
    VERDICT_IN,
    VerificationScenario,
    verification_checks,
)
from ais0c_harness.replay.aql import run_query
from ais0c_harness.replay.recording import Recording, recording_of

from .eval_helpers import REPO_ROOT, SUITES

EXPECTED = ExpectedEvent(address="192.0.2.15", username="svc_backup")
HOLDING = {"sourceip": "192.0.2.15", "username": "svc_backup"}
COLUMNS = "starttime, username"
IP_OFFENSES = (
    "lab-45-waf-sqli",
    "lab-46-waf-xss",
    "lab-48-waf-scan-blocked",
    "lab-49-approved-scanner",
    "lab-52-password-spraying",
)


def candidate(evidence_id: str) -> UrgentEvent:
    return UrgentEvent(
        rank=1,
        time=datetime(2026, 10, 5, 14, 57, 43, tzinfo=UTC),
        log_source="x",
        event_name="y",
        source="192.0.2.15",
        username="svc_backup",
        reason="r",
        checklist=[],
        evidence_id=evidence_id,
    )


def test_a_candidate_whose_evidence_lacks_the_event_is_not_found() -> None:
    rows = {"ev_1": [{"sourceip": "192.0.2.99", "username": "other"}]}

    assert not event_found(EXPECTED, [candidate("ev_1")], rows, [])


def test_a_candidate_backed_by_its_rows_is_found() -> None:
    assert event_found(EXPECTED, [candidate("ev_1")], {"ev_1": [HOLDING]}, [])


def test_a_candidate_citing_handed_evidence_is_not_found() -> None:
    assert not event_found(EXPECTED, [candidate("ev_c1")], {"ev_1": [HOLDING]}, [])


def test_a_cited_row_still_finds_the_event() -> None:
    assert event_found(EXPECTED, [], {"ev_1": [HOLDING]}, ["ev_1"])
    assert not event_found(EXPECTED, [], {"ev_1": [HOLDING]}, ["ev_2"])


def recording(name: str) -> Recording:
    return recording_of(REPO_ROOT.resolve(), name)


def test_the_scripted_query_follows_the_offense_type() -> None:
    sqli = search_query(recording("lab-45-waf-sqli"), columns=COLUMNS)
    dcsync = search_query(recording("lab-30-dcsync"), columns=COLUMNS)

    assert "sourceip = '198.51.100.23'" in sqli
    assert "username = 'svc_backup'" in dcsync


def test_the_scripted_search_returns_rows_for_ip_offenses() -> None:
    for name in IP_OFFENSES:
        played = recording(name)
        query = search_query(played, columns=COLUMNS)

        result = run_query(query, played.event_rows, now=played.offense.last_updated_time)

        assert len(result.rows) >= 1, name


def verification_scenario(*, verdict_in: frozenset[CaseVerdict]) -> VerificationScenario:
    found = load_suite(SUITES / "verification-gold", root=REPO_ROOT)
    played = next(item.scenario for item in found.scenarios if item.id == "ver-01-refutable-ip")
    assert isinstance(played, VerificationScenario)
    return played.model_copy(
        update={"expect": played.expect.model_copy(update={"verdict_in": verdict_in})}
    )


def checks_of(scenario: VerificationScenario, verdict: str) -> dict[str, bool]:
    result = VerificationResult.model_validate(
        {
            **verification_answer(scenario),
            "verdict": verdict,
            "task_id": "t",
            "status": "completed",
            "usage": {"tokens": 0, "tool_calls": 0, "seconds": 0},
        }
    )
    return {check.name: check.passed for check in verification_checks(scenario, result)}


def test_verification_verdict_is_scored_when_expected() -> None:
    scenario = verification_scenario(verdict_in=frozenset({CaseVerdict.FP}))

    checks = checks_of(scenario, "tp")

    assert {name for name, passed in checks.items() if not passed} == {VERDICT_IN}
    assert verification_answer(scenario)["verdict"] == "fp"


def test_verification_verdict_is_not_scored_by_default() -> None:
    scenario = verification_scenario(verdict_in=frozenset())

    assert VERDICT_IN not in checks_of(scenario, "tp")
