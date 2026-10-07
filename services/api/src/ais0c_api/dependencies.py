"""What every route needs: the database session and the request's session (identity).

The dependencies take the `Request` FastAPI hands them, so a test can build the app with its own
database and its own `DevAuthenticator` and exercise the real routes.

Two rules every route follows:

- The role dependency (`OPERATOR`, `HUNTER`, `ADMIN`) comes before the database session in the
  signature. FastAPI resolves dependencies in that order, so a request without a valid token or
  role is refused before any session exists. A session connects lazily today, so this is not
  what keeps such a request off the database; the order makes sure it never depends on that. A
  test checks it on every route.
- The database sessions are `scope="function"`: the transaction ends when the route returns,
  before the answer is sent. With FastAPI's default scope the commit would run after the client
  already has its 2xx, so a failed commit would be invisible to it and a read right after the
  answer could miss the change.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ais0c_api.auth import DevAuthenticator, Role, Session
from ais0c_api.problems import Problem
from ais0c_api.temporal import ScheduleTrigger

SessionFactory = async_sessionmaker[AsyncSession]


def now() -> datetime:
    """The API's clock: UTC. Every timestamp it writes or shows comes from here."""
    return datetime.now(UTC)


def sessions_of(request: Request) -> SessionFactory:
    """The app's session factory (set by `ais0c_api.app.build_app`)."""
    return request.app.state.sessions


def authenticator_of(request: Request) -> DevAuthenticator:
    return request.app.state.authenticator


def trigger_of(request: Request) -> ScheduleTrigger:
    return request.app.state.schedule_trigger


async def read_session(request: Request) -> AsyncIterator[AsyncSession]:
    """A session for a request that only reads; the transaction is closed, never committed."""
    async with sessions_of(request)() as session:
        yield session


async def write_session(request: Request) -> AsyncIterator[AsyncSession]:
    """A session for a request that changes something.

    The transaction is the route's: it commits when the route returns and rolls back when the
    route raises, so a rejected request writes neither the change nor its audit row
    (criterion 3).
    """
    async with sessions_of(request).begin() as session:
        yield session


type SessionDependency = Callable[[Request], Awaitable[Session]]


def require(lowest: Role) -> SessionDependency:
    """A dependency callable that answers 401 or 403 unless the session covers `lowest`.

    Used through `OPERATOR`, `HUNTER` and `ADMIN`, so a route names its lowest role in the
    signature (api.md's table) and no route decides for itself.
    """

    async def dependency(request: Request) -> Session:
        authenticator: DevAuthenticator = request.app.state.authenticator
        return authenticator.require(request.headers.get("authorization"), lowest)

    return dependency


ReadSession = Annotated[AsyncSession, Depends(read_session, scope="function")]
WriteSession = Annotated[AsyncSession, Depends(write_session, scope="function")]
Trigger = Annotated[ScheduleTrigger, Depends(trigger_of)]

OPERATOR = Annotated[Session, Depends(require(Role.OPERATOR))]
HUNTER = Annotated[Session, Depends(require(Role.HUNTER))]
ADMIN = Annotated[Session, Depends(require(Role.ADMIN))]

# `?from=` and `?to=`: ISO 8601 with a time zone; `aware` turns them into UTC.
FromParam = Annotated[datetime | None, Query(alias="from")]
ToParam = Annotated[datetime | None, Query()]


def aware(value: datetime | None, field: str) -> datetime | None:
    """`value` in UTC; a naive timestamp is a 422, as every time of the API is aware (api.md)."""
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise Problem(422, "request.invalid_time", detail=f"{field} must carry a time zone")
    return value.astimezone(UTC)
