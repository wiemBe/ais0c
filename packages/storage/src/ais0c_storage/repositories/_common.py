"""Helpers shared by the repository modules."""

from collections.abc import Mapping

from sqlalchemy import Select, Update, insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ais0c_storage.errors import DuplicateError, NotFoundError
from ais0c_storage.models import Base

UNIQUE_VIOLATION = "23505"

# Reads refresh objects the session already holds, so a row changed by a statement (an
# upsert, an UPDATE ... RETURNING elsewhere) never comes back with its old values.
_FRESH = {"populate_existing": True}


def is_unique_violation(error: IntegrityError) -> bool:
    return getattr(error.orig, "sqlstate", None) == UNIQUE_VIOLATION


async def get_row[R: Base](session: AsyncSession, entity: type[R], key: object) -> R | None:
    """The row with primary key `key` as it is in the database now."""
    return await session.get(entity, key, populate_existing=True)


async def fetch_one[R](session: AsyncSession, statement: Select[R]) -> R | None:
    return await session.scalar(statement, execution_options=_FRESH)


async def fetch_all[R](session: AsyncSession, statement: Select[R]) -> list[R]:
    return list(await session.scalars(statement, execution_options=_FRESH))


async def insert_row[R: Base](
    session: AsyncSession, entity: type[R], values: Mapping[str, object]
) -> R:
    """INSERT ... RETURNING the new row, server defaults included.

    A statement rather than `session.add`, so a duplicate key is reported by the database and
    never clashes with an object already loaded in the session.
    """
    statement = insert(entity).values(**values).returning(entity)
    return (await session.scalars(statement)).one()


async def insert_new[R: Base](
    session: AsyncSession, entity: type[R], values: Mapping[str, object], description: str
) -> R:
    """`insert_row` inside a savepoint, for rows whose key may already exist.

    A primary key or unique key violation raises `DuplicateError`; the savepoint is rolled back
    and the caller's transaction stays usable.
    """
    try:
        async with session.begin_nested():
            return await insert_row(session, entity, values)
    except IntegrityError as error:
        if is_unique_violation(error):
            raise DuplicateError(f"{description} already exists") from error
        raise


async def update_one[R: Base](
    session: AsyncSession, statement: Update, entity: type[R], description: str
) -> R:
    """Run `statement`, an UPDATE of a single row, and return the updated row.

    Raises `NotFoundError` when no row matched. Objects already loaded in the session are
    refreshed with the new values.
    """
    result = await session.scalars(
        statement.returning(entity), execution_options={"populate_existing": True}
    )
    row = result.one_or_none()
    if row is None:
        raise NotFoundError(f"{description} not found")
    return row
