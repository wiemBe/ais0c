"""Criterion 3: every call that starts an Ariel search passes the AQL Guard, and a rejected
call returns the guard's reason codes to the agent in `deny_reason`."""

from datetime import timedelta

import pytest
from gateway_support import AGENT_RUN, Harness, tool_result

from ais0c_contracts import ToolStatus
from ais0c_policy import check_aql
from ais0c_storage import PolicyDecision

pytestmark = pytest.mark.anyio

INVESTIGATE = "qradar-investigate-read"
VERIFY = "qradar-verify-read"
ALLOWED = (
    "SELECT sourceip, qid FROM events WHERE username = 'svc_backup_7731' LIMIT 50 LAST 2 HOURS"
)


async def create(
    harness: Harness, profile: str, query: str, *, run_id: str = AGENT_RUN
) -> tuple[ToolStatus, str | None]:
    run = await harness.start_run(run_id, profile=profile)
    intent = harness.intent(profile, "create_ariel_search", {"query_expression": query})
    async with harness.client() as client:
        result = tool_result(await harness.post(client, intent, run_id=run))
    return result.status, result.deny_reason


@pytest.mark.parametrize(
    ("query", "codes"),
    [
        ("SELECT * FROM events", "missing_limit, missing_time_bound"),
        ("SELECT * FROM flows LIMIT 10 LAST 1 HOURS", "table_not_allowed"),
        ("SELECT * FROM events WHERE username = 'LAST 1 HOURS' LIMIT 10", "missing_time_bound"),
        ("SELECT * FROM events WHERE qid = 1 LIMIT 10 LAST 8 DAYS", "window_exceeds_profile"),
        ("SELECT * FROM events WHERE qid = 1 LIMIT 5000 LAST 1 HOURS", "limit_exceeds_profile"),
        (
            'SELECT * FROM events WHERE "Custom Prop" = 1 LIMIT 10 LAST 3 DAYS',
            "wide_window_unindexed_filter",
        ),
        (
            "SELECT * FROM events LIMIT 10 LAST 1 HOURS; DELETE FROM events",
            "multiple_statements, from_invalid",
        ),
        ("SELECT * FROM events LIMIT 10 LAST 1 HOURS -- comment", "comment_not_allowed"),
        (
            "SELECT * FROM events WHERE qid IN (SELECT qid FROM events) LIMIT 10 LAST 1 HOURS",
            "nested_select",
        ),
    ],
)
async def test_a_rejected_query_returns_the_guard_codes(
    harness: Harness, query: str, codes: str
) -> None:
    status, reason = await create(harness, INVESTIGATE, query)

    assert status is ToolStatus.DENIED
    assert reason == f"aql_guard: {codes}"
    assert harness.fake.calls == []
    [row] = await harness.tool_calls(AGENT_RUN)
    assert (row.policy_decision, row.deny_reason) == (PolicyDecision.DENY, reason)


async def test_an_allowed_query_reaches_qradar_unchanged(harness: Harness) -> None:
    status, reason = await create(harness, INVESTIGATE, ALLOWED)

    assert (status, reason) == (ToolStatus.OK, None)
    assert harness.fake.tool_calls("create_ariel_search") == [{"query_expression": ALLOWED}]


async def test_epoch_millisecond_bounds_reach_qradar_unchanged(harness: Harness) -> None:
    query = (
        "SELECT sourceip, qid FROM events WHERE username = 'svc_backup_7731' "
        "LIMIT 50 START 1791057600000 STOP 1791061200000"
    )

    status, reason = await create(harness, INVESTIGATE, query)

    assert (status, reason) == (ToolStatus.OK, None)
    assert harness.fake.tool_calls("create_ariel_search") == [{"query_expression": query}]


async def test_the_guard_applies_the_profile_rules(harness: Harness) -> None:
    # Two hours is within the verify profile's limit; three are not.
    allowed = "SELECT qid FROM events WHERE username = 'svc' LIMIT 10 LAST 2 HOURS"
    assert (await create(harness, VERIFY, allowed))[0] is ToolStatus.OK

    harness.fake.reset()
    query = "SELECT qid FROM events WHERE username = 'svc' LIMIT 10 LAST 3 HOURS"
    status, reason = await create(harness, VERIFY, query, run_id="run-2")
    assert (status, reason) == (ToolStatus.DENIED, "aql_guard: window_exceeds_profile")


async def test_every_profile_that_starts_searches_has_the_guard(harness: Harness) -> None:
    for profile in ("qradar-investigate-read", "qradar-verify-read", "qradar-tuning-read"):
        run = await harness.start_run(f"run-{profile}", profile=profile)
        intent = harness.intent(profile, "create_ariel_search", {"query_expression": "SELECT 1"})
        async with harness.client() as client:
            result = tool_result(await harness.post(client, intent, run_id=run))
        assert result.deny_reason is not None
        assert result.deny_reason.startswith("aql_guard:")
    hunt_run = await harness.start_run(
        "run-hunt", profile="qradar-hunt-read", case_id=None, hunt_id="hunt-1"
    )
    intent = harness.intent(
        "qradar-hunt-read",
        "create_ariel_search",
        {"query_expression": "SELECT 1"},
        case_id=None,
        hunt_id="hunt-1",
    )
    async with harness.client() as client:
        result = tool_result(await harness.post(client, intent, run_id=hunt_run))
    assert result.deny_reason is not None
    assert result.deny_reason.startswith("aql_guard:")
    assert harness.fake.calls == []


async def test_the_query_hash_is_the_guard_hash(harness: Harness) -> None:
    run = await harness.start_run(AGENT_RUN, profile=INVESTIGATE)
    intent = harness.intent(INVESTIGATE, "create_ariel_search", {"query_expression": ALLOWED})
    async with harness.client() as client:
        result = tool_result(await harness.post(client, intent, run_id=run))

    assert result.evidence_id is not None
    evidence = await harness.evidence(result.evidence_id)
    assert evidence is not None
    profile = harness.registry.profiles[INVESTIGATE]
    assert profile.aql is not None
    guard = check_aql(ALLOWED, profile.aql, profile.connector.indexed_fields)
    assert (evidence.query_text, evidence.query_hash) == (ALLOWED, guard.query_hash)
    # LAST 2 HOURS ends at the time the gateway ran the query.
    assert (evidence.time_start, evidence.time_end) == (
        harness.now - timedelta(hours=2),
        harness.now,
    )
