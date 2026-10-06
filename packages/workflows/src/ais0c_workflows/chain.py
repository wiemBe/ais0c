"""The rules of an evaluation's agent chain that need no I/O (architecture §9; decisions T-42,
T-45 and T-51).

- The decision is the last analysis agent's that gave a result: Investigation's when it has
  one, Triage's otherwise (T-42 (1)).
- The notification level is max(the decision's AI level, the floor) (T-42 (2)).
- The decision's claims are all critical for Verification when the decision is FP or the level
  is high or critical, and none of them otherwise (T-026).
- Reporting gets the claims Verification did not dispute: a disagreement names a claim by its
  exact text (T-51).
- The case's data gaps are the decision's and Verification's.
- An evaluation goes to operator review for each QA reason it has (§9, "Zorunlu operatör
  kontrolü"); the random sample needs the Analysis Catalog and is the recording activity's.

Pure: the results depend only on the arguments.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

from ais0c_contracts import (
    CaseReport,
    CaseVerdict,
    Claim,
    Confidence,
    DataGap,
    InvestigationResult,
    Level,
    QAReason,
    TriageResult,
    UrgentEvent,
    VerificationResult,
)

# Level is a StrEnum whose values compare as strings; this is their order by severity.
LEVEL_ORDER: Final = (Level.LOW, Level.MEDIUM, Level.HIGH, Level.CRITICAL)
CRITICAL_LEVELS: Final = frozenset({Level.HIGH, Level.CRITICAL})


@dataclass(frozen=True, kw_only=True)
class Decision:
    """The verdict of the analysis agent the decision comes from, with what backs it."""

    verdict: CaseVerdict
    confidence: Confidence
    ai_level: Level
    claims: tuple[Claim, ...]
    data_gaps: tuple[DataGap, ...]


@dataclass(frozen=True, kw_only=True)
class ChainDecision:
    """What an evaluation's chain decided, as CaseWorkflow records it."""

    verdict: CaseVerdict
    confidence: Confidence
    ai_level: Level
    notify_level: Level
    report: CaseReport | None
    """None when Reporting gave no result: the decision is recorded without a report."""
    qa_reasons: tuple[QAReason, ...]
    """Without the random sample, which the recording activity decides."""


def decision_of(triage: TriageResult, investigation: InvestigationResult | None) -> Decision:
    """Investigation's decision when it gave a result, Triage's otherwise (T-42 (1))."""
    source = triage if investigation is None else investigation
    return Decision(
        verdict=source.verdict,
        confidence=source.confidence,
        ai_level=source.ai_level,
        claims=tuple(source.claims),
        data_gaps=tuple(source.data_gaps),
    )


def notify_level(ai_level: Level, floor_level: Level | None) -> Level:
    """max(AI level, floor): the AI cannot take a case below its floor (architecture §9)."""
    if floor_level is None:
        return ai_level
    return max(ai_level, floor_level, key=LEVEL_ORDER.index)


def claims_are_critical(verdict: CaseVerdict, level: Level) -> bool:
    """Whether Verification is to treat every claim of a decision as critical."""
    return verdict is CaseVerdict.FP or level in CRITICAL_LEVELS


def undisputed_claims(
    claims: Iterable[Claim], verification: VerificationResult | None
) -> tuple[Claim, ...]:
    """The claims no disagreement names, in order (T-51: the text matches exactly)."""
    disputed = (
        set() if verification is None else {item.claim_text for item in verification.disagreements}
    )
    return tuple(claim for claim in claims if claim.text not in disputed)


def case_data_gaps(
    decision: Decision, verification: VerificationResult | None
) -> tuple[DataGap, ...]:
    """The decision's data gaps and then Verification's, each once."""
    extra = () if verification is None else verification.data_gaps
    unique: list[DataGap] = []
    for item in [*decision.data_gaps, *extra]:
        if item not in unique:
            unique.append(item)
    return tuple(unique)


def evidence_ids(
    claims: Iterable[Claim], candidates: Iterable[UrgentEvent] = ()
) -> tuple[str, ...]:
    """The evidence the claims cite and then the candidates', each once, in order."""
    cited = [evidence_id for claim in claims for evidence_id in claim.evidence_ids]
    return tuple(dict.fromkeys([*cited, *(candidate.evidence_id for candidate in candidates)]))


def verifier_conflict(decision: Decision, verification: VerificationResult | None) -> bool:
    """Whether Verification contested the decision or gave no result (T-42 (3)).

    `agrees` says only that no claim was contested; the verifier's verdict is its own
    assessment (prompts/verification/v1.md). A result that agrees but states another verdict
    contradicts the decision as well (architecture §9: "Verification sonucu ... kararıyla
    çelişiyor").
    """
    return (
        verification is None or not verification.agrees or verification.verdict != decision.verdict
    )


def qa_reasons(
    decision: Decision,
    *,
    verification: VerificationResult | None,
    injection_suspected: bool,
    data_gaps: Sequence[DataGap],
) -> tuple[QAReason, ...]:
    """The evaluation's reasons for operator review, each once, apart from the random sample.

    `injection_suspected` is whether any agent of the chain suspected injection.
    """
    reasons: list[QAReason] = []
    if verifier_conflict(decision, verification):
        reasons.append(QAReason.VERIFIER_CONFLICT)
    if injection_suspected:
        reasons.append(QAReason.INJECTION_SUSPECTED)
    if decision.confidence is Confidence.LOW:
        reasons.append(QAReason.LOW_CONFIDENCE)
    if decision.verdict is CaseVerdict.FP and data_gaps:
        reasons.append(QAReason.FP_WITH_DATA_GAP)
    return tuple(reasons)
