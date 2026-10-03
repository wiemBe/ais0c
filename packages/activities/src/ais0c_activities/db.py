"""Database access shared by the activities."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# Built with `ais0c_storage.create_session_factory`. Every activity opens its own sessions and
# commits its own transactions.
type SessionFactory = async_sessionmaker[AsyncSession]
