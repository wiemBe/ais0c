"""Criterion 4: the qradar-verify-read profile gets results without payload and free-text
fields. The test uses a canned response with such fields (gateway_support.EVENT_ROWS)."""

import json

import pytest
from gateway_support import AGENT_RUN, EVENT_ROWS, LOG_INJECTION, Harness, tool_result

from ais0c_contracts import ToolResult, ToolStatus

pytestmark = pytest.mark.anyio

VERIFY = "qradar-verify-read"
INVESTIGATE = "qradar-investigate-read"
QUERY = "SELECT * FROM events WHERE username = 'svc_backup_7731' LIMIT 50 LAST 1 HOURS"
FREE_TEXT_KEYS = {"payload", "UTF8_Payload", "Message"}


async def search_results(harness: Harness, profile: str) -> ToolResult:
    """Create a search, then read its rows: the canned rows of the fake server."""
    run = await harness.start_run(f"run-{profile}", profile=profile)
    async with harness.client() as client:
        created = tool_result(
            await harness.post(
                client,
                harness.intent(profile, "create_ariel_search", {"query_expression": QUERY}),
                run_id=run,
            )
        )
        assert created.status is ToolStatus.OK, created.deny_reason
        search_id = str(created.data[0]["search_id"])
        return tool_result(
            await harness.post(
                client,
                harness.intent(profile, "get_ariel_search_results", {"search_id": search_id}),
                run_id=run,
            )
        )


async def test_the_verify_profile_gets_no_payload_or_free_text(harness: Harness) -> None:
    assert FREE_TEXT_KEYS <= set(EVENT_ROWS[0])  # the canned response has them

    result = await search_results(harness, VERIFY)

    assert result.status is ToolStatus.OK
    assert len(result.data) == len(EVENT_ROWS)
    for row in result.data:
        assert not FREE_TEXT_KEYS & set(row)
        assert {"sourceip", "username", "qid", "starttime"} <= set(row)
    assert LOG_INJECTION not in json.dumps(result.model_dump(mode="json"))


async def test_the_investigate_profile_keeps_them(harness: Harness) -> None:
    result = await search_results(harness, INVESTIGATE)

    assert FREE_TEXT_KEYS <= set(result.data[0])


async def test_the_verify_profile_cannot_select_payload_under_another_name(
    harness: Harness,
) -> None:
    run = await harness.start_run(AGENT_RUN, profile=VERIFY)
    query = "SELECT UTF8(payload) AS note FROM events WHERE username = 'svc' LIMIT 5 LAST 1 HOURS"
    intent = harness.intent(VERIFY, "create_ariel_search", {"query_expression": query})

    async with harness.client() as client:
        result = tool_result(await harness.post(client, intent, run_id=run))

    assert result.status is ToolStatus.DENIED
    assert result.deny_reason == "aql_filtered_field: qradar-verify-read may not query payload"
    assert harness.fake.calls == []


async def test_evidence_excerpts_never_hold_payload_or_credentials(harness: Harness) -> None:
    result = await search_results(harness, INVESTIGATE)

    assert result.evidence_id is not None
    evidence = await harness.evidence(result.evidence_id)
    assert evidence is not None
    assert len(evidence.excerpt) <= 500
    assert "payload" not in evidence.excerpt.lower()
    assert "hunter2-synthetic" not in evidence.excerpt
    assert '"password":"***"' in evidence.excerpt
