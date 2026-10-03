"""Semantic checks of a ToolIntent before the gateway runs it (architecture §13.2, §13.3).

The contract model already checks the structure: types, lengths and that `case_id` or
`hunt_id` is set. These checks add what the structure cannot say: identifiers are usable,
the reason and expected evidence are not blank, and the time window is ordered, not in the
future and within the gateway profile's limit.
"""

import re
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from ais0c_contracts import ToolIntent

# Workflow-derived IDs (`case-12345`, `case-hunt-<hunt_id>-<n>`, `group-<id>`, `hunt-...`).
_CONTEXT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}")
# Agent manifest IDs (config/agents/<agent>.yaml).
_AGENT_ID = re.compile(r"[a-z][a-z0-9-]{0,62}")

DEFAULT_MAX_CLOCK_SKEW = timedelta(minutes=5)


class IntentRejectReason(StrEnum):
    """Why a ToolIntent was rejected. Returned to the agent as structured data."""

    CONTEXT_ID_INVALID = "context_id_invalid"
    AGENT_ID_INVALID = "agent_id_invalid"
    REASON_MISSING = "reason_missing"
    EXPECTED_EVIDENCE_MISSING = "expected_evidence_missing"
    TIME_WINDOW_INVALID = "time_window_invalid"
    TIME_WINDOW_IN_FUTURE = "time_window_in_future"
    TIME_WINDOW_EXCEEDS_PROFILE = "time_window_exceeds_profile"


_PositiveTimedelta = Annotated[timedelta, Field(gt=timedelta(0))]


class IntentRules(BaseModel):
    """The ToolIntent limits of one gateway profile."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Longest `time_window` an intent of this profile may declare.
    max_time_window: _PositiveTimedelta
    # How far `time_window.end` may lie ahead of the gateway's clock.
    max_clock_skew: Annotated[timedelta, Field(ge=timedelta(0))] = DEFAULT_MAX_CLOCK_SKEW


def check_intent(
    intent: ToolIntent, rules: IntentRules, now: datetime
) -> tuple[IntentRejectReason, ...]:
    """Return every reason to reject `intent`; empty when it may run. `now` is timezone-aware."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    reasons: list[IntentRejectReason] = []
    for context_id in (intent.case_id, intent.hunt_id):
        if context_id is not None and not _CONTEXT_ID.fullmatch(context_id):
            reasons.append(IntentRejectReason.CONTEXT_ID_INVALID)
    if not _AGENT_ID.fullmatch(intent.agent_id):
        reasons.append(IntentRejectReason.AGENT_ID_INVALID)
    if not intent.reason.strip():
        reasons.append(IntentRejectReason.REASON_MISSING)
    if not intent.expected_evidence.strip():
        reasons.append(IntentRejectReason.EXPECTED_EVIDENCE_MISSING)

    window = intent.time_window
    if window.end <= window.start:
        reasons.append(IntentRejectReason.TIME_WINDOW_INVALID)
    else:
        if window.end > now + rules.max_clock_skew:
            reasons.append(IntentRejectReason.TIME_WINDOW_IN_FUTURE)
        if window.end - window.start > rules.max_time_window:
            reasons.append(IntentRejectReason.TIME_WINDOW_EXCEEDS_PROFILE)
    return tuple(dict.fromkeys(reasons))
