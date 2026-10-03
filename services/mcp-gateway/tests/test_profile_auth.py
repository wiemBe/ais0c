"""Criterion 1: every toolset profile has its own token, and a tool outside the token's profile
is denied with a reason.

The caller's profile comes only from its token. The bypass attempts of the T-007 report
(section 2) are negative tests here: no global endpoint, no profile from the path, the body or
a header, and no tool the token's profile lacks.
"""

import pytest
from gateway_support import AGENT_RUN, Harness, auth, new_token, tool_result

from ais0c_contracts import ToolStatus
from ais0c_storage import PolicyDecision

pytestmark = pytest.mark.anyio

QUERY = "SELECT sourceip FROM events WHERE username = 'svc_backup_7731' LIMIT 10 LAST 2 HOURS"


async def test_each_profile_has_its_own_token(harness: Harness) -> None:
    assert len(set(harness.tokens.values())) == len(harness.tokens) == 6
    async with harness.client() as client:
        for profile, token in harness.tokens.items():
            response = await client.get("/v1/tools", headers=auth(token))

            assert response.status_code == 200
            assert response.json()["name"] == profile
            listed = [tool["id"] for tool in response.json()["tools"]]
            assert listed == list(harness.registry.profiles[profile].tools)


async def test_a_tool_outside_the_profile_is_denied_with_a_reason(harness: Harness) -> None:
    run = await harness.start_run(AGENT_RUN, profile="qradar-triage-read", agent_id="triage")
    intent = harness.intent(
        "qradar-triage-read", "create_ariel_search", {"query_expression": QUERY}, agent_id="triage"
    )

    async with harness.client() as client:
        result = tool_result(await harness.post(client, intent, run_id=run))

    assert result.status is ToolStatus.DENIED
    assert result.deny_reason == (
        "tool_not_in_profile: create_ariel_search is not a tool of qradar-triage-read"
    )
    assert result.evidence_id is None
    assert harness.fake.calls == []
    [row] = await harness.tool_calls(run)
    assert (row.policy_decision, row.status, row.deny_reason) == (
        PolicyDecision.DENY,
        ToolStatus.DENIED,
        result.deny_reason,
    )


@pytest.mark.parametrize(
    ("profile", "tool_id", "arguments"),
    [
        ("qradar-verify-read", "get_offense", {"offense_id": 1001}),
        ("qradar-inventory-read", "get_rule", {"rule_id": 100234}),
        ("qradar-hunt-read", "list_assets", {}),
        ("qradar-tuning-read", "list_log_sources", {}),
        ("qradar-triage-read", "list_offense_closing_reasons", {}),
        ("qradar-investigate-read", "add_offense_note", {"offense_id": 1, "note_text": "x"}),
    ],
)
async def test_every_profile_denies_tools_it_does_not_have(
    harness: Harness, profile: str, tool_id: str, arguments: dict[str, object]
) -> None:
    run = await harness.start_run(AGENT_RUN, profile=profile)
    intent = harness.intent(profile, tool_id, arguments)

    async with harness.client() as client:
        result = tool_result(await harness.post(client, intent, run_id=run))

    assert result.status is ToolStatus.DENIED
    assert result.deny_reason is not None
    assert result.deny_reason.startswith("tool_not_in_profile:")
    assert harness.fake.calls == []


async def test_a_token_cannot_borrow_another_profile(harness: Harness) -> None:
    # The triage token, with an intent and a run that claim the investigate profile.
    run = await harness.start_run(AGENT_RUN, profile="qradar-investigate-read")
    intent = harness.intent(
        "qradar-investigate-read", "create_ariel_search", {"query_expression": QUERY}
    )

    async with harness.client() as client:
        response = await harness.post(
            client, intent, run_id=run, token=harness.tokens["qradar-triage-read"]
        )

    result = tool_result(response)
    assert result.status is ToolStatus.DENIED
    assert result.deny_reason is not None
    assert result.deny_reason.startswith(
        "profile_mismatch: the token is for qradar-triage-read, the intent names "
        "qradar-investigate-read"
    )
    assert harness.fake.calls == []


@pytest.mark.parametrize(
    "authorization",
    [
        None,
        "",
        "Bearer",
        "Bearer ",
        "Bearer not-a-token",
        "Basic dXNlcjpwYXNz",
        "{token}x",
        "Bearer {token}x",
        "Bearer {token} {token}",
    ],
)
async def test_without_a_valid_token_nothing_happens(
    harness: Harness, authorization: str | None
) -> None:
    run = await harness.start_run(AGENT_RUN, profile="qradar-triage-read", agent_id="triage")
    intent = harness.intent(
        "qradar-triage-read", "get_offense", {"offense_id": 1001}, agent_id="triage"
    )
    headers = {"X-Ais0c-Run-Id": run}
    if authorization is not None:
        headers["Authorization"] = authorization.format(token=harness.tokens["qradar-triage-read"])

    async with harness.client() as client:
        call = await client.post("/v1/tool-calls", json=intent, headers=headers)
        tools = await client.get("/v1/tools", headers=headers)

    for response in (call, tools):
        assert response.status_code == 401
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["title"] == "gateway.unauthorized"
    assert harness.fake.calls == []
    assert await harness.tool_calls(run) == []


async def test_the_bearer_scheme_is_case_insensitive(harness: Harness) -> None:
    async with harness.client() as client:
        response = await client.get(
            "/v1/tools", headers={"Authorization": f"bearer {harness.tokens['qradar-hunt-read']}"}
        )
    assert response.json()["name"] == "qradar-hunt-read"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/mcp"),
        ("POST", "/rpc"),
        ("POST", "/sse"),
        ("POST", "/servers/qradar-investigate-read/mcp"),
        ("POST", "/v1/tool-calls/qradar-investigate-read"),
        ("POST", "/v1/profiles/qradar-investigate-read/tool-calls"),
        ("GET", "/v1/tools/qradar-investigate-read"),
        ("GET", "/docs"),
        ("GET", "/openapi.json"),
    ],
)
async def test_there_is_no_other_endpoint(harness: Harness, method: str, path: str) -> None:
    token = harness.tokens["qradar-triage-read"]
    async with harness.client() as client:
        response = await client.request(method, path, headers=auth(token), json={})
    assert response.status_code in (404, 405)
    assert harness.fake.calls == []


async def test_the_profile_is_never_taken_from_the_request(harness: Harness) -> None:
    token = harness.tokens["qradar-triage-read"]
    headers = auth(token) | {
        "X-Ais0c-Profile": "qradar-investigate-read",
        "X-Forwarded-For": "203.0.113.7",
        "X-Contextforge-Server-Id": "qradar-investigate-read",
    }
    async with harness.client() as client:
        response = await client.get(
            "/v1/tools", params={"profile": "qradar-investigate-read"}, headers=headers
        )
    assert response.json()["name"] == "qradar-triage-read"
    assert "create_ariel_search" not in {tool["id"] for tool in response.json()["tools"]}


async def test_a_token_in_the_query_string_is_not_accepted(harness: Harness) -> None:
    async with harness.client() as client:
        response = await client.get(
            "/v1/tools", params={"token": harness.tokens["qradar-triage-read"]}
        )
    assert response.status_code == 401


async def test_a_disabled_profile_has_no_token(harness: Harness) -> None:
    # A token that the gateway does not know never maps to a profile, even if it is well formed.
    async with harness.client() as client:
        response = await client.get("/v1/tools", headers=auth(new_token()))
    assert response.status_code == 401
