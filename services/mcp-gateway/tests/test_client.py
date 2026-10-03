"""Criterion 11: the HTTP client in packages/agents implements T-009's GatewayClient, here
against the real gateway (in process) with the fake MCP server behind it."""

import inspect

import pytest
from gateway_support import AGENT_RUN, TRIAGE_RUN, Harness, new_token, triage_through_gateway

from ais0c_agents import GatewayClient, GatewayError, GatewayUnavailableError, ToolsetProfile
from ais0c_agents.gateway_http import HttpGatewayClient, bind_run
from ais0c_contracts import RunStatus, ToolIntent, ToolStatus
from ais0c_storage import PolicyDecision

pytestmark = pytest.mark.anyio

TRIAGE = "qradar-triage-read"


def intent(harness: Harness, tool_id: str = "get_offense", **arguments: object) -> ToolIntent:
    data = harness.intent(TRIAGE, tool_id, arguments or {"offense_id": 1001}, agent_id="triage")
    return ToolIntent.model_validate(data)


def test_it_implements_the_gateway_client_interface() -> None:
    assert issubclass(HttpGatewayClient, GatewayClient)
    assert not inspect.isabstract(HttpGatewayClient)
    assert inspect.signature(HttpGatewayClient.call) == inspect.signature(GatewayClient.call)


async def test_a_call_returns_the_recorded_result(harness: Harness) -> None:
    run = await harness.start_run(AGENT_RUN, profile=TRIAGE, agent_id="triage")
    client = harness.http_client(TRIAGE)

    with bind_run(run):
        result = await client.call(intent(harness))

    assert result.status is ToolStatus.OK
    assert result.data[0]["id"] == 1001
    [row] = await harness.tool_calls(run)
    assert (row.evidence_id, row.status) == (result.evidence_id, ToolStatus.OK)


async def test_a_denial_comes_back_as_a_result(harness: Harness) -> None:
    run = await harness.start_run(AGENT_RUN, profile=TRIAGE, agent_id="triage")
    client = harness.http_client(TRIAGE)

    with bind_run(run):
        result = await client.call(intent(harness, "list_reference_sets", limit=5))

    assert result.status is ToolStatus.DENIED
    assert result.deny_reason == (
        "tool_not_in_profile: list_reference_sets is not a tool of qradar-triage-read"
    )
    [row] = await harness.tool_calls(run)
    assert row.policy_decision is PolicyDecision.DENY


async def test_the_toolset_is_the_registry_profile(harness: Harness) -> None:
    toolset = await harness.http_client(TRIAGE).fetch_toolset()

    assert toolset == ToolsetProfile.model_validate(harness.registry.profiles[TRIAGE].tool_list())


async def test_a_call_outside_a_run_reaches_nothing(harness: Harness) -> None:
    await harness.start_run(AGENT_RUN, profile=TRIAGE, agent_id="triage")

    with pytest.raises(GatewayError, match="no agent run is bound"):
        await harness.http_client(TRIAGE).call(intent(harness))

    assert harness.fake.calls == []
    assert await harness.tool_calls(AGENT_RUN) == []


@pytest.mark.parametrize(
    ("token", "run_id", "message"),
    [
        (new_token(), AGENT_RUN, "HTTP 401: gateway.unauthorized"),
        (None, "run-nobody-started", "HTTP 422: gateway.unknown_run"),
    ],
)
async def test_no_result_is_a_gateway_error(
    harness: Harness, token: str | None, run_id: str, message: str
) -> None:
    await harness.start_run(AGENT_RUN, profile=TRIAGE, agent_id="triage")
    client = harness.http_client(TRIAGE, token=token)

    with bind_run(run_id), pytest.raises(GatewayError, match=message) as raised:
        await client.call(intent(harness))

    assert not isinstance(raised.value, GatewayUnavailableError)
    assert harness.fake.calls == []


async def test_a_triage_run_goes_through_the_gateway(harness: Harness) -> None:
    triage = await triage_through_gateway(harness)

    assert triage.run.status is RunStatus.COMPLETED, triage.run.error
    assert triage.run.result is not None
    [evidence_id] = triage.evidence_ids
    assert triage.run.result.claims[0].evidence_ids == [evidence_id]
    assert harness.fake.tool_calls("get_offense") == [{"offense_id": 4711}]
    [row] = await harness.tool_calls(TRIAGE_RUN)
    assert (row.status, row.evidence_id) == (ToolStatus.OK, evidence_id)
    assert await harness.evidence(evidence_id) is not None
