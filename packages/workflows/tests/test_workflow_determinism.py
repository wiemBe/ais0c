"""Workflow code makes no network, file, clock or random calls (criterion 10).

Temporal's sandbox rejects such calls when a test reaches them; this check also covers code no
test reaches. Time comes from `workflow.now()`, everything else from activities.
"""

import ast
from pathlib import Path

import pytest

import ais0c_workflows

PACKAGE = Path(ais0c_workflows.__file__).parent

# Modules whose use means I/O, a clock or randomness.
FORBIDDEN_MODULES = frozenset(
    {
        "aiohttp",
        "asyncpg",
        "concurrent",
        "glob",
        "grpc",
        "http",
        "httpx",
        "io",
        "multiprocessing",
        "os",
        "pathlib",
        "psycopg",
        "random",
        "requests",
        "secrets",
        "select",
        "selectors",
        "shutil",
        "smtplib",
        "socket",
        "sqlalchemy",
        "sqlite3",
        "ssl",
        "subprocess",
        "tempfile",
        "threading",
        "time",
        "urllib",
        "uuid",
    }
)
FORBIDDEN_BUILTINS = frozenset({"open", "input"})
CLOCK_TYPES = frozenset({"datetime", "date"})
CLOCK_METHODS = frozenset({"now", "utcnow", "today"})
# Not deterministic in workflows; `workflow.wait` and `workflow.as_completed` are.
ASYNCIO_FORBIDDEN = frozenset({"wait", "as_completed"})


def _dotted(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_dotted(node.value)}.{node.attr}"
    return ""


def forbidden_uses(source: str) -> list[str]:
    """Forbidden imports and calls in `source`, as `line: description`."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import | ast.ImportFrom):
            modules = (
                [alias.name for alias in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
            )
            found += [
                f"{node.lineno}: import {module}"
                for module in modules
                if module.split(".")[0] in FORBIDDEN_MODULES
            ]
            continue
        if not isinstance(node, ast.Call):
            continue
        name = _dotted(node.func)
        parts = name.split(".")
        if name in FORBIDDEN_BUILTINS:
            found.append(f"{node.lineno}: {name}()")
        elif len(parts) >= 2 and parts[-2] in CLOCK_TYPES and parts[-1] in CLOCK_METHODS:
            found.append(f"{node.lineno}: {name}()")
        elif parts[:1] == ["asyncio"] and parts[-1] in ASYNCIO_FORBIDDEN:
            found.append(f"{node.lineno}: {name}()")
    return found


@pytest.mark.parametrize(
    "module", sorted(PACKAGE.rglob("*.py")), ids=lambda path: path.relative_to(PACKAGE).as_posix()
)
def test_workflow_module_has_no_io_clock_or_randomness(module: Path) -> None:
    assert forbidden_uses(module.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize(
    ("source", "finding"),
    [
        ("import random", "1: import random"),
        ("from uuid import uuid4", "1: import uuid"),
        ("import urllib.request", "1: import urllib.request"),
        ("from pathlib import Path", "1: import pathlib"),
        ("x = datetime.now()", "1: datetime.now()"),
        ("x = datetime.datetime.utcnow()", "1: datetime.datetime.utcnow()"),
        ("x = date.today()", "1: date.today()"),
        ("open('notes.txt')", "1: open()"),
        ("await asyncio.wait(tasks)", "1: asyncio.wait()"),
    ],
)
def test_the_check_finds_forbidden_uses(source: str, finding: str) -> None:
    assert forbidden_uses(source) == [finding]


def test_the_check_allows_workflow_time_and_timers() -> None:
    source = "now = workflow.now()\nawait workflow.sleep(1)\nawait asyncio.sleep(1)\n"
    assert forbidden_uses(source) == []
