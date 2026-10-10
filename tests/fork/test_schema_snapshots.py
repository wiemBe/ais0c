# SPDX-License-Identifier: Apache-2.0
"""Acceptance criterion 6: input and output schemas are exported and kept in sync."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from qradar_mcp.fork.schema_export import export_snapshots, main, snapshot_differences

from .conftest import SNAPSHOT_DIR

REGENERATE = "python -m qradar_mcp.fork.schema_export"


def test_committed_snapshots_match_the_server() -> None:
    problems = snapshot_differences(SNAPSHOT_DIR)
    assert problems == [], (
        f"Tool contracts changed: {problems}. Run `{REGENERATE}` from the repository root, "
        "review the diff and commit it with the change."
    )


def test_each_tool_snapshot_has_both_schemas() -> None:
    for relative, content in export_snapshots().items():
        if relative.startswith("tools/"):
            snapshot = json.loads(content)
            assert snapshot["inputSchema"]["type"] == "object", relative
            assert snapshot["outputSchema"]["type"] == "object", relative
            assert snapshot["profiles"], relative


def _copy(tmp_path: Path) -> Path:
    target = tmp_path / "snapshots"
    shutil.copytree(SNAPSHOT_DIR, target)
    return target


def test_check_fails_when_a_schema_changed_without_a_snapshot_update(tmp_path: Path) -> None:
    snapshots = _copy(tmp_path)
    path = snapshots / "tools" / "get_offense.json"
    edited = json.loads(path.read_text())
    edited["outputSchema"]["required"].remove("rules")
    path.write_text(json.dumps(edited, indent=2, sort_keys=True) + "\n")

    assert snapshot_differences(snapshots) == ["changed: tools/get_offense.json"]
    assert main(["--check", "--dir", str(snapshots)]) == 1


def test_check_reports_missing_and_stale_files(tmp_path: Path) -> None:
    snapshots = _copy(tmp_path)
    (snapshots / "tools" / "list_rules.json").unlink()
    (snapshots / "tools" / "set_offense_status.json").write_text("{}\n")

    assert snapshot_differences(snapshots) == [
        "missing: tools/list_rules.json",
        "stale: tools/set_offense_status.json",
    ]


def test_regeneration_restores_a_clean_state(tmp_path: Path) -> None:
    snapshots = _copy(tmp_path)
    (snapshots / "tools" / "list_rules.json").unlink()
    (snapshots / "tools" / "stale.json").write_text("{}\n")

    assert main(["--dir", str(snapshots)]) == 0
    assert main(["--check", "--dir", str(snapshots)]) == 0
