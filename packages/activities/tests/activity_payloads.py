"""Synthetic offenses and triage results for the activity tests.

IPs are from the RFC 5737 ranges; user and host names are made up.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from ais0c_contracts import (
    CaseVerdict,
    Confidence,
    Level,
    OffenseSnapshot,
    RunStatus,
    TriageResult,
    Usage,
)

T0 = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)


def offense(
    offense_id: int,
    *,
    start: datetime = T0,
    updated: datetime | None = None,
    rule_ids: Sequence[int] = (100201,),
    offense_source: str = "203.0.113.7",
    source_ips: Sequence[str] = ("203.0.113.7",),
    destination_ips: Sequence[str] = ("198.51.100.15",),
    usernames: Sequence[str] = (),
    log_source_ids: Sequence[int] = (112,),
) -> OffenseSnapshot:
    return OffenseSnapshot(
        offense_id=offense_id,
        description="Excessive Firewall Accepts From Single Source",
        offense_type="Source IP",
        offense_source=offense_source,
        rule_ids=list(rule_ids),
        rule_names=["FW: excessive accepts"],
        categories=["Firewall Permit"],
        magnitude=4,
        start_time=start,
        last_updated_time=start if updated is None else updated,
        event_count=12,
        log_source_ids=list(log_source_ids),
        source_ips=list(source_ips),
        destination_ips=list(destination_ips),
        usernames=list(usernames),
    )


def triage_result(
    ai_level: Level = Level.MEDIUM, verdict: CaseVerdict = CaseVerdict.SUSPICIOUS
) -> TriageResult:
    return TriageResult(
        task_id="triage-task-1",
        status=RunStatus.COMPLETED,
        claims=[],
        data_gaps=[],
        injection_suspected=False,
        usage=Usage(tokens=0, tool_calls=0, seconds=0.0),
        verdict=verdict,
        confidence=Confidence.MEDIUM,
        ai_level=ai_level,
        rationale="Synthetic triage result.",
        needs_investigation=False,
        investigation_focus=[],
    )
