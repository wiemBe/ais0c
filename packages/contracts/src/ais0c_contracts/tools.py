"""The "Araç çağrıları" models of docs/impl/contracts.md."""

from typing import Self

from pydantic import JsonValue, model_validator

from ais0c_contracts.common import (
    ContractModel,
    DataGap,
    EvidenceId,
    ShortText,
    TimeWindow,
    check_case_or_hunt,
)
from ais0c_contracts.enums import CostClass, ToolStatus


class ToolIntent(ContractModel):
    case_id: str | None = None
    hunt_id: str | None = None
    agent_id: str
    toolset_profile: str
    tool_id: str
    tool_schema_version: str
    # Validated separately against the tool's own schema.
    arguments: dict[str, JsonValue]
    reason: ShortText
    hypothesis_id: str | None = None
    expected_evidence: ShortText
    time_window: TimeWindow
    cost_class: CostClass

    @model_validator(mode="after")
    def _case_or_hunt(self) -> Self:
        check_case_or_hunt(self.case_id, self.hunt_id)
        return self


class ToolCoverage(ContractModel):
    complete: bool
    gaps: list[DataGap]


class ToolResult(ContractModel):
    status: ToolStatus
    # Shown to the agent.
    deny_reason: ShortText | None = None
    evidence_id: EvidenceId | None = None
    # Truncated rows, filtered per toolset profile.
    data: list[dict[str, JsonValue]]
    truncated: bool
    coverage: ToolCoverage
