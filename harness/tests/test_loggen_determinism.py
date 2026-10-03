"""Acceptance criterion 2: the same scenario and seed always produce the same
event sequence. The check runs the CLI with ``--dry-run`` twice and compares the
written event log and label file byte-for-byte.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ais0c_harness.loggen.__main__ import main
from ais0c_harness.loggen.scenario import available_scenarios

# A fixed base time so the whole file (timestamps included) is reproducible from
# the seed alone; --dry-run defaults to this anchor, but pin it for clarity.
BASE_TIME = "2026-02-01T00:00:00+00:00"


def dry_run(out_dir: Path, scenario: str, seed: int) -> tuple[str, str]:
    code = main(
        [
            "run",
            "--scenario",
            scenario,
            "--seed",
            str(seed),
            "--dry-run",
            "--base-time",
            BASE_TIME,
            "--out-dir",
            str(out_dir),
        ]
    )
    assert code == 0
    events = (out_dir / f"{scenario}.{seed}.events.log").read_text(encoding="utf-8")
    labels = (out_dir / f"{scenario}.{seed}.labels.jsonl").read_text(encoding="utf-8")
    return events, labels


@pytest.mark.parametrize("scenario", available_scenarios())
def test_same_scenario_and_seed_are_byte_identical(scenario: str, tmp_path: Path) -> None:
    first = dry_run(tmp_path / "a", scenario, seed=1)
    second = dry_run(tmp_path / "b", scenario, seed=1)
    assert first == second
    assert first[0].strip(), "the scenario produced no events"


@pytest.mark.parametrize("scenario", available_scenarios())
def test_different_seed_changes_the_sequence(scenario: str, tmp_path: Path) -> None:
    events_1, _ = dry_run(tmp_path / "s1", scenario, seed=1)
    events_2, _ = dry_run(tmp_path / "s2", scenario, seed=2)
    assert events_1 != events_2


def test_dry_run_without_base_time_is_still_reproducible(tmp_path: Path) -> None:
    # Without --base-time, a dry run falls back to a fixed anchor, so two runs
    # with no base time still match (the determinism guarantee needs only seed).
    scenario = available_scenarios()[0]
    first_dir, second_dir = tmp_path / "x", tmp_path / "y"
    for out in (first_dir, second_dir):
        assert (
            main(["run", "--scenario", scenario, "--seed", "4", "--dry-run", "--out-dir", str(out)])
            == 0
        )
    name = f"{scenario}.4.events.log"
    assert (first_dir / name).read_bytes() == (second_dir / name).read_bytes()


def test_dry_run_sends_nothing_and_needs_no_target(tmp_path: Path) -> None:
    # --dry-run must not require --target (it writes files instead of sending).
    assert (
        main(
            ["run", "--scenario", available_scenarios()[0], "--dry-run", "--out-dir", str(tmp_path)]
        )
        == 0
    )
