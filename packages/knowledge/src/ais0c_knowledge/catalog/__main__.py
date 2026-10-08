"""The Analysis Catalog on the command line (T-068, decision T-95):

    uv run python -m ais0c_knowledge.catalog telemetry [--csv PATH]

`telemetry` lists the log sources of the catalog by type: how many QRadar has enabled, how
many are left out (disabled in QRadar or no longer listed) and the telemetry classes they carry
(`effective_telemetry_classes`). A type whose enabled log sources carry no class shows
`UNCLASSIFIED`; an admin assigns those classes in the Analysis Catalog. With `--csv PATH` it
also writes one row per log source for the classification work. The CSV holds the installation's
log source names, so the command refuses a PATH inside a git checkout. In a CSV the classes of
a cell are separated by `;`.

The database is `AIS0C_DATABASE_URL`. The command only reads.

Exit status: 0 on success, 2 on a usage or configuration error.
"""

import argparse
import asyncio
import csv
import os
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

from sqlalchemy.exc import SQLAlchemyError

from ais0c_storage import (
    StorageError,
    TelemetryClass,
    create_engine,
    create_session_factory,
    database_url,
)
from ais0c_storage.models import CatalogLogSourceRow
from ais0c_storage.repositories import effective_telemetry_classes, list_catalog_log_sources

EXIT_OK: Final = 0
EXIT_ERROR: Final = 2
UNCLASSIFIED: Final = "UNCLASSIFIED"
CSV_COLUMNS: Final = (
    "log_source_id",
    "name",
    "type_name",
    "qradar_enabled",
    "missing",
    "telemetry_classes",
    "default_telemetry_classes",
    "effective_telemetry_classes",
)
# A spreadsheet reads a cell starting with one of these as a formula.
_FORMULA_STARTS: Final = ("=", "+", "-", "@", "\t", "\r")
_HEADER: Final = ("type", "enabled", "excluded", "classes")


class UsageError(Exception):
    """The arguments or the environment are wrong; the message is for the operator."""


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    """Run the command line; returns the exit status."""
    return asyncio.run(run(argv, environ))


async def run(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ais0c_knowledge.catalog",
        description="The Analysis Catalog (architecture §9, decision T-95).",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    telemetry = commands.add_parser(
        "telemetry", help="list the catalog's log sources by type, with their telemetry classes"
    )
    telemetry.add_argument("--csv", type=Path, help="also write one row per log source here")
    args = parser.parse_args(argv)
    try:
        if args.csv is not None:
            _check_csv_path(args.csv)
        rows = await _read(os.environ if environ is None else environ)
        if args.csv is not None:
            _write_csv(args.csv, rows)
    except (UsageError, StorageError, SQLAlchemyError, OSError) as error:
        # SQLAlchemy's messages carry the statement and the host, not the password.
        print(f"error: {_describe(error)}", file=sys.stderr)
        return EXIT_ERROR
    print("\n".join(report_lines(rows)))
    return EXIT_OK


def report_lines(rows: Sequence[CatalogLogSourceRow]) -> list[str]:
    """The `telemetry` report: a header, one line per type (most enabled log sources first)
    and a summary."""
    types: set[str] = set()
    enabled: dict[str, int] = defaultdict(int)
    excluded: dict[str, int] = defaultdict(int)
    classes: dict[str, set[TelemetryClass]] = defaultdict(set)
    unclassified: dict[str, int] = defaultdict(int)
    for row in rows:
        types.add(row.type_name)
        if not _is_enabled(row):
            excluded[row.type_name] += 1
            continue
        enabled[row.type_name] += 1
        found = effective_telemetry_classes(row)
        classes[row.type_name] |= found
        if not found:
            unclassified[row.type_name] += 1
    lines: list[tuple[str, str, str, str]] = [_HEADER]
    for type_name in sorted(types, key=lambda name: (-enabled[name], name)):
        lines.append(
            (
                type_name,
                str(enabled[type_name]),
                str(excluded[type_name]),
                _classes_cell(enabled[type_name], classes[type_name], unclassified[type_name]),
            )
        )
    widths = [max(len(line[column]) for line in lines) for column in range(len(_HEADER))]
    table = [
        f"{line[0]:<{widths[0]}}  {line[1]:>{widths[1]}}  {line[2]:>{widths[2]}}  {line[3]}"
        for line in lines
    ]
    missing = sum(row.missing_since is not None for row in rows)
    disabled = sum(row.missing_since is None and not row.qradar_enabled for row in rows)
    open_count = sum(unclassified.values())
    noun = "log source" if open_count == 1 else "log sources"
    summary = (
        f"{len(rows)} log sources: {sum(enabled.values())} enabled, {disabled} disabled, "
        f"{missing} missing; {open_count} enabled {noun} unclassified."
    )
    return [*table, summary]


def _classes_cell(enabled: int, classes: set[TelemetryClass], unclassified: int) -> str:
    if enabled == 0:
        return "-"
    names = ",".join(telemetry.value for telemetry in TelemetryClass if telemetry in classes)
    if not names:
        return UNCLASSIFIED
    # Another log source of the type still needs a class.
    return f"{names} (+{unclassified} {UNCLASSIFIED})" if unclassified else names


def _is_enabled(row: CatalogLogSourceRow) -> bool:
    return row.qradar_enabled and row.missing_since is None


async def _read(environ: Mapping[str, str]) -> list[CatalogLogSourceRow]:
    engine = create_engine(database_url(environ))
    try:
        async with create_session_factory(engine)() as session:
            return await list_catalog_log_sources(session)
    finally:
        await engine.dispose()


def _check_csv_path(path: Path) -> None:
    """Refuses a PATH inside a git checkout: the CSV holds the installation's data."""
    target = path.resolve()
    for root in _checkouts():
        if target.is_relative_to(root):
            raise UsageError(f"{path} is inside the repository {root}; write the CSV elsewhere")


def _checkouts() -> set[Path]:
    """The roots of the git checkouts around the working directory and this module."""
    found: set[Path] = set()
    for start in (Path.cwd(), Path(__file__).resolve().parent):
        for candidate in (start, *start.parents):
            if (candidate / ".git").exists():
                found.add(candidate.resolve())
                break
    return found


def _write_csv(path: Path, rows: Sequence[CatalogLogSourceRow]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with open(descriptor, "w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            assigned = row.telemetry_classes
            writer.writerow(
                [
                    row.log_source_id,
                    _cell(row.name),
                    _cell(row.type_name),
                    str(row.qradar_enabled).lower(),
                    str(row.missing_since is not None).lower(),
                    "" if assigned is None else ";".join(assigned),
                    ";".join(row.default_telemetry_classes),
                    ";".join(
                        telemetry.value
                        for telemetry in TelemetryClass
                        if telemetry in effective_telemetry_classes(row)
                    ),
                ]
            )


def _cell(text: str) -> str:
    return f"'{text}" if text.startswith(_FORMULA_STARTS) else text


def _describe(error: Exception) -> str:
    return f"{type(error).__name__}: {error}" if not isinstance(error, UsageError) else str(error)


if __name__ == "__main__":
    sys.exit(main())
