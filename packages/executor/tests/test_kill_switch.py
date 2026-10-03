"""The kill switch (T-017 criteria 1-3): checked right before every external write."""

import asyncio
import secrets
import time
from pathlib import Path

import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from storage_postgres import Server  # pyright: ignore[reportMissingImports]

from ais0c_executor.common import ExecutorError, KillSwitch, Templates, WritesDisabled
from ais0c_storage import ActorKind, PlatformFlag, create_engine, create_session_factory
from ais0c_storage.repositories import set_platform_flag

pytestmark = pytest.mark.anyio

type Sessions = async_sessionmaker[AsyncSession]


class Outbox:
    """Stands in for QRadar or the SMTP relay: keeps what was sent."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, content: str) -> str:
        self.sent.append(content)
        return "sent"


async def switch(
    sessions: Sessions,
    enabled: bool,
    reason: str = "Canary starts on the selected offenses.",
    actor_id: str = "admin01",
) -> None:
    """What an admin does from the UI, committed at once."""
    async with sessions.begin() as session:
        await set_platform_flag(
            session,
            PlatformFlag.WRITES_ENABLED,
            enabled=enabled,
            reason=reason,
            actor_kind=ActorKind.USER,
            actor_id=actor_id,
        )


async def test_a_new_platform_writes_nothing(sessions: Sessions) -> None:
    """Criterion 1: without the flag, writes are off (shadow mode)."""
    kill_switch = KillSwitch(sessions)
    outbox = Outbox()

    assert await kill_switch.writes_enabled() is False
    with pytest.raises(WritesDisabled, match="never switched on") as raised:
        await kill_switch.guarded(lambda: outbox.send("note"))

    assert outbox.sent == []
    assert isinstance(raised.value, ExecutorError)
    assert (raised.value.reason, raised.value.changed_by, raised.value.changed_at) == (
        None,
        None,
        None,
    )


async def test_writes_go_out_while_the_switch_is_on(sessions: Sessions) -> None:
    await switch(sessions, True)
    kill_switch = KillSwitch(sessions)
    outbox = Outbox()

    assert await kill_switch.writes_enabled() is True
    await kill_switch.check()
    assert await kill_switch.guarded(lambda: outbox.send("note")) == "sent"
    assert outbox.sent == ["note"]


async def test_a_switched_off_flag_says_who_and_why(sessions: Sessions) -> None:
    await switch(sessions, True)
    await switch(sessions, False, "Notes repeat themselves; under review.", "admin02")
    kill_switch = KillSwitch(sessions)

    assert await kill_switch.writes_enabled() is False
    with pytest.raises(WritesDisabled, match="switched off by admin02") as raised:
        await kill_switch.check()

    assert raised.value.reason == "Notes repeat themselves; under review."
    assert raised.value.changed_by == "admin02"
    assert raised.value.changed_at is not None


async def test_the_message_gives_the_reason_on_one_line(sessions: Sessions) -> None:
    await switch(sessions, False, "Notes repeat themselves.\nUnder review.")
    kill_switch = KillSwitch(sessions)

    with pytest.raises(WritesDisabled) as raised:
        await kill_switch.check()
    assert str(raised.value).endswith(": Notes repeat themselves. Under review.")
    assert raised.value.reason == "Notes repeat themselves.\nUnder review."

    # A flag written behind the repository may have no reason.
    async with sessions.begin() as session:
        await session.execute(text("UPDATE platform_flags SET reason = NULL"))
    with pytest.raises(WritesDisabled) as raised:
        await kill_switch.check()
    changed_at = raised.value.changed_at
    assert changed_at is not None
    assert str(raised.value).endswith(f"at {changed_at.isoformat()}")


async def test_a_switch_off_after_the_content_is_ready_stops_the_send(
    sessions: Sessions, tmp_path: Path
) -> None:
    """Criterion 2. Writes were on when the work began and when the content was rendered; the
    admin switches them off before the send, and nothing is sent."""
    (tmp_path / "note.txt").write_text("[AI-SOC] Değerlendirme #{{ n }}\nÖzet: {{ summary }}")
    templates = Templates(tmp_path)
    await switch(sessions, True)
    kill_switch = KillSwitch(sessions)
    outbox = Outbox()

    await kill_switch.check()
    content = templates.render("note.txt", n=1, summary="SMB access from 203.0.113.7.")
    await switch(sessions, False, "Kill switch drill.")

    with pytest.raises(WritesDisabled, match="switched off by admin01"):
        await kill_switch.guarded(lambda: outbox.send(content))
    assert outbox.sent == []


async def test_no_write_goes_out_after_the_switch_off(sessions: Sessions) -> None:
    """Criterion 3. A writer sends every 10 ms while an admin switches writes off: nothing
    goes out later than 5 seconds after the switch-off. Nothing is cached, so in fact every
    attempt that starts after it is refused."""
    await switch(sessions, True)
    kill_switch = KillSwitch(sessions)
    outbox = Outbox()
    attempts: list[tuple[float, bool]] = []  # (start, sent)
    stop = asyncio.Event()

    async def writer() -> None:
        while not stop.is_set():
            started = time.monotonic()
            try:
                await kill_switch.guarded(lambda: outbox.send("note"))
            except WritesDisabled:
                attempts.append((started, False))
            else:
                attempts.append((started, True))
            await asyncio.sleep(0.01)

    task = asyncio.create_task(writer())
    await asyncio.sleep(0.3)
    await switch(sessions, False, "Kill switch drill.")
    switched_off_at = time.monotonic()
    await asyncio.sleep(1.0)
    stop.set()
    await task

    sent = [started for started, ok in attempts if ok]
    refused = [started for started, ok in attempts if not ok]
    assert sent
    assert len([started for started in refused if started > switched_off_at]) >= 10
    assert all(started < switched_off_at + 5 for started in sent)
    assert all(started < switched_off_at for started in sent)
    assert len(outbox.sent) == len(sent)


async def test_every_check_reads_the_database(engine: AsyncEngine, sessions: Sessions) -> None:
    """Criterion 3: the flag is read on every check, not kept from an earlier one."""
    await switch(sessions, True)
    kill_switch = KillSwitch(sessions)
    reads: list[str] = []

    def record(connection: Connection, cursor: object, statement: str, *_: object) -> None:
        if "FROM platform_flags" in statement:
            reads.append(statement)

    outbox = Outbox()
    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        await kill_switch.check()
        await kill_switch.check()
        await kill_switch.writes_enabled()
        await kill_switch.guarded(lambda: outbox.send("note"))
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)

    assert len(reads) == 4
    assert outbox.sent == ["note"]


@pytest.mark.parametrize("problem", ["database-down", "table-missing"])
async def test_an_unreadable_flag_stops_writes(server: Server, problem: str) -> None:
    """If the flag cannot be read, the error propagates and nothing is sent."""
    name = f"test_{secrets.token_hex(6)}"
    if problem == "table-missing":
        # A database the migrations have not reached.
        server.create_database(name)
    engine = create_engine(server.app_url(name))
    outbox = Outbox()
    try:
        kill_switch = KillSwitch(create_session_factory(engine))
        with pytest.raises(DBAPIError):
            await kill_switch.guarded(lambda: outbox.send("note"))
        with pytest.raises(DBAPIError):
            await kill_switch.writes_enabled()
    finally:
        await engine.dispose()
        server.drop_database(name)

    assert outbox.sent == []
