"""Criterion 5: the tool descriptions agents see come from config/connectors/qradar.yaml.

The fake MCP server's tool metadata (description, title, parameter descriptions) carries an
injected instruction. It must never reach an agent: not through the gateway's tool list, not
through a result, and not into the model's context in a real agent run.
"""

import json

import pytest
from gateway_support import (
    AGENT_RUN,
    DESCRIPTION_INJECTION,
    Harness,
    agent_helpers,
    auth,
    load_config,
    tool_result,
    triage_through_gateway,
)

from ais0c_contracts import RunStatus, ToolStatus

pytestmark = pytest.mark.anyio

QUERY = "SELECT qid FROM events WHERE username = 'svc_backup_7731' LIMIT 10 LAST 1 HOURS"


async def test_tool_lists_hold_only_registry_text(harness: Harness) -> None:
    manifest, _ = load_config()
    async with harness.client() as client:
        for profile, token in harness.tokens.items():
            response = await client.get("/v1/tools", headers=auth(token))

            assert DESCRIPTION_INJECTION not in response.text
            for tool in response.json()["tools"]:
                registry_entry = manifest["tools"][tool["id"]]
                assert tool["description"] == registry_entry["description"], profile
                assert tool["parameters"] == registry_entry["input_schema"], profile


async def test_results_hold_no_upstream_metadata(harness: Harness) -> None:
    run = await harness.start_run(AGENT_RUN, profile="qradar-investigate-read")
    profile = "qradar-investigate-read"
    async with harness.client() as client:
        created = tool_result(
            await harness.post(
                client,
                harness.intent(profile, "create_ariel_search", {"query_expression": QUERY}),
                run_id=run,
            )
        )
        search_id = str(created.data[0]["search_id"])
        responses = [
            await harness.post(client, harness.intent(profile, tool, arguments), run_id=run)
            for tool, arguments in [
                ("get_offense", {"offense_id": 1001}),
                ("list_assets", {}),
                ("get_ariel_search_status", {"search_id": search_id}),
                ("get_ariel_search_results", {"search_id": search_id}),
            ]
        ]
    for response in responses:
        assert tool_result(response).status is ToolStatus.OK
        assert DESCRIPTION_INJECTION not in response.text


async def test_the_gateway_never_reads_tool_metadata_from_the_server(harness: Harness) -> None:
    await test_results_hold_no_upstream_metadata(harness)
    async with harness.client() as client:
        for token in harness.tokens.values():
            await client.get("/v1/tools", headers=auth(token))

    assert len(harness.fake.calls) == 5
    assert harness.fake.tool_listings == 0


async def test_injected_upstream_text_never_reaches_the_model(harness: Harness) -> None:
    helpers = agent_helpers()

    triage = await triage_through_gateway(harness)

    assert triage.run.status is RunStatus.COMPLETED, triage.run.error
    assert triage.requests
    registry_tools = harness.registry.profiles["qradar-triage-read"].tools
    for messages, info in triage.requests:
        for definition in info.function_tools:
            assert definition.description == registry_tools[definition.name].entry.description
            assert DESCRIPTION_INJECTION not in json.dumps(definition.parameters_json_schema)
        for text in helpers.model_inputs(messages, info):
            assert DESCRIPTION_INJECTION not in text
