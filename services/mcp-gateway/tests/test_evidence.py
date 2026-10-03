"""Criterion 7: every call is written to tool_calls, every successful query to evidence, and the
evidence_id the agent gets is the key of that evidence row. An empty read of the offense source
is the exception (T-014 criterion 4, D-33)."""

from datetime import timedelta
from typing import Any

import pytest
from gateway_support import AGENT_RUN, LOG_INJECTION, Harness, tool_result
from sqlalchemy import func, select

from ais0c_activities import SOURCE_AGENT_ID
from ais0c_contracts import EvidenceSource, ToolStatus
from ais0c_mcp_gateway.evidence import OFFENSE_SOURCE_AGENT_ID
from ais0c_storage import PolicyDecision
from ais0c_storage.models import EvidenceRow
from ais0c_storage.repositories import find_unknown_evidence_ids

pytestmark = pytest.mark.anyio

INVESTIGATE = "qradar-investigate-read"
TRIAGE = "qradar-triage-read"
QUERY = "SELECT qid FROM events WHERE username = 'svc_backup_7731' LIMIT 10 LAST 2 HOURS"
# The intake's reads: the pseudo agent `offense-source` in the context `offense-intake`, with the
# Triage profile (ais0c_activities.GatewayOffenseSource).
SOURCE_RUN = "run-offense-intake-1"
INTAKE_CONTEXT = "offense-intake"
NO_OFFENSES = {"offenses": [], "count": 0, "total_count": 0}
CHANGED_OFFENSES = {"filter": 'status = "OPEN" and last_updated_time > 1759525200000', "limit": 50}


async def evidence_rows(harness: Harness) -> int:
    async with harness.sessions() as session:
        return await session.scalar(select(func.count()).select_from(EvidenceRow)) or 0


def intake_read(harness: Harness) -> dict[str, Any]:
    return harness.intent(
        TRIAGE,
        "list_offenses",
        CHANGED_OFFENSES,
        agent_id=OFFENSE_SOURCE_AGENT_ID,
        case_id=INTAKE_CONTEXT,
    )


async def test_every_call_is_recorded_and_ok_calls_have_evidence(harness: Harness) -> None:
    run = await harness.start_run(AGENT_RUN, profile=INVESTIGATE)
    harness.fake.responses["list_rules"] = lambda arguments: RuntimeError("QRadar request failed")
    calls = [
        ("get_offense", {"offense_id": 1001}),  # ok
        ("list_reference_sets", {}),  # denied: not in the profile
        ("list_rules", {}),  # error: the server fails
        ("get_rule", {"rule_id": 100234}),  # ok
    ]

    results = []
    async with harness.client() as client:
        for tool_id, arguments in calls:
            response = await harness.post(
                client, harness.intent(INVESTIGATE, tool_id, arguments), run_id=run
            )
            results.append(tool_result(response))

    assert [r.status for r in results] == [
        ToolStatus.OK,
        ToolStatus.DENIED,
        ToolStatus.ERROR,
        ToolStatus.OK,
    ]
    rows = await harness.tool_calls(run)
    assert [row.intent.tool_id for row in rows] == [tool for tool, _ in calls]
    assert [(row.policy_decision, row.status) for row in rows] == [
        (PolicyDecision.ALLOW, ToolStatus.OK),
        (PolicyDecision.DENY, ToolStatus.DENIED),
        (PolicyDecision.ALLOW, ToolStatus.ERROR),
        (PolicyDecision.ALLOW, ToolStatus.OK),
    ]
    for row, result in zip(rows, results, strict=True):
        assert row.evidence_id == result.evidence_id
        assert row.deny_reason == result.deny_reason
        assert row.latency_ms >= 0
    returned = [r.evidence_id for r in results if r.evidence_id is not None]
    assert len(returned) == 2
    async with harness.sessions() as session:
        assert await find_unknown_evidence_ids(session, returned) == set()


async def test_the_evidence_row_says_where_the_data_is(harness: Harness) -> None:
    run = await harness.start_run(AGENT_RUN, profile=INVESTIGATE)
    async with harness.client() as client:
        result = tool_result(
            await harness.post(
                client, harness.intent(INVESTIGATE, "get_offense", {"offense_id": 1001}), run_id=run
            )
        )

    assert result.evidence_id is not None
    assert result.evidence_id.startswith("ev_")
    evidence = await harness.evidence(result.evidence_id)
    assert evidence is not None
    assert evidence.source is EvidenceSource.QRADAR
    assert evidence.query_text == 'get_offense {"offense_id": 1001}'
    assert len(evidence.query_hash) == 64
    assert evidence.identifiers == {"tool": "get_offense", "rows": "1", "offense_id": "1001"}
    assert (evidence.time_start, evidence.time_end) == (
        harness.now - timedelta(hours=1),
        harness.now,
    )
    assert evidence.retrieved_at == harness.now
    assert evidence.expires_at == harness.now + timedelta(days=30)
    assert "Multiple Login Failures" in evidence.excerpt


async def test_search_results_point_back_to_the_query(harness: Harness) -> None:
    run = await harness.start_run(AGENT_RUN, profile=INVESTIGATE)
    async with harness.client() as client:
        created = tool_result(
            await harness.post(
                client,
                harness.intent(INVESTIGATE, "create_ariel_search", {"query_expression": QUERY}),
                run_id=run,
            )
        )
        search_id = str(created.data[0]["search_id"])
        harness.clock[0] += timedelta(minutes=3)
        page = tool_result(
            await harness.post(
                client,
                harness.intent(
                    INVESTIGATE, "get_ariel_search_results", {"search_id": search_id, "limit": 20}
                ),
                run_id=run,
            )
        )

    assert created.evidence_id is not None
    assert page.evidence_id is not None
    create_evidence = await harness.evidence(created.evidence_id)
    page_evidence = await harness.evidence(page.evidence_id)
    assert create_evidence is not None
    assert page_evidence is not None
    assert create_evidence.identifiers["search_id"] == search_id
    assert page_evidence.identifiers == {
        "tool": "get_ariel_search_results",
        "rows": "2",
        "search_id": search_id,
        "limit": "20",
    }
    # The page is evidence of the same query, over the window the query covered.
    assert (page_evidence.query_text, page_evidence.query_hash) == (
        create_evidence.query_text,
        create_evidence.query_hash,
    )
    assert (page_evidence.time_start, page_evidence.time_end) == (
        create_evidence.time_start,
        create_evidence.time_end,
    )
    assert page_evidence.retrieved_at == create_evidence.retrieved_at + timedelta(minutes=3)


async def test_denied_and_failed_calls_leave_no_evidence(harness: Harness) -> None:
    run = await harness.start_run(AGENT_RUN, profile=INVESTIGATE)
    harness.fake.responses["get_offense"] = lambda arguments: RuntimeError("boom")
    async with harness.client() as client:
        failed = tool_result(
            await harness.post(
                client, harness.intent(INVESTIGATE, "get_offense", {"offense_id": 1}), run_id=run
            )
        )
        denied = tool_result(
            await harness.post(
                client,
                harness.intent(
                    INVESTIGATE, "create_ariel_search", {"query_expression": "SELECT 1"}
                ),
                run_id=run,
            )
        )

    assert (failed.evidence_id, denied.evidence_id) == (None, None)
    assert [row.evidence_id for row in await harness.tool_calls(run)] == [None, None]


async def test_an_empty_read_of_the_offense_source_leaves_no_evidence(harness: Harness) -> None:
    """T-014 criterion 4: most intake polls find no changed offense; such a read is recorded as
    a call without evidence. A read that finds offenses has its evidence as before."""
    run = await harness.start_run(
        SOURCE_RUN, profile=TRIAGE, agent_id=OFFENSE_SOURCE_AGENT_ID, case_id=INTAKE_CONTEXT
    )
    harness.fake.responses["list_offenses"] = lambda _: NO_OFFENSES
    async with harness.client() as client:
        empty = tool_result(await harness.post(client, intake_read(harness), run_id=run))
        harness.fake.responses.clear()
        found = tool_result(await harness.post(client, intake_read(harness), run_id=run))

    assert (empty.status, empty.data, empty.evidence_id) == (ToolStatus.OK, [], None)
    assert empty.coverage.complete
    assert found.evidence_id is not None
    rows = await harness.tool_calls(run)
    assert [(row.policy_decision, row.status, row.evidence_id) for row in rows] == [
        (PolicyDecision.ALLOW, ToolStatus.OK, None),
        (PolicyDecision.ALLOW, ToolStatus.OK, found.evidence_id),
    ]
    assert await evidence_rows(harness) == 1


async def test_an_empty_result_of_an_agent_is_evidence(harness: Harness) -> None:
    """For an agent, "searched and found nothing" is evidence too: an empty result keeps it."""
    run = await harness.start_run(AGENT_RUN, profile=INVESTIGATE)
    harness.fake.responses["list_offenses"] = lambda _: NO_OFFENSES
    async with harness.client() as client:
        result = tool_result(
            await harness.post(
                client, harness.intent(INVESTIGATE, "list_offenses", CHANGED_OFFENSES), run_id=run
            )
        )

    assert (result.status, result.data) == (ToolStatus.OK, [])
    assert result.evidence_id is not None
    evidence = await harness.evidence(result.evidence_id)
    assert evidence is not None
    assert (evidence.identifiers["rows"], evidence.excerpt) == ("0", "[]")
    assert [row.evidence_id for row in await harness.tool_calls(run)] == [result.evidence_id]


def test_the_offense_source_is_the_intakes_pseudo_agent() -> None:
    assert OFFENSE_SOURCE_AGENT_ID == SOURCE_AGENT_ID


async def test_excerpts_keep_free_text_out_under_any_name(harness: Harness) -> None:
    # The query renamed the payload, so no field name says it is free text.
    raw = f"<13>Oct 3 23:00:00 DC-LAB-01 Microsoft-Windows-Security-Auditing {LOG_INJECTION}"
    harness.fake.responses["get_ariel_search_results"] = lambda _: {
        "events": [{"raw": raw, "sourceip": "198.51.100.23", "token_hint": "abc"}]
    }
    run = await harness.start_run(AGENT_RUN, profile=INVESTIGATE)
    query = "SELECT UTF8(payload) AS raw, sourceip FROM events WHERE qid = 1 LIMIT 5 LAST 1 HOURS"
    async with harness.client() as client:
        created = tool_result(
            await harness.post(
                client,
                harness.intent(INVESTIGATE, "create_ariel_search", {"query_expression": query}),
                run_id=run,
            )
        )
        page = tool_result(
            await harness.post(
                client,
                harness.intent(
                    INVESTIGATE,
                    "get_ariel_search_results",
                    {"search_id": str(created.data[0]["search_id"])},
                ),
                run_id=run,
            )
        )

    assert page.data[0]["raw"] == raw  # the agent's own profile may read it
    assert page.evidence_id is not None
    evidence = await harness.evidence(page.evidence_id)
    assert evidence is not None
    assert evidence.excerpt == (
        f'[{{"raw":"[{len(raw)} characters]","sourceip":"198.51.100.23","token_hint":"***"}}]'
    )
