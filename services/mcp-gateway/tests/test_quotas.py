"""Criterion 8: the case and hunt pools have independent limits; a full hunt pool never makes
case calls wait. Also: room comes back when a search ends, and hunts start searches only in
their allowed hours."""

import asyncio
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from gateway_support import CASE_ID, HUNT_ID, Harness, tool_result

from ais0c_contracts import ToolResult, ToolStatus
from ais0c_mcp_gateway.quotas import Admission, QuotaDenial, QuotaPool
from ais0c_mcp_gateway.registry import HourRange, QuotaPoolConfig

pytestmark = pytest.mark.anyio

QUERY = "SELECT qid FROM events WHERE username = 'svc_backup_7731' LIMIT 10 LAST 1 HOURS"


def pool_config(**changes: object) -> QuotaPoolConfig:
    fields: dict[str, Any] = {
        "concurrent_searches": 1,
        "requests_per_minute": 100,
        "max_wait_seconds": 0.3,
        "search_ttl_seconds": 600,
    }
    return QuotaPoolConfig.model_validate(fields | changes)


def pools(**hunt: object) -> tuple[QuotaPool, QuotaPool]:
    case = QuotaPool("case", pool_config(concurrent_searches=3))
    return case, QuotaPool("hunt", pool_config(**hunt))


# --- the pools ------------------------------------------------------------------------------


async def test_a_full_hunt_pool_does_not_delay_the_case_pool() -> None:
    case, hunt = pools()
    first = await hunt.admit(starts_search=True)
    assert isinstance(first, Admission)

    waiting = asyncio.create_task(hunt.admit(starts_search=True))
    started = time.monotonic()
    case_admission = await case.admit(starts_search=True)
    case_elapsed = time.monotonic() - started

    assert isinstance(case_admission, Admission)
    assert case_elapsed < 0.05
    assert not waiting.done()
    assert await waiting == QuotaDenial(pool="hunt", limit="concurrent_searches")


async def test_the_request_rate_is_limited_per_pool() -> None:
    case, hunt = pools(requests_per_minute=2, max_wait_seconds=0)

    assert isinstance(await hunt.admit(starts_search=False), Admission)
    assert isinstance(await hunt.admit(starts_search=False), Admission)
    assert await hunt.admit(starts_search=False) == QuotaDenial(
        pool="hunt", limit="requests_per_minute"
    )
    assert isinstance(await case.admit(starts_search=False), Admission)


async def test_calls_that_start_no_search_ignore_search_slots() -> None:
    _, hunt = pools()
    assert isinstance(await hunt.admit(starts_search=True), Admission)

    assert isinstance(await hunt.admit(starts_search=False), Admission)


async def test_an_ended_search_lets_a_waiting_call_in() -> None:
    _, hunt = pools(max_wait_seconds=5)
    first = await hunt.admit(starts_search=True)
    assert isinstance(first, Admission)
    await hunt.bind_search(first, "search-1")

    waiting = asyncio.create_task(hunt.admit(starts_search=True))
    await asyncio.sleep(0.05)
    started = time.monotonic()
    await hunt.finish_search("search-1")

    assert isinstance(await waiting, Admission)
    assert time.monotonic() - started < 0.5


async def test_a_search_that_was_not_created_gives_its_slot_back() -> None:
    _, hunt = pools(max_wait_seconds=0)
    reserved = await hunt.admit(starts_search=True)
    assert isinstance(reserved, Admission)

    await hunt.release(reserved)
    await hunt.release(reserved)

    assert hunt.open_searches == 0
    assert isinstance(await hunt.admit(starts_search=True), Admission)


async def test_a_bound_slot_is_not_released_by_mistake() -> None:
    _, hunt = pools(max_wait_seconds=0)
    admission = await hunt.admit(starts_search=True)
    assert isinstance(admission, Admission)
    await hunt.bind_search(admission, "search-1")

    await hunt.release(admission)

    assert hunt.open_searches == 1


async def test_an_abandoned_search_frees_its_slot_after_the_ttl() -> None:
    clock = [1000.0]
    hunt = QuotaPool(
        "hunt", pool_config(max_wait_seconds=0, search_ttl_seconds=60), monotonic=lambda: clock[0]
    )
    first = await hunt.admit(starts_search=True)
    assert isinstance(first, Admission)
    await hunt.bind_search(first, "search-1")
    assert isinstance(await hunt.admit(starts_search=True), QuotaDenial)

    clock[0] += 60

    assert isinstance(await hunt.admit(starts_search=True), Admission)


@pytest.mark.parametrize(
    ("utc", "allowed"),
    [
        (datetime(2026, 10, 3, 17, 0, tzinfo=UTC), True),  # 20:00 in Istanbul
        (datetime(2026, 10, 3, 21, 0, tzinfo=UTC), True),  # 00:00
        (datetime(2026, 10, 4, 3, 59, tzinfo=UTC), True),  # 06:59
        (datetime(2026, 10, 4, 4, 0, tzinfo=UTC), False),  # 07:00
        (datetime(2026, 10, 3, 9, 0, tzinfo=UTC), False),  # 12:00
        (datetime(2026, 10, 3, 16, 59, tzinfo=UTC), False),  # 19:59
    ],
)
def test_hunt_hours_are_local_and_cross_midnight(utc: datetime, allowed: bool) -> None:
    hunt = QuotaPool(
        "hunt",
        pool_config(
            allowed_hours=HourRange.model_validate("20:00-07:00"), time_zone="Europe/Istanbul"
        ),
    )
    assert hunt.allows_new_search(utc) is allowed
    assert QuotaPool("case", pool_config()).allows_new_search(utc)


# --- through the gateway --------------------------------------------------------------------


def small_hunt_pool(manifest: dict[str, Any], policy: dict[str, Any]) -> None:
    manifest["quota_pools"]["hunt"]["concurrent_searches"] = 1
    manifest["quota_pools"]["hunt"]["max_wait_seconds"] = 2


async def create(harness: Harness, run_id: str, *, hunt: bool) -> ToolResult:
    profile = "qradar-hunt-read" if hunt else "qradar-investigate-read"
    ids: dict[str, Any] = {"case_id": None, "hunt_id": HUNT_ID} if hunt else {"case_id": CASE_ID}
    intent = harness.intent(profile, "create_ariel_search", {"query_expression": QUERY}, **ids)
    async with harness.client() as client:
        return tool_result(await harness.post(client, intent, run_id=run_id))


async def start_runs(harness: Harness) -> None:
    await harness.start_run("run-hunt", profile="qradar-hunt-read", case_id=None, hunt_id=HUNT_ID)
    await harness.start_run("run-case", profile="qradar-investigate-read")


async def test_a_full_hunt_pool_never_delays_case_calls(
    make_harness: Callable[..., Harness],
) -> None:
    harness = make_harness(small_hunt_pool)
    await start_runs(harness)
    assert (await create(harness, "run-hunt", hunt=True)).status is ToolStatus.OK

    second_hunt = asyncio.create_task(create(harness, "run-hunt", hunt=True))
    await asyncio.sleep(0.05)
    started = time.monotonic()
    case_result = await create(harness, "run-case", hunt=False)
    case_elapsed = time.monotonic() - started

    assert case_result.status is ToolStatus.OK
    assert case_elapsed < 1
    assert not second_hunt.done()
    hunt_result = await second_hunt
    assert hunt_result.status is ToolStatus.DENIED
    assert hunt_result.deny_reason == "quota_exhausted: hunt pool, concurrent_searches; try later"
    assert len(harness.fake.tool_calls("create_ariel_search")) == 2


async def test_deleting_a_hunt_search_makes_room(make_harness: Callable[..., Harness]) -> None:
    harness = make_harness(small_hunt_pool)
    await start_runs(harness)
    first = await create(harness, "run-hunt", hunt=True)
    search_id = str(first.data[0]["search_id"])
    intent = harness.intent(
        "qradar-hunt-read",
        "delete_ariel_search",
        {"search_id": search_id},
        case_id=None,
        hunt_id=HUNT_ID,
    )
    async with harness.client() as client:
        assert tool_result(await harness.post(client, intent, run_id="run-hunt")).status is (
            ToolStatus.OK
        )

    assert (await create(harness, "run-hunt", hunt=True)).status is ToolStatus.OK


async def test_hunts_start_searches_only_in_their_hours(harness: Harness) -> None:
    await start_runs(harness)
    harness.clock[0] = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)  # 12:00 in Istanbul

    hunt_result = await create(harness, "run-hunt", hunt=True)
    case_result = await create(harness, "run-case", hunt=False)

    assert hunt_result.status is ToolStatus.DENIED
    assert hunt_result.deny_reason == "outside_allowed_hours: the hunt pool starts no search now"
    assert case_result.status is ToolStatus.OK
    assert len(harness.fake.tool_calls("create_ariel_search")) == 1
