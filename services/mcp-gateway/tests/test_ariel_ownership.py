"""Criterion 6: an Ariel search can be read and deleted only in the case or hunt that started it
through the gateway (and under the same profile). Other searches never reach QRadar."""

import uuid
from typing import Any

import pytest
from gateway_support import CASE_ID, HUNT_ID, OTHER_CASE_ID, Harness, tool_result

from ais0c_contracts import ToolResult, ToolStatus

pytestmark = pytest.mark.anyio

INVESTIGATE = "qradar-investigate-read"
VERIFY = "qradar-verify-read"
HUNT = "qradar-hunt-read"
QUERY = "SELECT qid FROM events WHERE username = 'svc_backup_7731' LIMIT 10 LAST 1 HOURS"
FOLLOW_UPS = ["get_ariel_search_status", "get_ariel_search_results", "delete_ariel_search"]
NOT_OWNED = "search_not_owned: the search was not started through the gateway in this case or hunt"


class Context:
    """One agent run in a case or hunt, under one profile."""

    def __init__(
        self,
        harness: Harness,
        run_id: str,
        profile: str = INVESTIGATE,
        *,
        case_id: str | None = CASE_ID,
        hunt_id: str | None = None,
    ) -> None:
        self.harness = harness
        self.run_id = run_id
        self.profile = profile
        self.ids: dict[str, Any] = {"case_id": case_id, "hunt_id": hunt_id}

    async def start(self) -> "Context":
        await self.harness.start_run(self.run_id, profile=self.profile, **self.ids)
        return self

    async def call(self, tool_id: str, **arguments: object) -> ToolResult:
        intent = self.harness.intent(self.profile, tool_id, arguments, **self.ids)
        async with self.harness.client() as client:
            return tool_result(await self.harness.post(client, intent, run_id=self.run_id))

    async def create(self) -> str:
        result = await self.call("create_ariel_search", query_expression=QUERY)
        assert result.status is ToolStatus.OK, result.deny_reason
        return str(result.data[0]["search_id"])


async def test_the_starting_case_can_read_and_delete_its_search(harness: Harness) -> None:
    owner = await Context(harness, "run-owner").start()
    search_id = await owner.create()

    for tool_id in FOLLOW_UPS:
        result = await owner.call(tool_id, search_id=search_id)
        assert result.status is ToolStatus.OK, (tool_id, result.deny_reason)
    for tool_id in FOLLOW_UPS:
        assert harness.fake.tool_calls(tool_id) == [{"search_id": search_id}]


async def test_another_run_of_the_same_case_can_read_it(harness: Harness) -> None:
    search_id = await (await Context(harness, "run-first").start()).create()
    second = await Context(harness, "run-second").start()

    result = await second.call("get_ariel_search_results", search_id=search_id)

    assert result.status is ToolStatus.OK


@pytest.mark.parametrize("tool_id", FOLLOW_UPS)
async def test_another_case_cannot_read_or_delete_it(harness: Harness, tool_id: str) -> None:
    search_id = await (await Context(harness, "run-owner").start()).create()
    stranger = await Context(harness, "run-stranger", case_id=OTHER_CASE_ID).start()

    result = await stranger.call(tool_id, search_id=search_id)

    assert result.status is ToolStatus.DENIED
    assert result.deny_reason is not None
    assert result.deny_reason.startswith(NOT_OWNED)
    assert harness.fake.tool_calls(tool_id) == []


async def test_a_hunt_cannot_read_a_case_search(harness: Harness) -> None:
    search_id = await (await Context(harness, "run-owner").start()).create()
    hunt = await Context(harness, "run-hunt", HUNT, case_id=None, hunt_id=HUNT_ID).start()

    result = await hunt.call("get_ariel_search_results", search_id=search_id)

    assert result.status is ToolStatus.DENIED
    assert harness.fake.tool_calls("get_ariel_search_results") == []


async def test_a_hunt_owns_its_searches_and_another_hunt_does_not(harness: Harness) -> None:
    owner = await Context(harness, "run-hunt-1", HUNT, case_id=None, hunt_id=HUNT_ID).start()
    search_id = await owner.create()
    other = await Context(harness, "run-hunt-2", HUNT, case_id=None, hunt_id="hunt-other").start()
    case_with_same_hunt = await Context(
        harness, "run-case-hunt", HUNT, case_id=CASE_ID, hunt_id=HUNT_ID
    ).start()

    assert (
        await owner.call("get_ariel_search_status", search_id=search_id)
    ).status is ToolStatus.OK
    for intruder in (other, case_with_same_hunt):
        result = await intruder.call("get_ariel_search_status", search_id=search_id)
        assert result.status is ToolStatus.DENIED


async def test_another_profile_in_the_same_case_cannot_read_it(harness: Harness) -> None:
    # The verify profile filters free text; it must not read a search whose query it could not
    # have run itself.
    search_id = await (await Context(harness, "run-investigate").start()).create()
    verifier = await Context(harness, "run-verify", VERIFY).start()

    result = await verifier.call("get_ariel_search_results", search_id=search_id)

    assert result.status is ToolStatus.DENIED
    assert harness.fake.tool_calls("get_ariel_search_results") == []


@pytest.mark.parametrize("tool_id", FOLLOW_UPS)
async def test_a_search_not_started_through_the_gateway_is_refused(
    harness: Harness, tool_id: str
) -> None:
    # An analyst's search, or one an MCP client started without the gateway.
    context = await Context(harness, "run-1").start()

    result = await context.call(tool_id, search_id=str(uuid.uuid4()))

    assert result.status is ToolStatus.DENIED
    assert harness.fake.calls == []


async def test_a_failed_create_owns_nothing(harness: Harness) -> None:
    context = await Context(harness, "run-1").start()
    search_id = str(uuid.uuid4())
    harness.fake.responses["create_ariel_search"] = lambda arguments: RuntimeError(
        f"QRadar request failed for {search_id}"
    )
    failed = await context.call("create_ariel_search", query_expression=QUERY)
    assert failed.status is ToolStatus.ERROR

    result = await context.call("get_ariel_search_results", search_id=search_id)

    assert result.status is ToolStatus.DENIED
    assert harness.fake.tool_calls("get_ariel_search_results") == []
