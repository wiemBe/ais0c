"""The chain rules that need no I/O (T-026 criteria 4, 6 and 7; decisions T-42, T-45, T-51)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from workflow_fakes import NO_USAGE

from ais0c_contracts import (
    CaseVerdict,
    Claim,
    Confidence,
    DataGap,
    DataGapReason,
    Disagreement,
    InvestigationResult,
    Level,
    QAReason,
    RunStatus,
    TriageResult,
    UrgentEvent,
    VerificationResult,
)
from ais0c_workflows.chain import (
    Decision,
    case_data_gaps,
    claims_are_critical,
    decision_of,
    evidence_ids,
    notify_level,
    qa_reasons,
    undisputed_claims,
    verifier_conflict,
)

START = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)
FIRST = Claim(text="svc_backup replicated the directory.", evidence_ids=["ev_1", "ev_2"])
SECOND = Claim(text="198.51.100.15 is not a domain controller.", evidence_ids=["ev_2"])


def gap(hours: int = 0) -> DataGap:
    return DataGap(
        source="Microsoft Windows Security Event Log",
        period_start=START + timedelta(hours=hours),
        period_end=START + timedelta(hours=hours + 1),
        reason=DataGapReason.NO_DATA,
    )


def base(task_id: str) -> dict[str, object]:
    return {
        "task_id": task_id,
        "status": RunStatus.COMPLETED,
        "claims": [FIRST],
        "data_gaps": [gap()],
        "injection_suspected": False,
        "usage": NO_USAGE,
    }


TRIAGE = TriageResult.model_validate(
    base("t")
    | {
        "verdict": "suspicious",
        "confidence": "medium",
        "ai_level": "medium",
        "rationale": "Free text.",
        "needs_investigation": True,
        "investigation_focus": [],
    }
)
INVESTIGATION = InvestigationResult.model_validate(
    base("i")
    | {
        "claims": [SECOND],
        "data_gaps": [],
        "verdict": "tp",
        "confidence": "high",
        "ai_level": "critical",
        "timeline": [],
        "hypotheses": [],
        "urgent_event_candidates": [],
    }
)


def verification(**fields: object) -> VerificationResult:
    return VerificationResult.model_validate(
        base("v")
        | {
            "claims": [],
            "data_gaps": [],
            "agrees": True,
            "verdict": "suspicious",
            "confidence": "medium",
            "disagreements": [],
            "checked_evidence_ids": [],
        }
        | fields
    )


REVIEWED = Decision(
    verdict=CaseVerdict.SUSPICIOUS,
    confidence=Confidence.MEDIUM,
    ai_level=Level.MEDIUM,
    claims=(FIRST,),
    data_gaps=(),
)


def decision(**fields: object) -> Decision:
    return replace(REVIEWED, **fields)


def test_the_decision_is_investigations_when_it_has_one() -> None:
    assert decision_of(TRIAGE, INVESTIGATION) == Decision(
        verdict=CaseVerdict.TP,
        confidence=Confidence.HIGH,
        ai_level=Level.CRITICAL,
        claims=(SECOND,),
        data_gaps=(),
    )
    assert decision_of(TRIAGE, None) == Decision(
        verdict=CaseVerdict.SUSPICIOUS,
        confidence=Confidence.MEDIUM,
        ai_level=Level.MEDIUM,
        claims=(FIRST,),
        data_gaps=(gap(),),
    )


@pytest.mark.parametrize(
    ("ai_level", "floor", "expected"),
    [
        (Level.LOW, None, Level.LOW),
        (Level.LOW, Level.HIGH, Level.HIGH),
        (Level.CRITICAL, Level.HIGH, Level.CRITICAL),
        # As strings "medium" > "high"; by severity it is lower.
        (Level.MEDIUM, Level.HIGH, Level.HIGH),
        (Level.HIGH, Level.MEDIUM, Level.HIGH),
    ],
)
def test_the_notification_level_is_the_higher_by_severity(
    ai_level: Level, floor: Level | None, expected: Level
) -> None:
    assert notify_level(ai_level, floor) is expected


def test_claims_are_critical_for_fp_or_a_high_level() -> None:
    assert claims_are_critical(CaseVerdict.FP, Level.LOW)
    assert claims_are_critical(CaseVerdict.TP, Level.HIGH)
    assert claims_are_critical(CaseVerdict.SUSPICIOUS, Level.CRITICAL)
    assert not claims_are_critical(CaseVerdict.TP, Level.MEDIUM)
    assert not claims_are_critical(CaseVerdict.SUSPICIOUS, Level.LOW)


def test_a_disagreement_removes_only_the_claim_with_its_exact_text() -> None:
    disputed = verification(
        agrees=False,
        disagreements=[
            Disagreement(claim_text=SECOND.text, reason="The host is a domain controller."),
            Disagreement(claim_text=FIRST.text.upper(), reason="Another claim."),
        ],
    )

    assert undisputed_claims([FIRST, SECOND], disputed) == (FIRST,)
    assert undisputed_claims([FIRST, SECOND], None) == (FIRST, SECOND)


def test_evidence_is_listed_once_claims_first() -> None:
    candidate = UrgentEvent(
        rank=1,
        time=START,
        log_source="DC-01",
        event_name="Directory Service Access",
        reason="Replication.",
        checklist=[],
        evidence_id="ev_2",
    )
    other = candidate.model_copy(update={"evidence_id": "ev_9"})

    assert evidence_ids([FIRST, SECOND], [candidate, other]) == ("ev_1", "ev_2", "ev_9")
    assert evidence_ids([]) == ()


def test_the_case_data_gaps_are_the_decisions_and_verifications() -> None:
    gaps = case_data_gaps(decision(data_gaps=(gap(0),)), verification(data_gaps=[gap(0), gap(2)]))

    assert gaps == (gap(0), gap(2))
    assert case_data_gaps(decision(data_gaps=(gap(1),)), None) == (gap(1),)


def test_a_verifier_conflicts_when_it_disagrees_differs_or_gives_nothing() -> None:
    reviewed = decision()

    assert not verifier_conflict(reviewed, verification())
    assert verifier_conflict(reviewed, None)
    assert verifier_conflict(
        reviewed,
        verification(
            agrees=False, disagreements=[Disagreement(claim_text=FIRST.text, reason="No.")]
        ),
    )
    assert verifier_conflict(reviewed, verification(verdict="tp"))


def test_every_qa_reason_of_an_evaluation_is_listed_once() -> None:
    reasons = qa_reasons(
        decision(verdict=CaseVerdict.FP, confidence=Confidence.LOW),
        verification=None,
        injection_suspected=True,
        data_gaps=[gap()],
    )

    assert reasons == (
        QAReason.VERIFIER_CONFLICT,
        QAReason.INJECTION_SUSPECTED,
        QAReason.LOW_CONFIDENCE,
        QAReason.FP_WITH_DATA_GAP,
    )


def test_a_clean_evaluation_has_no_qa_reason() -> None:
    assert (
        qa_reasons(decision(), verification=verification(), injection_suspected=False, data_gaps=[])
        == ()
    )
    # A data gap counts only for an FP decision.
    assert (
        qa_reasons(
            decision(), verification=verification(), injection_suspected=False, data_gaps=[gap()]
        )
        == ()
    )
