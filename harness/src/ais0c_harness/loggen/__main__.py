"""Command-line entry point for the synthetic log generator.

    uv run python -m ais0c_harness.loggen run \\
        --scenario s2-dcsync --target 192.0.2.10:514 --seed 1 --speed 60

``run`` expands a scenario deterministically, writes an event log and a label
file, and (unless ``--dry-run``) sends the events over syslog. Every rendered
line is scanned for non-synthetic data before anything is written or sent, so
the generator fails closed if a real address, domain, account or host ever
reaches the wire (AGENTS.md hard rule 6).
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from . import synthetic
from .formats import render
from .scenario import (
    SCENARIOS_DIR,
    GeneratedEvent,
    ScenarioError,
    available_scenarios,
    generate,
    labels_jsonl,
    load_scenario,
)
from .sender import open_target, parse_target

#: Fixed anchor for ``--dry-run`` when no ``--base-time`` is given, so a dry run
#: is reproducible from the seed alone. A live run defaults to the current time.
DRY_RUN_BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class RunResult:
    events: list[GeneratedEvent]
    lines: list[str]
    events_path: Path
    labels_path: Path


def _scan(line: str, event_id: str) -> str:
    """Reject any non-synthetic literal before the line leaves the process."""
    synthetic.assert_text_is_synthetic(line, where=event_id)
    return line


def build(
    *,
    scenario_name: str,
    seed: int,
    base_time: datetime,
    scenarios_dir: Path = SCENARIOS_DIR,
) -> tuple[list[GeneratedEvent], list[str]]:
    """Expand a scenario and render every event, scanning each line."""
    scenario = load_scenario(scenario_name, scenarios_dir)
    events = generate(scenario, seed=seed, base_time=base_time)
    lines = [_scan(render(g.event), g.label.event_id) for g in events]
    return events, lines


def write_artifacts(
    out_dir: Path, scenario_name: str, seed: int, events: list[GeneratedEvent], lines: list[str]
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    events_path = out_dir / f"{scenario_name}.{seed}.events.log"
    labels_path = out_dir / f"{scenario_name}.{seed}.labels.jsonl"
    events_path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    labels_path.write_text(labels_jsonl(events), encoding="utf-8")
    return events_path, labels_path


def _send(
    events: list[GeneratedEvent],
    lines: list[str],
    *,
    host: str,
    port: int,
    protocol: str,
    speed: float,
    base_time: datetime,
) -> None:
    """Send rendered lines, pacing them by the scenario timeline / ``speed``."""
    first = (events[0].label.time - base_time).total_seconds() if events else 0.0
    start = time.monotonic()
    with open_target(host, port, protocol) as target:
        for generated, line in zip(events, lines, strict=True):
            offset = (generated.label.time - base_time).total_seconds()
            due = start + (offset - first) / speed
            delay = due - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            target.send(line)


def run(args: argparse.Namespace) -> int:
    base_time: datetime = args.base_time or (
        DRY_RUN_BASE_TIME if args.dry_run else datetime.now(UTC)
    )
    try:
        events, lines = build(
            scenario_name=args.scenario,
            seed=args.seed,
            base_time=base_time,
            scenarios_dir=args.scenarios_dir,
        )
    except ScenarioError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    events_path, labels_path = write_artifacts(
        args.out_dir, args.scenario, args.seed, events, lines
    )
    print(
        f"{args.scenario}: {len(events)} events, seed {args.seed}, "
        f"base {base_time.isoformat()}\n  events: {events_path}\n  labels: {labels_path}"
    )

    if args.dry_run:
        print("dry run: nothing sent")
        return 0

    host, port = parse_target(args.target)
    if args.speed <= 0:
        print("error: --speed must be greater than 0", file=sys.stderr)
        return 2
    _send(
        events,
        lines,
        host=host,
        port=port,
        protocol=args.protocol,
        speed=args.speed,
        base_time=base_time,
    )
    print(f"sent {len(events)} events to {host}:{port} over {args.protocol}")
    return 0


def list_scenarios(args: argparse.Namespace) -> int:
    for name in available_scenarios(args.scenarios_dir):
        print(name)
    return 0


def _iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("--base-time must include a timezone offset (e.g. +00:00)")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ais0c_harness.loggen")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="generate and send a scenario")
    run_parser.add_argument("--scenario", required=True, help="scenario name (without .yaml)")
    run_parser.add_argument(
        "--target", help="syslog destination host:port (required unless --dry-run)"
    )
    run_parser.add_argument("--seed", type=int, default=0, help="RNG seed (default: 0)")
    run_parser.add_argument(
        "--speed",
        type=float,
        default=1.0,
        help="time compression: 60 sends a 1-hour scenario in 1 minute (default: 1.0)",
    )
    run_parser.add_argument(
        "--protocol",
        choices=("udp", "tcp"),
        default="tcp",
        help="syslog transport (default: tcp; udp can drop events under a fast burst)",
    )
    run_parser.add_argument(
        "--dry-run", action="store_true", help="write the files but send nothing"
    )
    run_parser.add_argument(
        "--out-dir", type=Path, default=Path("."), help="where to write the event and label files"
    )
    run_parser.add_argument(
        "--base-time",
        type=_iso,
        default=None,
        help="ISO-8601 base time (default: a fixed anchor for --dry-run, now for a live run)",
    )
    run_parser.add_argument(
        "--scenarios-dir", type=Path, default=SCENARIOS_DIR, help="scenario directory"
    )
    run_parser.set_defaults(func=run)

    list_parser = subparsers.add_parser("list", help="list available scenarios")
    list_parser.add_argument(
        "--scenarios-dir", type=Path, default=SCENARIOS_DIR, help="scenario directory"
    )
    list_parser.set_defaults(func=list_scenarios)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "func", None) is run and not args.dry_run and not args.target:
        parser.error("--target is required unless --dry-run is given")
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
