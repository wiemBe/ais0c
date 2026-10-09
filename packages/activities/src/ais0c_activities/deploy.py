"""Deployment checks that need the storage and knowledge packages.

The worker service imports only activities and workflows, so database migrations, platform
flags and the approved skill registry cross that package boundary here.
"""

import asyncio
from pathlib import Path

from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from ais0c_knowledge.skills import load_skills
from ais0c_storage import (
    PlatformFlag,
    create_engine,
    create_session_factory,
    create_sync_engine,
)
from ais0c_storage.migrate import alembic_config, upgrade
from ais0c_storage.repositories import get_platform_flag


def migrate_to_head(database_url: str) -> str:
    """Upgrade the application database to the newest revision; return that revision."""
    engine = create_sync_engine(database_url)
    try:
        with engine.begin() as connection:
            upgrade(connection)
    finally:
        engine.dispose()
    return _head_revision()


def database_revision(database_url: str) -> tuple[str | None, str]:
    """Return the current database revision, or ``None``, and the newest revision."""
    engine = create_sync_engine(database_url)
    try:
        with engine.connect() as connection:
            current = MigrationContext.configure(connection).get_current_revision()
    finally:
        engine.dispose()
    return current, _head_revision()


def writes_enabled(database_url: str) -> bool:
    """Return the T-23 kill switch flag; a flag without a row is off."""
    return asyncio.run(_writes_enabled(database_url))


async def _writes_enabled(database_url: str) -> bool:
    engine = create_engine(database_url)
    try:
        sessions = create_session_factory(engine)
        async with sessions() as session:
            row = await get_platform_flag(session, PlatformFlag.WRITES_ENABLED)
        return False if row is None else row.enabled
    finally:
        await engine.dispose()


def approved_skill_count(root: Path) -> int:
    """Return the number of valid approved skills under the deployment root."""
    return len(load_skills(root / "skills", mode="prod"))


def _head_revision() -> str:
    revision = ScriptDirectory.from_config(alembic_config()).get_current_head()
    if revision is None:
        raise RuntimeError("the migration tree has no head revision")
    return revision
