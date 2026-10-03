"""Semantic ToolIntent checks (T-011 criterion 2)."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ais0c_contracts import ToolIntent
from ais0c_policy import IntentRejectReason, IntentRules, check_intent

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
RULES = IntentRules(max_time_window=timedelta(days=7))


def intent(**changes: object) -> ToolIntent:
    fields: dict[str, object] = {
        "run_id": "case-1001-triage-1",
        "case_id": "case-1001",
        "agent_id": "triage",
        "toolset_profile": "qradar-triage-read",
        "tool_id": "get_offense",
        "tool_schema_version": "v",
        "arguments": {"offense_id": 1001},
        "reason": "Read the offense.",
        "expected_evidence": "The offense record.",
        "time_window": {"start": NOW - timedelta(days=1), "end": NOW},
        "cost_class": "low",
    }
    fields.update(changes)
    return ToolIntent.model_validate(fields)


def window(start: datetime, end: datetime) -> dict[str, datetime]:
    return {"start": start, "end": end}


def test_a_valid_intent_passes() -> None:
    assert check_intent(intent(), RULES, NOW) == ()


def test_a_hunt_intent_passes() -> None:
    hunt = intent(case_id=None, hunt_id="hunt-apt29-2025-10-01-2026-10-01-ab12cd")
    assert check_intent(hunt, RULES, NOW) == ()


def test_a_window_of_exactly_the_profile_limit_passes() -> None:
    assert check_intent(intent(time_window=window(NOW - timedelta(days=7), NOW)), RULES, NOW) == ()


def test_a_window_longer_than_the_profile_limit_is_rejected() -> None:
    long = intent(time_window=window(NOW - timedelta(days=7, seconds=1), NOW))
    assert check_intent(long, RULES, NOW) == (IntentRejectReason.TIME_WINDOW_EXCEEDS_PROFILE,)


@pytest.mark.parametrize("length", [timedelta(0), timedelta(hours=-1)])
def test_an_empty_or_reversed_window_is_rejected(length: timedelta) -> None:
    reversed_window = intent(time_window=window(NOW, NOW + length))
    assert check_intent(reversed_window, RULES, NOW) == (IntentRejectReason.TIME_WINDOW_INVALID,)


def test_a_window_ending_in_the_future_is_rejected() -> None:
    future = intent(time_window=window(NOW, NOW + timedelta(hours=1)))
    assert check_intent(future, RULES, NOW) == (IntentRejectReason.TIME_WINDOW_IN_FUTURE,)


def test_a_small_clock_skew_is_tolerated() -> None:
    skewed = intent(time_window=window(NOW - timedelta(hours=1), NOW + timedelta(minutes=4)))
    assert check_intent(skewed, RULES, NOW) == ()


@pytest.mark.parametrize(
    "changes",
    [
        {"case_id": ""},
        {"case_id": "case 1001"},
        {"case_id": "-case"},
        {"case_id": "case-<untrusted_x>"},
        {"case_id": "c" * 201},
        {"hunt_id": "hunt/../../x"},
    ],
)
def test_a_malformed_case_or_hunt_id_is_rejected(changes: dict[str, Any]) -> None:
    fields: dict[str, Any] = {"hunt_id": "hunt-1"} | changes
    assert IntentRejectReason.CONTEXT_ID_INVALID in check_intent(intent(**fields), RULES, NOW)


@pytest.mark.parametrize("agent_id", ["", "Triage", "triage agent", "t" * 64])
def test_a_malformed_agent_id_is_rejected(agent_id: str) -> None:
    rejected = intent(agent_id=agent_id)
    assert check_intent(rejected, RULES, NOW) == (IntentRejectReason.AGENT_ID_INVALID,)


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("reason", IntentRejectReason.REASON_MISSING),
        ("expected_evidence", IntentRejectReason.EXPECTED_EVIDENCE_MISSING),
    ],
)
def test_a_blank_reason_or_expected_evidence_is_rejected(
    field: str, reason: IntentRejectReason
) -> None:
    assert check_intent(intent(**{field: " \n\t"}), RULES, NOW) == (reason,)


def test_every_reason_is_reported_once() -> None:
    bad = intent(
        case_id="bad id",
        hunt_id="also bad",
        agent_id="?",
        reason="",
        time_window=window(NOW, NOW + timedelta(days=30)),
    )
    assert check_intent(bad, RULES, NOW) == (
        IntentRejectReason.CONTEXT_ID_INVALID,
        IntentRejectReason.AGENT_ID_INVALID,
        IntentRejectReason.REASON_MISSING,
        IntentRejectReason.TIME_WINDOW_IN_FUTURE,
        IntentRejectReason.TIME_WINDOW_EXCEEDS_PROFILE,
    )


def test_an_intent_without_case_and_hunt_never_reaches_the_check() -> None:
    # The contract rejects it already; the gateway answers such a request with an error.
    with pytest.raises(ValueError, match="case_id or hunt_id is required"):
        intent(case_id=None, hunt_id=None)


def test_a_naive_clock_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        check_intent(intent(), RULES, datetime(2026, 10, 3, 12, 0))  # noqa: DTZ001


def test_rules_need_a_positive_window() -> None:
    with pytest.raises(ValueError, match="greater than"):
        IntentRules(max_time_window=timedelta(0))
