"""Repository functions of `platform_flags`: the kill switch (T-017 criteria 1 and 4)."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from ais0c_storage.db import create_session_factory
from ais0c_storage.enums import ActorKind, PlatformFlag
from ais0c_storage.repositories import (
    PLATFORM_FLAG_AUDIT_ACTION,
    PLATFORM_FLAG_OBJECT_TYPE,
    get_platform_flag,
    list_audit,
    set_platform_flag,
)

pytestmark = pytest.mark.anyio

WRITES = PlatformFlag.WRITES_ENABLED


async def switch(
    session: AsyncSession,
    enabled: bool,
    reason: str = "Canary starts on the selected offenses.",
    *,
    actor_kind: ActorKind = ActorKind.USER,
    actor_id: str = "admin01",
) -> None:
    await set_platform_flag(
        session, WRITES, enabled=enabled, reason=reason, actor_kind=actor_kind, actor_id=actor_id
    )


async def flag_audit(session: AsyncSession) -> list[tuple[str, object, object]]:
    """(actor, new value, replaced value) of every flag change, newest first."""
    entries = await list_audit(
        session, object_type=PLATFORM_FLAG_OBJECT_TYPE, object_id=WRITES.value
    )
    return [
        (entry.actor_id, entry.details["enabled"], entry.details["previous"]) for entry in entries
    ]


async def test_a_new_database_has_no_flag(session: AsyncSession) -> None:
    """Criterion 1: nothing is seeded; a flag without a row is off (shadow mode)."""
    assert await get_platform_flag(session, WRITES) is None


async def test_a_change_records_who_when_and_why(session: AsyncSession) -> None:
    """Criterion 4."""
    row = await set_platform_flag(
        session,
        WRITES,
        enabled=True,
        reason="  Canary starts on the selected offenses.\n",
        actor_kind=ActorKind.USER,
        actor_id=" admin01 ",
    )
    await session.commit()

    assert (row.name, row.enabled, row.reason, row.changed_by) == (
        WRITES,
        True,
        "Canary starts on the selected offenses.",
        "admin01",
    )
    [entry] = await list_audit(session)
    assert (entry.actor_kind, entry.actor_id, entry.action) == (
        ActorKind.USER,
        "admin01",
        PLATFORM_FLAG_AUDIT_ACTION,
    )
    assert (entry.object_type, entry.object_id) == ("platform_flag", "writes_enabled")
    assert entry.details == {
        "enabled": True,
        "previous": None,
        "reason": "Canary starts on the selected offenses.",
    }
    # Both times are the database's transaction time.
    assert entry.at == row.changed_at
    assert row.changed_at.utcoffset() == timedelta(0)
    stored = await get_platform_flag(session, WRITES)
    assert stored is not None
    assert (stored.enabled, stored.changed_at) == (True, row.changed_at)


async def test_every_change_is_audited_with_the_value_it_replaced(session: AsyncSession) -> None:
    await switch(session, True)
    await session.commit()
    await switch(session, False, "Notes repeat the same text.", actor_id="admin02")
    await session.commit()
    # Switching off what is already off is recorded too: who confirmed it, and why.
    await switch(
        session,
        False,
        "Incident review is not finished.",
        actor_kind=ActorKind.SYSTEM,
        actor_id="deploy",
    )
    await session.commit()

    assert await flag_audit(session) == [
        ("deploy", False, False),
        ("admin02", False, True),
        ("admin01", True, None),
    ]
    row = await get_platform_flag(session, WRITES)
    assert row is not None
    assert (row.enabled, row.reason) == (False, "Incident review is not finished.")


@pytest.mark.parametrize("reason", ["", "   ", "\n\t "])
async def test_a_reason_is_required(session: AsyncSession, reason: str) -> None:
    with pytest.raises(ValueError, match="reason is required"):
        await switch(session, True, reason)

    assert await get_platform_flag(session, WRITES) is None
    assert await list_audit(session) == []


async def test_a_blank_actor_is_refused(session: AsyncSession) -> None:
    with pytest.raises(ValueError, match="actor_id is required"):
        await switch(session, True, actor_id=" ")

    assert await get_platform_flag(session, WRITES) is None


async def test_an_agent_cannot_change_a_flag(session: AsyncSession) -> None:
    with pytest.raises(ValueError, match="agent cannot change"):
        await switch(session, True, actor_kind=ActorKind.AGENT, actor_id="triage")

    assert await get_platform_flag(session, WRITES) is None
    assert await list_audit(session) == []


@pytest.mark.parametrize("enabled", [1, "false", None])
async def test_enabled_must_be_a_bool(session: AsyncSession, enabled: object) -> None:
    with pytest.raises(TypeError, match="True or False"):
        await switch(session, enabled)  # type: ignore[arg-type]

    assert await get_platform_flag(session, WRITES) is None


async def test_an_unknown_flag_is_refused(session: AsyncSession) -> None:
    with pytest.raises(ValueError, match="not a valid PlatformFlag"):
        await set_platform_flag(
            session,
            "notes_enabled",  # type: ignore[arg-type]
            enabled=True,
            reason="x",
            actor_kind=ActorKind.USER,
            actor_id="admin01",
        )
    with pytest.raises(ValueError, match="not a valid PlatformFlag"):
        await get_platform_flag(session, "notes_enabled")  # type: ignore[arg-type]


async def test_a_rolled_back_change_leaves_no_audit_entry(session: AsyncSession) -> None:
    """The change and its audit entry are committed or rolled back together."""
    await switch(session, True)
    await session.rollback()

    assert await get_platform_flag(session, WRITES) is None
    assert await list_audit(session) == []


async def test_concurrent_changes_take_turns(engine: AsyncEngine) -> None:
    """The second change waits for the first to commit and records the value it replaced."""
    sessions = create_session_factory(engine)
    async with sessions.begin() as session:
        await switch(session, False, "Shadow mode.", actor_id="admin00")

    async with sessions() as first, sessions() as second:
        await switch(first, True, actor_id="admin01")
        racing = asyncio.create_task(switch(second, False, "Stop.", actor_id="admin02"))
        await asyncio.sleep(0.3)
        assert not racing.done()
        await first.commit()
        await racing
        await second.commit()

    async with sessions() as session:
        assert await flag_audit(session) == [
            ("admin02", False, True),
            ("admin01", True, False),
            ("admin00", False, None),
        ]
