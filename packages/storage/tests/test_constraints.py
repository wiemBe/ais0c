"""Unique keys of the write records (criterion 3) and the append-only audit log (criterion 4)."""

from typing import LiteralString

import psycopg
import pytest
import storage_payloads as payloads
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from storage_postgres import Server

from ais0c_contracts import CaseSource, Level
from ais0c_storage.enums import ActorKind, NoteStatus, NotificationStatus
from ais0c_storage.errors import DuplicateError
from ais0c_storage.repositories import (
    append_audit,
    create_case,
    get_note,
    list_audit,
    record_note,
    record_notification,
)

pytestmark = pytest.mark.anyio

INSUFFICIENT_PRIVILEGE = "42501"
UNIQUE_VIOLATION = "23505"


async def open_case(session: AsyncSession) -> None:
    await create_case(
        session,
        case_id=payloads.CASE_ID,
        source=CaseSource.OFFENSE,
        offense_id=payloads.OFFENSE_ID,
        sla_due_at=payloads.T1,
        workflow_id=payloads.CASE_ID,
        run_id="run-1",
    )


def sqlstate(error: DBAPIError) -> str | None:
    return getattr(error.orig, "sqlstate", None)


# --- Criterion 3


async def test_same_note_cannot_be_recorded_twice(session: AsyncSession) -> None:
    await open_case(session)
    note = payloads.note_content(run_marker="7f3a9c")
    await record_note(
        session,
        note,
        case_id=payloads.CASE_ID,
        status=NoteStatus.WRITTEN,
        written_at=payloads.T1,
    )

    with pytest.raises(DuplicateError):
        await record_note(
            session,
            note,
            case_id=payloads.CASE_ID,
            status=NoteStatus.SKIPPED_DUPLICATE,
            written_at=payloads.T1,
        )

    # The duplicate was rolled back to its savepoint; the transaction goes on.
    await record_note(
        session,
        payloads.note_content(run_marker="8b4d1e", evaluation_no=2),
        case_id=payloads.CASE_ID,
        status=NoteStatus.WRITTEN,
        written_at=payloads.T1,
    )
    await session.commit()
    stored = await get_note(session, offense_id=payloads.OFFENSE_ID, run_marker="7f3a9c")
    assert stored is not None
    assert stored.status is NoteStatus.WRITTEN


async def test_database_rejects_a_duplicate_note(session: AsyncSession) -> None:
    """The constraint itself, without the repository."""
    await open_case(session)
    await session.commit()
    insert = text(
        "INSERT INTO notes_written (id, case_id, offense_id, evaluation_no, run_marker, status,"
        " written_at) VALUES (gen_random_uuid(), :case_id, 12345, 1, '7f3a9c', 'written', now())"
    )
    await session.execute(insert, {"case_id": payloads.CASE_ID})

    with pytest.raises(IntegrityError) as raised:
        await session.execute(insert, {"case_id": payloads.CASE_ID})

    assert sqlstate(raised.value) == UNIQUE_VIOLATION


async def test_same_run_marker_on_another_offense_is_a_different_note(
    session: AsyncSession,
) -> None:
    await open_case(session)
    first = payloads.note_content(run_marker="7f3a9c")
    other = first.model_copy(update={"offense_id": payloads.OFFENSE_ID + 1})
    for note in (first, other):
        await record_note(
            session,
            note,
            case_id=payloads.CASE_ID,
            status=NoteStatus.WRITTEN,
            written_at=payloads.T1,
        )
    await session.commit()


async def test_same_email_cannot_be_recorded_twice(session: AsyncSession) -> None:
    message = payloads.email_message(idempotency_key="case-12345:1")
    await record_notification(
        session,
        message,
        status=NotificationStatus.SENT,
        level=Level.HIGH,
        case_id=payloads.CASE_ID,
        sent_at=payloads.T1,
    )

    with pytest.raises(DuplicateError):
        await record_notification(
            session,
            message,
            status=NotificationStatus.SENT,
            level=Level.HIGH,
            case_id=payloads.CASE_ID,
            sent_at=payloads.T1,
        )
    await session.commit()


async def test_database_rejects_a_duplicate_idempotency_key(session: AsyncSession) -> None:
    insert = text(
        "INSERT INTO notifications (id, kind, case_id, recipients, subject, idempotency_key,"
        " status) VALUES (gen_random_uuid(), 'case_alert', 'case-12345',"
        " ARRAY['soc-operators@example.com'], 'subject', 'case-12345:1', 'failed')"
    )
    await session.execute(insert)

    with pytest.raises(IntegrityError) as raised:
        await session.execute(insert)

    assert sqlstate(raised.value) == UNIQUE_VIOLATION


# --- Criterion 4


async def write_audit_entry(engine: AsyncEngine) -> None:
    async with AsyncSession(engine) as session, session.begin():
        await append_audit(
            session,
            actor_kind=ActorKind.USER,
            actor_id="admin01",
            action="catalog.rule.update",
            object_type="catalog_rule",
            object_id="100201",
            details={"mode": "skip"},
        )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_log SET action = 'catalog.rule.delete'",
        "DELETE FROM audit_log",
        "TRUNCATE audit_log",
        # Statement-level: even a change that matches no row is refused.
        "UPDATE audit_log SET action = 'x' WHERE false",
        "DELETE FROM audit_log WHERE false",
    ],
)
async def test_audit_log_refuses_changes_from_the_application(
    engine: AsyncEngine, statement: str
) -> None:
    await write_audit_entry(engine)

    with pytest.raises(DBAPIError) as raised:
        async with engine.begin() as connection:
            await connection.execute(text(statement))

    assert sqlstate(raised.value) == INSUFFICIENT_PRIVILEGE
    assert "audit_log is append-only" in str(raised.value.orig)
    async with AsyncSession(engine) as session:
        entries = await list_audit(session)
    assert [entry.action for entry in entries] == ["catalog.rule.update"]


async def test_trigger_not_privileges_does_the_refusing(engine: AsyncEngine) -> None:
    """The application role owns the table and holds UPDATE and DELETE; only the trigger
    stops it."""
    async with engine.connect() as connection:
        privileges = await connection.execute(
            text(
                "SELECT has_table_privilege('audit_log', 'UPDATE'),"
                " has_table_privilege('audit_log', 'DELETE')"
            )
        )
        assert privileges.one() == (True, True)
        triggers = await connection.scalars(
            text(
                "SELECT tgname FROM pg_trigger"
                " WHERE tgrelid = 'audit_log'::regclass AND NOT tgisinternal AND tgenabled = 'O'"
            )
        )
        assert list(triggers) == ["audit_log_append_only"]


@pytest.mark.parametrize(
    "statement", ["UPDATE audit_log SET actor_id = 'x'", "DELETE FROM audit_log"]
)
async def test_audit_log_refuses_changes_from_a_superuser(
    server: Server, database: str, engine: AsyncEngine, statement: LiteralString
) -> None:
    await write_audit_entry(engine)

    with (
        server.admin_connection(database) as connection,
        pytest.raises(psycopg.errors.InsufficientPrivilege),
    ):
        connection.execute(statement)


async def test_audit_log_accepts_inserts(engine: AsyncEngine) -> None:
    await write_audit_entry(engine)
    await write_audit_entry(engine)

    async with AsyncSession(engine) as session:
        entries = await list_audit(session, object_type="catalog_rule", object_id="100201")

    assert len(entries) == 2
    assert entries[0].id > entries[1].id
