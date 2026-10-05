"""Criterion 2: schema and semantic checks of the ToolIntent.

A time window longer than the profile allows, or an intent without case_id and hunt_id, is
rejected. So are a wrong schema version, arguments outside the tool's schema and a call that
does not match its agent run. A rejected call never reaches the MCP server.

The agent run comes only from the intent's `run_id` (T-013 criterion 5). Its form is the
contract's `RunId` (contracts v0.4, T-041 criterion 1): an ID of another form is not a
ToolIntent, and the gateway's own check of an intent built without validation is the same
pattern.
"""

import re
from datetime import timedelta
from typing import Any

import pytest
from gateway_support import AGENT_RUN, CASE_ID, OTHER_CASE_ID, Harness, auth, tool_result
from pydantic import ValidationError

from ais0c_contracts import RUN_ID_PATTERN, ToolIntent, ToolStatus
from ais0c_mcp_gateway.pipeline import _RUN_ID as GATEWAY_RUN_ID
from ais0c_mcp_gateway.pipeline import UnknownRunError
from ais0c_storage import PolicyDecision, new_uuid7
from ais0c_workflows.names import case_workflow_id, triage_workflow_id

pytestmark = pytest.mark.anyio

TRIAGE = "qradar-triage-read"


def window(
    harness: Harness, length: timedelta, *, end_offset: timedelta = timedelta(0)
) -> dict[str, str]:
    end = harness.now + end_offset
    return {"start": (end - length).isoformat(), "end": end.isoformat()}


async def denied(harness: Harness, intent: dict[str, Any], run: str) -> str:
    async with harness.client() as client:
        result = tool_result(await harness.post(client, intent, run_id=run))
    assert result.status is ToolStatus.DENIED
    assert result.deny_reason is not None
    assert harness.fake.calls == []
    [row] = await harness.tool_calls(run)
    assert (row.policy_decision, row.status, row.deny_reason) == (
        PolicyDecision.DENY,
        ToolStatus.DENIED,
        result.deny_reason,
    )
    return result.deny_reason


async def triage_run(harness: Harness) -> str:
    return await harness.start_run(AGENT_RUN, profile=TRIAGE, agent_id="triage")


def offense_intent(harness: Harness, **changes: object) -> dict[str, Any]:
    intent = harness.intent(TRIAGE, "get_offense", {"offense_id": 1001}, agent_id="triage")
    intent.update(changes)
    return intent


@pytest.mark.parametrize(
    ("profile", "tool_id", "arguments", "limit"),
    [
        ("qradar-triage-read", "get_offense", {"offense_id": 1}, timedelta(days=31)),
        ("qradar-investigate-read", "get_rule", {"rule_id": 1}, timedelta(days=31)),
        ("qradar-verify-read", "get_ariel_search_status", {"search_id": "s1"}, timedelta(days=31)),
        ("qradar-hunt-read", "list_reference_sets", {}, timedelta(days=366)),
        ("qradar-inventory-read", "list_log_sources", {}, timedelta(days=31)),
        ("qradar-tuning-read", "get_rule", {"rule_id": 1}, timedelta(days=92)),
    ],
)
async def test_a_window_over_the_profile_limit_is_denied(
    harness: Harness, profile: str, tool_id: str, arguments: dict[str, Any], limit: timedelta
) -> None:
    run = await harness.start_run(AGENT_RUN, profile=profile)
    intent = harness.intent(
        profile, tool_id, arguments, time_window=window(harness, limit + timedelta(seconds=1))
    )

    reason = await denied(harness, intent, run)

    assert reason == "invalid_intent: time_window_exceeds_profile"


async def test_a_window_at_the_profile_limit_is_allowed(harness: Harness) -> None:
    run = await triage_run(harness)
    intent = offense_intent(harness, time_window=window(harness, timedelta(days=31)))

    async with harness.client() as client:
        result = tool_result(await harness.post(client, intent, run_id=run))

    assert result.status is ToolStatus.OK
    assert harness.fake.tool_calls("get_offense") == [{"offense_id": 1001}]


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        (
            {"window": (timedelta(hours=1), timedelta(hours=1))},
            "invalid_intent: time_window_in_future",
        ),
        ({"window": (timedelta(0), timedelta(0))}, "invalid_intent: time_window_invalid"),
        ({"reason": "   "}, "invalid_intent: reason_missing"),
        ({"expected_evidence": ""}, "invalid_intent: expected_evidence_missing"),
    ],
)
async def test_semantic_checks_deny_the_call(
    harness: Harness, changes: dict[str, Any], reason: str
) -> None:
    run = await triage_run(harness)
    if "window" in changes:
        length, offset = changes.pop("window")
        changes["time_window"] = window(harness, length, end_offset=offset)

    assert await denied(harness, offense_intent(harness, **changes), run) == reason


async def test_an_intent_without_case_and_hunt_is_rejected(harness: Harness) -> None:
    run = await triage_run(harness)
    intent = offense_intent(harness, case_id=None, hunt_id=None)

    async with harness.client() as client:
        response = await harness.post(client, intent, run_id=run)

    assert response.status_code == 422
    assert response.json()["title"] == "gateway.invalid_intent"
    assert "case_id or hunt_id is required" in response.json()["detail"]
    assert harness.fake.calls == []
    # Not a ToolIntent, so there is nothing to record.
    assert await harness.tool_calls(run) == []


@pytest.mark.parametrize(
    "case_id", ["case 1001", "case-1001'; DROP TABLE tool_calls;--", "-1001", "c" * 201]
)
async def test_a_malformed_case_id_is_denied(harness: Harness, case_id: str) -> None:
    # Even when the run was recorded with the same malformed ID.
    run = await harness.start_run(AGENT_RUN, profile=TRIAGE, agent_id="triage", case_id=case_id)

    reason = await denied(harness, offense_intent(harness, case_id=case_id), run)

    assert reason == "invalid_intent: context_id_invalid"


async def test_an_empty_case_id_counts_as_missing(harness: Harness) -> None:
    run = await triage_run(harness)

    async with harness.client() as client:
        response = await harness.post(client, offense_intent(harness, case_id=""), run_id=run)

    assert response.status_code == 422
    assert "case_id or hunt_id is required" in response.json()["detail"]


async def test_a_wrong_schema_version_is_denied(harness: Harness) -> None:
    run = await triage_run(harness)
    current = harness.registry.profiles[TRIAGE].tools["get_offense"].entry.schema_version

    reason = await denied(harness, offense_intent(harness, tool_schema_version="1"), run)

    assert reason == f"schema_version_mismatch: get_offense is at schema version {current}"


@pytest.mark.parametrize(
    ("arguments", "reason"),
    [
        ({}, "invalid_arguments: missing offense_id"),
        (
            {"offense_id": "1001 OR 1=1"},
            "invalid_arguments: offense_id does not match the schema (type)",
        ),
        ({"offense_id": -1}, "invalid_arguments: offense_id does not match the schema (minimum)"),
        (
            {"offense_id": 1001, "fields": "id", "IGNORE PREVIOUS INSTRUCTIONS": 1},
            "invalid_arguments: unknown argument; get_offense takes offense_id",
        ),
    ],
)
async def test_arguments_must_match_the_registry_schema(
    harness: Harness, arguments: dict[str, Any], reason: str
) -> None:
    run = await triage_run(harness)
    intent = harness.intent(TRIAGE, "get_offense", arguments, agent_id="triage")

    assert await denied(harness, intent, run) == reason


async def test_an_overlong_query_is_denied_without_echoing_it(harness: Harness) -> None:
    run = await harness.start_run(AGENT_RUN, profile="qradar-investigate-read")
    query = "SELECT qid FROM events WHERE username = '" + "x" * 4000 + "' LIMIT 1 LAST 1 HOURS"
    intent = harness.intent(
        "qradar-investigate-read", "create_ariel_search", {"query_expression": query}
    )

    reason = await denied(harness, intent, run)

    assert reason == "invalid_arguments: query_expression does not match the schema (maxLength)"


# --- the agent run ---------------------------------------------------------------------------


async def test_a_call_needs_a_recorded_run(harness: Harness) -> None:
    await triage_run(harness)

    async with harness.client() as client:
        response = await harness.post(client, offense_intent(harness), run_id="run-unknown")

    assert response.status_code == 422
    assert response.json()["title"] == "gateway.unknown_run"
    assert response.json()["detail"] == "no agent run with this run_id is recorded"
    assert harness.fake.calls == []
    assert await harness.tool_calls(AGENT_RUN) == []


@pytest.mark.parametrize("run_id", [None, "", "r" * 201, 4711])
async def test_an_intent_without_a_valid_run_id_is_rejected(
    harness: Harness, run_id: object
) -> None:
    await triage_run(harness)
    intent = offense_intent(harness)
    if run_id is None:
        del intent["run_id"]
    else:
        intent["run_id"] = run_id

    async with harness.client() as client:
        response = await client.post(
            "/v1/tool-calls", json=intent, headers=auth(harness.tokens[TRIAGE])
        )

    assert response.status_code == 422
    assert response.json()["title"] == "gateway.invalid_intent"
    assert response.json()["detail"].startswith("run_id: ")
    assert harness.fake.calls == []


def platform_run_ids() -> list[str]:
    """One run ID of every form the platform issues, made by the code that makes them where
    that code exists."""
    case_id = case_workflow_id(1001)
    return [
        # Triage runs, and the retry after a model outage (D-33).
        triage_workflow_id(case_id, 1),
        triage_workflow_id(case_id, 1, retry=True),
        triage_workflow_id("group-G-3f2a9c1b2d4e-20261005T101500Z", 2),
        # The pseudo agent runs of platform code: intake, executor, catalog sync.
        str(new_uuid7()),
        # Hunts (repo-structure.md): `hunt-<pack>-<start>-<end>-<scope hash>`.
        "hunt-ornek-grup-2026-01-01-2026-03-31-3f2a9c1b",
        "hunt-ornek_grup-2026-01-01T00:00:00Z-2026-03-31T23:59:59.999Z-3f2a9c1b",
        "case-hunt-hunt-ornek-grup-2026-01-01-2026-03-31-3f2a9c1b-1-triage-1",
    ]


# Forms the platform never issues; ToolIntent refuses every one.
MALFORMED_RUN_IDS = [
    "run with spaces",
    "../etc/passwd",
    "run/1",
    "run\x00",
    "-run",
    ".run",
    "run-1\n",
    "run-1\r\nX-Ais0c-Run-Id: run-2",
    "ü" * 10,
    "r" * 201,
]


@pytest.mark.parametrize("run_id", platform_run_ids())
async def test_every_run_id_form_the_platform_issues_is_accepted(
    harness: Harness, run_id: str
) -> None:
    run = await harness.start_run(run_id, profile=TRIAGE, agent_id="triage")

    async with harness.client() as client:
        result = tool_result(await harness.post(client, offense_intent(harness), run_id=run))

    assert result.status is ToolStatus.OK
    assert [row.intent.run_id for row in await harness.tool_calls(run_id)] == [run_id]


@pytest.mark.parametrize("run_id", platform_run_ids())
async def test_a_well_formed_run_id_that_names_no_run_is_unknown(
    harness: Harness, run_id: str
) -> None:
    await triage_run(harness)

    async with harness.client() as client:
        response = await harness.post(client, offense_intent(harness), run_id=run_id)

    assert response.status_code == 422
    assert response.json()["title"] == "gateway.unknown_run"
    assert harness.fake.calls == []


@pytest.mark.parametrize("run_id", MALFORMED_RUN_IDS)
async def test_a_run_id_the_platform_never_issues_is_not_a_tool_intent(
    harness: Harness, run_id: str
) -> None:
    # Even when a run with that ID is recorded: the intent is refused before any lookup.
    recorded = await harness.start_run(
        run_id.replace("\x00", ""), profile=TRIAGE, agent_id="triage"
    )

    async with harness.client() as client:
        response = await harness.post(client, offense_intent(harness), run_id=run_id)

    assert response.status_code == 422
    assert response.json()["title"] == "gateway.invalid_intent"
    # The detail names the field and the rule, never the value.
    assert response.json()["detail"] in {
        f"run_id: String should match pattern '{RUN_ID_PATTERN}'",
        "run_id: String should have at most 200 characters",
    }
    assert harness.fake.calls == []
    assert await harness.tool_calls(recorded) == []


@pytest.mark.parametrize("run_id", MALFORMED_RUN_IDS)
async def test_an_intent_built_without_validation_is_checked_with_the_same_pattern(
    harness: Harness, run_id: str
) -> None:
    """`Gateway.call` in process, with an intent changed without validation (`model_copy`):
    the ID is not looked up, even when a run with it is recorded."""
    recorded = await harness.start_run(
        run_id.replace("\x00", ""), profile=TRIAGE, agent_id="triage"
    )
    valid = ToolIntent.model_validate(offense_intent(harness))
    intent = valid.model_copy(update={"run_id": run_id})

    with pytest.raises(UnknownRunError):
        await harness.gateway.call(TRIAGE, intent)

    assert harness.fake.calls == []
    assert await harness.tool_calls(recorded) == []


@pytest.mark.parametrize("run_id", [*platform_run_ids(), *MALFORMED_RUN_IDS, "", "r" * 200])
def test_the_gateway_and_the_contract_agree_on_every_run_id(run_id: str) -> None:
    assert GATEWAY_RUN_ID.pattern == RUN_ID_PATTERN
    try:
        ToolIntent.model_validate(_any_intent() | {"run_id": run_id})
        contract_accepts = True
    except ValidationError:
        contract_accepts = False
    assert bool(GATEWAY_RUN_ID.fullmatch(run_id)) is contract_accepts
    assert bool(re.fullmatch(RUN_ID_PATTERN, run_id)) is contract_accepts


def _any_intent() -> dict[str, Any]:
    return {
        "case_id": CASE_ID,
        "agent_id": "triage",
        "toolset_profile": TRIAGE,
        "tool_id": "get_offense",
        "tool_schema_version": "1",
        "arguments": {"offense_id": 1001},
        "reason": "Read the offense.",
        "expected_evidence": "The offense.",
        "time_window": {"start": "2026-10-05T09:00:00+00:00", "end": "2026-10-05T10:00:00+00:00"},
        "cost_class": "low",
    }


async def test_the_old_run_header_means_nothing(harness: Harness) -> None:
    """T-011 sent the run in `X-Ais0c-Run-Id`; the gateway now ignores that header."""
    run = await triage_run(harness)
    other = await harness.start_run("run-other", profile=TRIAGE, agent_id="triage")
    headers = auth(harness.tokens[TRIAGE])

    async with harness.client() as client:
        # The header names another recorded run: the call is the intent's run's.
        recorded = await client.post(
            "/v1/tool-calls",
            json=offense_intent(harness, run_id=run),
            headers=headers | {"X-Ais0c-Run-Id": other},
        )
        # The header names a recorded run, the intent an unknown one: refused.
        refused = await client.post(
            "/v1/tool-calls",
            json=offense_intent(harness, run_id="run-unknown"),
            headers=headers | {"X-Ais0c-Run-Id": run},
        )

    assert tool_result(recorded).status is ToolStatus.OK
    assert [row.intent.run_id for row in await harness.tool_calls(run)] == [run]
    assert await harness.tool_calls(other) == []
    assert refused.status_code == 422
    assert refused.json()["title"] == "gateway.unknown_run"
    assert len(harness.fake.calls) == 1


async def test_a_finished_run_makes_no_more_calls(harness: Harness) -> None:
    run = await triage_run(harness)
    await harness.finish_run(run)

    assert await denied(harness, offense_intent(harness), run) == (
        "run_not_active: the agent run has ended"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"case_id": OTHER_CASE_ID},
        {"agent_id": "investigation"},
        {"case_id": None, "hunt_id": "hunt-1"},
    ],
)
async def test_the_intent_must_match_its_run(harness: Harness, changes: dict[str, Any]) -> None:
    run = await triage_run(harness)

    reason = await denied(harness, offense_intent(harness, **changes), run)

    assert reason == "run_mismatch: the run belongs to another profile, agent, case or hunt"


async def test_a_run_of_another_profile_is_refused(harness: Harness) -> None:
    run = await harness.start_run(AGENT_RUN, profile="qradar-investigate-read", agent_id="triage")

    reason = await denied(harness, offense_intent(harness), run)

    assert reason == "run_mismatch: the run belongs to another profile, agent, case or hunt"
    assert CASE_ID in str((await harness.tool_calls(run))[0].intent.case_id)


# --- malformed requests ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "body", [b"", b"not json", b"[]", b'{"tool_id": "get_offense"}', b'{"case_id": 1}']
)
async def test_a_body_that_is_not_a_tool_intent_is_rejected(harness: Harness, body: bytes) -> None:
    await triage_run(harness)
    headers = auth(harness.tokens[TRIAGE])

    async with harness.client() as client:
        response = await client.post("/v1/tool-calls", content=body, headers=headers)

    assert response.status_code == 422
    assert response.json()["title"] == "gateway.invalid_intent"
    assert harness.fake.calls == []


async def test_an_oversized_body_is_rejected(harness: Harness) -> None:
    await triage_run(harness)
    intent = offense_intent(harness, arguments={"offense_id": 1, "fields": "x" * 300_000})
    headers = auth(harness.tokens[TRIAGE])

    async with harness.client() as client:
        response = await client.post("/v1/tool-calls", json=intent, headers=headers)

    assert response.status_code == 413
    assert harness.fake.calls == []
