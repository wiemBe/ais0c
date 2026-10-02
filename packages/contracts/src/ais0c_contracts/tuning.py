"""The "Tuning ve geri bildirim" models of docs/impl/contracts.md."""

from typing import Annotated, Self

from pydantic import StringConstraints, model_validator

from ais0c_contracts.common import ContractModel, ShortText, Summary
from ais0c_contracts.enums import CaseVerdict, FeedbackReason, TuningChange


class Backtest(ContractModel):
    days: int
    suppressed_offenses: int
    suppressed_tp_offenses: int


class TuningProposal(ContractModel):
    cluster_id: str
    rule_id: int
    change_type: TuningChange
    description_tr: Summary
    rationale: ShortText
    backtest: Backtest
    # True exactly when the backtest suppresses a true positive.
    risk_flag: bool

    @model_validator(mode="after")
    def _risk_flag_matches_backtest(self) -> Self:
        if self.risk_flag != (self.backtest.suppressed_tp_offenses > 0):
            raise ValueError("risk_flag must be true exactly when suppressed_tp_offenses > 0")
        return self


class OperatorFeedback(ContractModel):
    case_id: str
    # The operator's verdict.
    verdict: CaseVerdict
    reason: FeedbackReason
    comment: Annotated[str, StringConstraints(max_length=500)] | None = None
