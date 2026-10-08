"""`python -m ais0c_knowledge.catalog telemetry` (T-068 criterion 5): the report, the CSV and the
promise that it only reads."""

import csv
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import URL, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ais0c_knowledge.catalog.__main__ import EXIT_ERROR, EXIT_OK, main, run
from ais0c_storage import TelemetryClass
from ais0c_storage.models import CatalogLogSourceRow
from ais0c_storage.repositories import SyncedLogSource, sync_catalog_log_sources

from .catalog_helpers import WINDOWS_SECURITY

pytestmark = pytest.mark.anyio

Sessions = async_sessionmaker[AsyncSession]
REPO_ROOT = Path(__file__).resolve().parents[3]
T0 = datetime(2026, 10, 8, tzinfo=UTC)
WINDOWS = (TelemetryClass.WINDOWS,)


def environment(url: URL) -> dict[str, str]:
    return {"AIS0C_DATABASE_URL": url.render_as_string(hide_password=False)}


async def fill(sessions: Sessions) -> None:
    """Windows enabled, Windows disabled and an unclassified Universal LEEF, as in the task."""
    async with sessions.begin() as session:
        await sync_catalog_log_sources(
            session,
            [
                SyncedLogSource(1, "SRV-1.example.com", WINDOWS_SECURITY, True, WINDOWS),
                SyncedLogSource(2, "SRV-2.example.com", WINDOWS_SECURITY, False, WINDOWS),
                SyncedLogSource(3, "=SRV-3.example.com", "Universal LEEF"),
            ],
            synced_by="knowledge-sync",
            synced_at=T0,
        )


async def snapshot(sessions: Sessions) -> list[dict[str, object]]:
    async with sessions() as session:
        rows = await session.scalars(select(CatalogLogSourceRow).order_by("log_source_id"))
        return [
            {column.key: getattr(row, column.key) for column in row.__table__.columns}
            for row in rows
        ]


async def test_telemetry_report_lines(
    sessions: Sessions, database_url: URL, capsys: pytest.CaptureFixture[str]
) -> None:
    await fill(sessions)

    assert await run(["telemetry"], environment(database_url)) == EXIT_OK

    assert capsys.readouterr().out.splitlines() == [
        "type                                  enabled  excluded  classes",
        "Microsoft Windows Security Event Log        1         1  windows",
        "Universal LEEF                              1         0  UNCLASSIFIED",
        "3 log sources: 2 enabled, 1 disabled, 0 missing; 1 enabled log source unclassified.",
    ]


async def test_a_type_with_one_classified_log_source_still_shows_the_others(
    sessions: Sessions, database_url: URL, capsys: pytest.CaptureFixture[str]
) -> None:
    await fill(sessions)
    async with sessions.begin() as session:
        session.add_all(
            [
                CatalogLogSourceRow(
                    log_source_id=4,
                    name="SRV-4.example.com",
                    type_name="Universal LEEF",
                    defined=False,
                    in_scope=True,
                    telemetry_classes=["dns"],
                    updated_by="admin-1",
                    updated_at=T0,
                )
            ]
        )

    assert await run(["telemetry"], environment(database_url)) == EXIT_OK

    lines = capsys.readouterr().out.splitlines()
    assert lines[1].endswith("dns (+1 UNCLASSIFIED)")
    assert lines[-1].endswith("1 enabled log source unclassified.")


async def test_csv_has_one_row_per_log_source(
    sessions: Sessions, database_url: URL, tmp_path: Path
) -> None:
    await fill(sessions)
    target = tmp_path / "log-sources.csv"

    assert await run(["telemetry", "--csv", str(target)], environment(database_url)) == EXIT_OK

    with target.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    assert [row["log_source_id"] for row in rows] == ["1", "2", "3"]
    assert [row["qradar_enabled"] for row in rows] == ["true", "false", "true"]
    assert [row["effective_telemetry_classes"] for row in rows] == ["windows", "", ""]
    assert [row["default_telemetry_classes"] for row in rows] == ["windows", "windows", ""]
    assert [row["telemetry_classes"] for row in rows] == ["", "", ""]
    assert [row["missing"] for row in rows] == ["false", "false", "false"]
    # A name that starts like a formula stays text in a spreadsheet.
    assert rows[2]["name"] == "'=SRV-3.example.com"
    assert target.stat().st_mode & 0o077 == 0


@pytest.mark.parametrize("name", ["telemetry.csv", "packages/knowledge/out/telemetry.csv"])
def test_csv_inside_the_repo_is_refused(name: str, capsys: pytest.CaptureFixture[str]) -> None:
    target = REPO_ROOT / name

    assert main(["telemetry", "--csv", str(target)], {}) == EXIT_ERROR

    assert "inside the repository" in capsys.readouterr().err
    assert not target.exists()


def test_csv_reached_through_a_link_into_the_repo_is_refused(tmp_path: Path) -> None:
    link = tmp_path / "inside"
    link.symlink_to(REPO_ROOT, target_is_directory=True)

    assert main(["telemetry", "--csv", str(link / "telemetry.csv")], {}) == EXIT_ERROR
    assert not (REPO_ROOT / "telemetry.csv").exists()


def test_a_missing_database_url_is_a_configuration_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["telemetry"], {}) == EXIT_ERROR
    assert "AIS0C_DATABASE_URL is not set" in capsys.readouterr().err


async def test_report_writes_nothing(sessions: Sessions, database_url: URL, tmp_path: Path) -> None:
    await fill(sessions)
    before = await snapshot(sessions)

    assert await run(["telemetry"], environment(database_url)) == EXIT_OK
    assert (
        await run(["telemetry", "--csv", str(tmp_path / "out.csv")], environment(database_url))
        == EXIT_OK
    )

    assert await snapshot(sessions) == before
