"""The qradar-mcp fork lives in services/qradar-mcp (T-086, D-46).

The fork is a git subtree with its own Python 3.11 project: provenance is recorded in
services/qradar-mcp/UPSTREAM and the ais0c tools (ruff, pyright, pytest) leave it alone.
"""

import re
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FORK = ROOT / "services/qradar-mcp"
HASH = re.compile(r"[0-9a-f]{40}")
FORK_DIR = "services/qradar-mcp"


def test_the_fork_tree_matches_its_upstream_file() -> None:
    lines = [
        line
        for line in (FORK / "UPSTREAM").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]

    assert len(lines) == 2
    assert HASH.fullmatch(lines[0].removeprefix("fork_commit: "))
    assert lines[0].startswith("fork_commit: ")
    upstream_url, _, upstream_commit = lines[1].removeprefix("upstream: ").partition(" ")
    assert upstream_url == "https://github.com/IBM/qradar-mcp"
    assert HASH.fullmatch(upstream_commit)
    license_text = (FORK / "LICENSE").read_text(encoding="utf-8")
    assert "Apache License" in license_text
    assert "Version 2.0" in license_text
    assert (FORK / "NOTICE").is_file()


def test_tooling_excludes_the_fork() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]

    assert FORK_DIR in config["ruff"]["extend-exclude"]
    assert FORK_DIR in config["pyright"]["exclude"]
    assert f"--ignore={FORK_DIR}" in config["pytest"]["addopts"]


def test_ais0c_code_does_not_import_the_fork_modules() -> None:
    done = subprocess.run(
        [  # noqa: S607
            "git",
            "grep",
            "-n",
            "-E",
            r"^(from|import) (fork|tools|utils|client|server)\b",
            "--",
            "packages",
            "services/api",
            "services/worker",
            "services/mcp-gateway",
            "harness",
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
        text=True,
    )

    assert done.returncode == 1, done.stdout
