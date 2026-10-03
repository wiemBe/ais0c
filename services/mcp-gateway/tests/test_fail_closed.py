"""Criterion 9: fail closed.

- An MCP server that cannot be reached gives an `error` ToolResult; the gateway does not retry.
- A gateway that cannot be reached makes the client raise a clear error, and the agent run
  ends cleanly as `failed`.

Also: a slow server times out after one attempt, a server error is an `error` result, and
without the database the gateway answers 503 and calls nothing.
"""

import asyncio
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import pytest
from gateway_support import (
    AGENT_RUN,
    TRIAGE_RUN,
    FakeQRadar,
    Harness,
    agent_helpers,
    build_harness,
    free_port,
    new_token,
    tool_result,
)
from sqlalchemy import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ais0c_agents import GatewayUnavailableError, build_triage_agent
from ais0c_agents.gateway_http import HttpGatewayClient
from ais0c_contracts import DataGapReason, RunStatus, ToolResult, ToolStatus
from ais0c_mcp_gateway.registry import Registry
from ais0c_storage import PolicyDecision, create_engine, create_session_factory

pytestmark = pytest.mark.anyio

TRIAGE = "qradar-triage-read"
MakeHarness = Callable[[str], Harness]


@pytest.fixture
def harness_for(
    registry: Registry, sessions: async_sessionmaker[AsyncSession], fake: FakeQRadar
) -> MakeHarness:
    """A harness whose MCP instance is at `url`, with a short call timeout."""

    def make(url: str) -> Harness:
        return build_harness(
            registry=registry, sessions=sessions, fake=fake, upstream_url=url, timeout_seconds=0.5
        )

    return make


async def get_offense(harness: Harness, run_id: str = AGENT_RUN) -> ToolResult:
    intent = harness.intent(TRIAGE, "get_offense", {"offense_id": 1001}, agent_id="triage")
    async with harness.client() as client:
        return tool_result(await harness.post(client, intent, run_id=run_id))


@asynccontextmanager
async def closing_server() -> AsyncIterator[tuple[str, list[int]]]:
    """A TCP server that accepts connections and closes them at once; counts them."""
    accepted: list[int] = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        accepted.append(1)
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        yield f"http://127.0.0.1:{port}", accepted


async def test_an_unreachable_mcp_server_gives_an_error_result(harness_for: MakeHarness) -> None:
    harness = harness_for(f"http://127.0.0.1:{free_port()}")
    await harness.start_run(AGENT_RUN, profile=TRIAGE, agent_id="triage")

    started = time.monotonic()
    result = await get_offense(harness)

    assert time.monotonic() - started < 2
    assert result.status is ToolStatus.ERROR
    assert result.deny_reason == "upstream_unreachable: the MCP server cannot be reached"
    assert result.evidence_id is None
    assert not result.coverage.complete
    [gap] = result.coverage.gaps
    assert (gap.source, gap.reason) == ("qradar", DataGapReason.QUERY_FAILED)
    [row] = await harness.tool_calls(AGENT_RUN)
    assert (row.policy_decision, row.status) == (PolicyDecision.ALLOW, ToolStatus.ERROR)


async def test_a_broken_server_is_tried_once(harness_for: MakeHarness) -> None:
    async with closing_server() as (url, accepted):
        harness = harness_for(url)
        await harness.start_run(AGENT_RUN, profile=TRIAGE, agent_id="triage")

        first = await get_offense(harness)
        second = await get_offense(harness)

    assert first.status is second.status is ToolStatus.ERROR
    assert len(accepted) == 2  # one connection per call: no retries


async def test_a_slow_server_times_out_after_one_attempt(
    harness_for: MakeHarness, fake_server: tuple[FakeQRadar, str]
) -> None:
    harness = harness_for(fake_server[1])
    await harness.start_run(AGENT_RUN, profile=TRIAGE, agent_id="triage")
    harness.fake.delays["get_offense"] = 3

    started = time.monotonic()
    result = await get_offense(harness)

    assert time.monotonic() - started < 2.5
    assert result.status is ToolStatus.ERROR
    assert result.deny_reason == "upstream_timeout: the MCP server did not answer in time"
    assert len(harness.fake.tool_calls("get_offense")) == 1


@pytest.mark.parametrize(
    ("tool_id", "arguments", "response", "reason"),
    [
        (
            "get_offense",
            {"offense_id": 1},
            RuntimeError("QRadar request failed: HTTPStatusError"),
            "upstream_error: QRadar request failed: HTTPStatusError",
        ),
        ("list_rules", {}, {"unexpected": []}, "upstream_result_invalid: no result rows"),
    ],
)
async def test_a_failed_tool_is_an_error_result(
    harness: Harness,
    tool_id: str,
    arguments: dict[str, Any],
    response: dict[str, Any] | Exception,
    reason: str,
) -> None:
    await harness.start_run(AGENT_RUN, profile=TRIAGE, agent_id="triage")
    harness.fake.responses[tool_id] = lambda _: response
    intent = harness.intent(TRIAGE, tool_id, arguments, agent_id="triage")

    async with harness.client() as client:
        result = tool_result(await harness.post(client, intent, run_id=AGENT_RUN))

    assert (result.status, result.deny_reason) == (ToolStatus.ERROR, reason)


async def test_a_search_without_an_id_is_an_error_and_frees_its_slot(harness: Harness) -> None:
    await harness.start_run(AGENT_RUN, profile="qradar-investigate-read")
    harness.fake.responses["create_ariel_search"] = lambda _: {"status": "WAIT"}
    query = "SELECT qid FROM events WHERE username = 'svc' LIMIT 1 LAST 1 HOURS"
    intent = harness.intent(
        "qradar-investigate-read", "create_ariel_search", {"query_expression": query}
    )

    async with harness.client() as client:
        result = tool_result(await harness.post(client, intent, run_id=AGENT_RUN))

    assert (result.status, result.deny_reason) == (
        ToolStatus.ERROR,
        "upstream_result_invalid: no search_id",
    )
    assert harness.gateway.pools[("qradar", "case")].open_searches == 0


async def test_the_server_refusing_the_gateway_token_is_an_error(harness: Harness) -> None:
    await harness.start_run(AGENT_RUN, profile=TRIAGE, agent_id="triage")
    real_token = harness.fake.token
    harness.fake.token = new_token()
    try:
        result = await get_offense(harness)
    finally:
        harness.fake.token = real_token

    assert result.status is ToolStatus.ERROR
    assert result.deny_reason == "upstream_error: the MCP call failed"
    assert harness.fake.calls == []


async def test_without_the_database_nothing_is_called(
    registry: Registry, fake: FakeQRadar, fake_server: tuple[FakeQRadar, str]
) -> None:
    url = URL.create(
        "postgresql+psycopg",
        username="ais0c_app",
        password="not-a-real-password",  # noqa: S106
        host="127.0.0.1",
        port=free_port(),
        database="ais0c",
    )
    engine = create_engine(url)
    try:
        harness = build_harness(
            registry=registry,
            sessions=create_session_factory(engine),
            fake=fake,
            upstream_url=fake_server[1],
        )
        intent = harness.intent(TRIAGE, "get_offense", {"offense_id": 1}, agent_id="triage")
        async with harness.client() as client:
            response = await harness.post(client, intent, run_id=AGENT_RUN)
    finally:
        await engine.dispose()

    assert response.status_code == 503
    assert response.json()["title"] == "gateway.storage_unavailable"
    assert fake.calls == []


# --- the gateway itself is unreachable ------------------------------------------------------


async def test_an_unreachable_gateway_raises_a_clear_error() -> None:
    client = HttpGatewayClient(f"http://127.0.0.1:{free_port()}", "t" * 40)

    with pytest.raises(GatewayUnavailableError, match="cannot be reached"):
        await client.fetch_toolset()


async def test_an_unreachable_gateway_ends_the_agent_run_cleanly() -> None:
    helpers = agent_helpers()
    client = HttpGatewayClient(f"http://127.0.0.1:{free_port()}", "t" * 40)
    script = helpers.ScriptedModel(
        helpers.call("get_offense", offense_id=4711), helpers.answer(helpers.triage_output())
    )
    agent = build_triage_agent(
        manifest=helpers.triage_manifest(),
        prompt=helpers.triage_prompt(),
        profiles=helpers.PROFILES,
        gateway=client,
        model=script.model,
    )

    run = await agent.run(helpers.triage_task(), run_id=TRIAGE_RUN, nonce=helpers.NONCE)

    assert run.status is RunStatus.FAILED
    assert run.result is None
    assert run.error is not None
    assert "GatewayUnavailableError" in run.error
