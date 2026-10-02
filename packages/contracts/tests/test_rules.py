"""Cross-field rules: case/hunt scope, hunt window, tuning risk flag, hunt outcome, ranks."""

import pytest
from pydantic import ValidationError

from ais0c_contracts import (
    MAX_HUNT_WINDOW_MONTHS,
    AgentTask,
    CaseReport,
    ContractModel,
    HuntOutcome,
    HuntReport,
    HuntRequest,
    ToolIntent,
    TuningProposal,
    derive_hunt_outcome,
)

from . import payloads
from .payloads import VALID

SCOPED_MODELS = [AgentTask, ToolIntent]


@pytest.mark.parametrize("model", SCOPED_MODELS, ids=lambda model: model.__name__)
@pytest.mark.parametrize(
    "ids",
    [
        {"case_id": None, "hunt_id": None},
        {"case_id": "", "hunt_id": ""},
        {"case_id": "", "hunt_id": None},
    ],
)
def test_case_and_hunt_id_both_empty_is_rejected(
    model: type[ContractModel], ids: dict[str, object]
) -> None:
    with pytest.raises(ValidationError, match="case_id or hunt_id is required"):
        model.model_validate(VALID[model]() | ids)


@pytest.mark.parametrize("model", SCOPED_MODELS, ids=lambda model: model.__name__)
def test_case_and_hunt_id_both_missing_is_rejected(model: type[ContractModel]) -> None:
    payload = VALID[model]()
    del payload["case_id"], payload["hunt_id"]
    with pytest.raises(ValidationError, match="case_id or hunt_id is required"):
        model.model_validate(payload)


@pytest.mark.parametrize("model", SCOPED_MODELS, ids=lambda model: model.__name__)
@pytest.mark.parametrize(
    "ids",
    [
        {"case_id": "case-12345", "hunt_id": None},
        {"case_id": None, "hunt_id": "hunt-1"},
        {"case_id": "case-hunt-hunt-1-1", "hunt_id": "hunt-1"},
    ],
)
def test_case_or_hunt_id_is_enough(model: type[ContractModel], ids: dict[str, object]) -> None:
    model.model_validate(VALID[model]() | ids)


def _hunt_window(start: str, end: str) -> dict[str, object]:
    return payloads.hunt_request() | {"window_start": start, "window_end": end}


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2025-10-01T00:00:00Z", "2026-10-01T00:00:00Z"),  # exactly 12 months
        ("2026-07-01T00:00:00Z", "2026-10-01T00:00:00Z"),  # 3 months
        ("2026-04-01T00:00:00Z", "2026-10-01T00:00:00Z"),  # 6 months
        ("2026-10-01T00:00:00Z", "2026-10-01T00:00:01Z"),  # shortest window
        ("2024-02-29T00:00:00Z", "2025-02-28T00:00:00Z"),  # leap day + 12 months
        ("2025-10-01T03:00:00+03:00", "2026-10-01T03:00:00+03:00"),  # 12 months, not UTC
    ],
)
def test_hunt_window_within_twelve_months_is_accepted(start: str, end: str) -> None:
    HuntRequest.model_validate(_hunt_window(start, end))


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2025-10-01T00:00:00Z", "2026-10-01T00:00:01Z"),
        ("2025-09-30T23:59:59Z", "2026-10-01T00:00:00Z"),
        ("2024-02-29T00:00:00Z", "2025-03-01T00:00:00Z"),
        ("2024-10-01T00:00:00Z", "2026-10-01T00:00:00Z"),
    ],
)
def test_hunt_window_over_twelve_months_is_rejected(start: str, end: str) -> None:
    assert MAX_HUNT_WINDOW_MONTHS == 12
    with pytest.raises(ValidationError, match="must not exceed 12 months"):
        HuntRequest.model_validate(_hunt_window(start, end))


@pytest.mark.parametrize(
    ("start", "end"),
    [
        ("2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z"),
        ("2026-10-01T00:00:00Z", "2026-09-01T00:00:00Z"),
        # Same instant written in two timezones.
        ("2026-10-01T03:00:00+03:00", "2026-10-01T00:00:00Z"),
    ],
)
def test_hunt_window_end_not_after_start_is_rejected(start: str, end: str) -> None:
    with pytest.raises(ValidationError, match="window_end must be after window_start"):
        HuntRequest.model_validate(_hunt_window(start, end))


@pytest.mark.parametrize(("suppressed_tp", "risk_flag"), [(0, False), (1, True), (5, True)])
def test_consistent_risk_flag_is_accepted(suppressed_tp: int, risk_flag: bool) -> None:
    payload = payloads.tuning_proposal() | {"risk_flag": risk_flag}
    payload["backtest"] = payloads.backtest() | {"suppressed_tp_offenses": suppressed_tp}
    TuningProposal.model_validate(payload)


@pytest.mark.parametrize(("suppressed_tp", "risk_flag"), [(0, True), (1, False), (5, False)])
def test_inconsistent_risk_flag_is_rejected(suppressed_tp: int, risk_flag: bool) -> None:
    payload = payloads.tuning_proposal() | {"risk_flag": risk_flag}
    payload["backtest"] = payloads.backtest() | {"suppressed_tp_offenses": suppressed_tp}
    with pytest.raises(ValidationError, match="risk_flag must be true exactly when"):
        TuningProposal.model_validate(payload)


SUP, REF, INC = HuntOutcome.SUPPORTED, HuntOutcome.REFUTED, HuntOutcome.INCONCLUSIVE

# Hypothesis outcomes -> hunt outcome (contracts.md, HuntReport.outcome)
OUTCOME_CASES: list[tuple[list[HuntOutcome], HuntOutcome]] = [
    ([SUP], SUP),
    ([SUP, REF], SUP),
    ([SUP, INC], SUP),
    ([REF, INC, SUP], SUP),
    ([REF], REF),
    ([REF, REF, REF], REF),
    ([INC], INC),
    ([REF, INC], INC),
    ([INC, INC], INC),
    ([], INC),
]


@pytest.mark.parametrize(("hypotheses", "expected"), OUTCOME_CASES)
def test_derive_hunt_outcome(hypotheses: list[HuntOutcome], expected: HuntOutcome) -> None:
    assert derive_hunt_outcome(hypotheses) is expected


def _hunt_report(hypotheses: list[HuntOutcome], outcome: HuntOutcome) -> dict[str, object]:
    return payloads.hunt_report() | {
        "outcome": outcome.value,
        "hypotheses": [payloads.hypothesis_result(h.value) for h in hypotheses],
    }


@pytest.mark.parametrize(("hypotheses", "expected"), OUTCOME_CASES)
def test_hunt_report_with_consistent_outcome_is_accepted(
    hypotheses: list[HuntOutcome], expected: HuntOutcome
) -> None:
    HuntReport.model_validate(_hunt_report(hypotheses, expected))


@pytest.mark.parametrize(("hypotheses", "expected"), OUTCOME_CASES)
def test_hunt_report_with_inconsistent_outcome_is_rejected(
    hypotheses: list[HuntOutcome], expected: HuntOutcome
) -> None:
    for wrong in set(HuntOutcome) - {expected}:
        with pytest.raises(ValidationError, match=f"outcome must be {expected.value}"):
            HuntReport.model_validate(_hunt_report(hypotheses, wrong))


def test_case_report_urgent_events_must_be_sorted_by_rank() -> None:
    payload = payloads.case_report()
    payload["urgent_events"] = [payloads.urgent_event(2), payloads.urgent_event(1)]
    with pytest.raises(ValidationError, match="sorted by rank"):
        CaseReport.model_validate(payload)


def test_urgent_event_rank_starts_at_one() -> None:
    payload = payloads.case_report()
    payload["urgent_events"] = [payloads.urgent_event(0)]
    with pytest.raises(ValidationError, match="greater_than_equal"):
        CaseReport.model_validate(payload)
