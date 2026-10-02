"""The "Hunt" models of docs/impl/contracts.md."""

import calendar
from collections.abc import Iterable
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import StringConstraints, model_validator

from ais0c_contracts.common import Claim, ContractModel, DataGap, Summary, UtcDatetime
from ais0c_contracts.enums import HuntOutcome

MAX_HUNT_WINDOW_MONTHS = 12


def _add_months(value: datetime, months: int) -> datetime:
    """Same day `months` later, clamped to the month's last day (29 Feb + 12 -> 28 Feb)."""
    month_index = value.month - 1 + months
    year, month = value.year + month_index // 12, month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


class HuntScope(ContractModel):
    kind: Literal["all", "asset_group", "user_group"]
    values: list[str]


class HuntRequest(ContractModel):
    # Only `approved` pack versions are accepted; checked by the API, not here.
    pack_id: str
    pack_version: str
    # None or empty means every hypothesis in the pack.
    hypothesis_ids: list[str] | None = None
    window_start: UtcDatetime
    window_end: UtcDatetime
    scope: HuntScope
    trigger: Literal["manual", "schedule"]
    requested_by: str

    @model_validator(mode="after")
    def _window(self) -> Self:
        if self.window_end <= self.window_start:
            raise ValueError("window_end must be after window_start")
        if self.window_end > _add_months(self.window_start, MAX_HUNT_WINDOW_MONTHS):
            raise ValueError(f"hunt window must not exceed {MAX_HUNT_WINDOW_MONTHS} months")
        return self


class HypothesisResult(ContractModel):
    hypothesis_id: str
    outcome: HuntOutcome
    rationale_tr: Summary
    claims: list[Claim]
    data_gaps: list[DataGap]


class CoverageEntry(ContractModel):
    """One month x log source type cell. Comes from the database; never written by an LLM."""

    # Calendar month, `YYYY-MM`.
    month: Annotated[str, StringConstraints(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]
    log_source_type: str
    status: Literal["covered", "no_data", "not_parsed", "not_visible"]


class DetectionProposal(ContractModel):
    title: str
    sigma_yaml: str


class HuntVersions(ContractModel):
    pack: str
    # Keyed by agent ID.
    prompts: dict[str, str]
    models: dict[str, str]


def derive_hunt_outcome(outcomes: Iterable[HuntOutcome]) -> HuntOutcome:
    """Hunt outcome from its hypothesis outcomes.

    `supported` if any hypothesis is supported, `refuted` if all are refuted, otherwise
    `inconclusive`. No hypotheses at all is `inconclusive`.
    """
    collected = list(outcomes)
    if HuntOutcome.SUPPORTED in collected:
        return HuntOutcome.SUPPORTED
    if collected and all(outcome is HuntOutcome.REFUTED for outcome in collected):
        return HuntOutcome.REFUTED
    return HuntOutcome.INCONCLUSIVE


class HuntReport(ContractModel):
    hunt_id: str
    summary_tr: Summary
    outcome: HuntOutcome
    hypotheses: list[HypothesisResult]
    coverage: list[CoverageEntry]
    findings: list[Claim]
    detection_proposals: list[DetectionProposal]
    versions: HuntVersions

    @model_validator(mode="after")
    def _outcome_matches_hypotheses(self) -> Self:
        expected = derive_hunt_outcome(h.outcome for h in self.hypotheses)
        if self.outcome is not expected:
            raise ValueError(f"outcome must be {expected.value} for these hypothesis outcomes")
        return self
