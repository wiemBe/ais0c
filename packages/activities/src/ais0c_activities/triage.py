"""The Triage step behind the `triage` activity.

T-010 has no implementation: tests supply scripted ones, and T-012 connects the Triage agent
(packages/agents) through Pydantic AI's `TemporalDurability`.
"""

from typing import Protocol

from ais0c_contracts import EnrichmentContext, OffenseSnapshot, TriageResult


class TriageRunner(Protocol):
    async def triage(
        self,
        *,
        case_id: str,
        evaluation_no: int,
        offense: OffenseSnapshot,
        enrichment: EnrichmentContext,
    ) -> TriageResult:
        """The triage decision for one evaluation of a case."""
        ...
