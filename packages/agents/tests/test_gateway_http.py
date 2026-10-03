"""The HTTP gateway client (T-011 criteria 9 and 11), against a mock transport.

The client is also tested against the real gateway in services/mcp-gateway/tests/test_client.py.
"""

import asyncio
import json
import secrets

import httpx2
import pytest

from ais0c_agents import (
    GatewayClient,
    GatewayError,
    GatewayUnavailableError,
    TriageAgent,
    build_triage_agent,
)
from ais0c_agents.gateway_http import (
    RUN_ID_HEADER,
    HttpGatewayClient,
    bind_run,
    bound_run,
)
from ais0c_contracts import CostClass, RunStatus, TimeWindow, ToolIntent, ToolResult, ToolStatus

from .helpers import (
    END,
    OFFENSE_EVIDENCE,
    OFFENSE_ROW,
    PROFILES,
    START,
    ScriptedModel,
    answer,
    call,
    denied,
    ok,
    run_triage,
    triage_manifest,
    triage_output,
    triage_prompt,
)

TOKEN = secrets.token_urlsafe(32)
BASE_URL = "http://gateway.test"


def intent() -> ToolIntent:
    return ToolIntent(
        case_id="case-4711",
        agent_id="triage",
        toolset_profile="qradar-triage-read",
        tool_id="get_offense",
        tool_schema_version="1",
        arguments={"offense_id": 4711},
        reason="Read the offense.",
        expected_evidence="The offense record.",
        time_window=TimeWindow(start=START, end=END),
        cost_class=CostClass.LOW,
    )


class Recorder:
    """A mock transport that answers every request the same way and keeps the requests."""

    def __init__(
        self, status: int = 200, body: object = None, *, error: Exception | None = None
    ) -> None:
        self.status = status
        self.body = (
            ok(OFFENSE_EVIDENCE, OFFENSE_ROW).model_dump(mode="json") if body is None else body
        )
        self.error = error
        self.requests: list[httpx2.Request] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        content = self.body if isinstance(self.body, bytes) else json.dumps(self.body).encode()
        return httpx2.Response(self.status, content=content)

    def client(self) -> HttpGatewayClient:
        return HttpGatewayClient(BASE_URL, TOKEN, transport=httpx2.MockTransport(self.handle))


def send(client: HttpGatewayClient, run_id: str | None = "run-4711-triage-1") -> ToolResult:
    async def go() -> ToolResult:
        if run_id is None:
            return await client.call(intent())
        with bind_run(run_id):
            return await client.call(intent())

    return asyncio.run(go())


def test_it_is_a_gateway_client() -> None:
    assert isinstance(Recorder().client(), GatewayClient)


def test_a_call_posts_the_intent_with_the_token_and_the_run() -> None:
    recorder = Recorder()

    result = send(recorder.client())

    [request] = recorder.requests
    assert (request.method, str(request.url)) == ("POST", f"{BASE_URL}/v1/tool-calls")
    assert request.headers["authorization"] == f"Bearer {TOKEN}"
    assert request.headers[RUN_ID_HEADER] == "run-4711-triage-1"
    assert ToolIntent.model_validate_json(request.content) == intent()
    assert result == ok(OFFENSE_EVIDENCE, OFFENSE_ROW)


def test_a_denial_is_a_result_not_an_error() -> None:
    reason = "tool_not_in_profile: create_ariel_search is not a tool of qradar-triage-read"
    recorder = Recorder(body=denied(reason).model_dump(mode="json"))

    result = send(recorder.client())

    assert (result.status, result.deny_reason) == (ToolStatus.DENIED, reason)


def test_a_call_outside_a_run_sends_nothing() -> None:
    recorder = Recorder()

    with pytest.raises(GatewayError, match="no agent run is bound"):
        send(recorder.client(), run_id=None)
    assert recorder.requests == []


def test_bind_run_nests_and_resets() -> None:
    assert bound_run() is None
    with bind_run("run-a"):
        with bind_run("run-b"):
            assert bound_run() == "run-b"
        assert bound_run() == "run-a"
    assert bound_run() is None
    with pytest.raises(ValueError, match="run_id"):
        bind_run("").__enter__()


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (401, {"title": "gateway.unauthorized", "status": 401}, "HTTP 401: gateway.unauthorized"),
        (422, {"title": "gateway.unknown_run", "status": 422}, "HTTP 422: gateway.unknown_run"),
        (503, {"title": "gateway.storage_unavailable"}, "HTTP 503: gateway.storage_unavailable"),
        (500, b"Internal Server Error", "HTTP 500$"),
        (200, {"status": "fine"}, "not a ToolResult"),
        (200, b"<html>proxy page</html>", "not a ToolResult"),
    ],
)
def test_no_tool_result_is_a_gateway_error(status: int, body: object, message: str) -> None:
    with pytest.raises(GatewayError, match=message) as raised:
        send(Recorder(status, body).client())
    assert not isinstance(raised.value, GatewayUnavailableError)


@pytest.mark.parametrize(
    "error", [httpx2.ConnectError("refused"), httpx2.ConnectTimeout("no route")]
)
def test_an_unreachable_gateway_raises_gateway_unavailable(error: Exception) -> None:
    with pytest.raises(GatewayUnavailableError, match="cannot be reached"):
        send(Recorder(error=error).client())


def test_a_gateway_that_stops_answering_is_a_gateway_error() -> None:
    with pytest.raises(GatewayError, match="ReadTimeout"):
        send(Recorder(error=httpx2.ReadTimeout("slow")).client())


def test_the_token_stays_out_of_repr_and_errors() -> None:
    client = Recorder(401, {"title": "gateway.unauthorized"}).client()

    with pytest.raises(GatewayError) as raised:
        send(client)

    assert TOKEN not in repr(client)
    assert TOKEN not in str(raised.value)


@pytest.mark.parametrize(("url", "token"), [("gateway:8080", TOKEN), (BASE_URL, "")])
def test_settings_are_checked(url: str, token: str) -> None:
    with pytest.raises(ValueError, match=r"base_url|token"):
        HttpGatewayClient(url, token)


def test_toolset_comes_from_the_gateway() -> None:
    profile = PROFILES["qradar-triage-read"]
    recorder = Recorder(body=profile.model_dump(mode="json"))

    fetched = asyncio.run(recorder.client().fetch_toolset())

    [request] = recorder.requests
    assert (request.method, str(request.url)) == ("GET", f"{BASE_URL}/v1/tools")
    assert fetched == profile


# --- with the Triage agent ----------------------------------------------------------------------


def build_agent(script: ScriptedModel, client: HttpGatewayClient) -> TriageAgent:
    return build_triage_agent(
        manifest=triage_manifest(),
        prompt=triage_prompt(),
        profiles=PROFILES,
        gateway=client,
        model=script.model,
    )


def test_a_triage_run_completes_through_the_client() -> None:
    recorder = Recorder()
    script = ScriptedModel(
        call("get_offense", offense_id=4711), answer(triage_output(OFFENSE_EVIDENCE))
    )
    agent = build_agent(script, recorder.client())

    with bind_run("run-4711-triage-1"):
        run = run_triage(agent)

    assert run.status is RunStatus.COMPLETED
    assert run.result is not None
    assert run.result.claims[0].evidence_ids == [OFFENSE_EVIDENCE]
    assert len(recorder.requests) == 1


def test_an_unreachable_gateway_ends_the_run_as_failed() -> None:
    recorder = Recorder(error=httpx2.ConnectError("connection refused"))
    script = ScriptedModel(call("get_offense", offense_id=4711), answer(triage_output()))
    agent = build_agent(script, recorder.client())

    with bind_run("run-4711-triage-1"):
        run = run_triage(agent)

    assert run.status is RunStatus.FAILED
    assert run.result is None
    assert run.error is not None
    assert "GatewayUnavailableError" in run.error
