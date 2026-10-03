"""Scenario model, loader and the deterministic expansion into labelled events.

A scenario is a YAML file under ``harness/scenarios/``. It is a list of steps;
a step emits one or more blocks of events of a single :class:`~.templates.LogKind`.
A step carries the ground truth the eval harness scores against: whether its
events are malicious and, if so, the ATT&CK technique they instantiate.

Expansion is deterministic. Given the same scenario, seed and base time, the
same events and labels come out byte-for-byte, because:

* one :class:`random.Random` is seeded once and consumed in a fixed order
  (steps in file order, blocks in file order, then each block's timing draws
  before its per-event template draws);
* event times are ``base_time + offset``; offsets are either a fixed interval
  or seeded jitter drawn in that same fixed order;
* the output is sorted by ``(when, sequence)``, and the sequence is the fixed
  generation index, so equal timestamps keep their generation order.

The ``sequence`` index also names the event (``<scenario>-<seed>-<n>``), so an
event's identity never depends on the order it happens to be sent in.
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from .templates import TEMPLATES, Event, LogKind

#: Scenario definitions ship with the package.
SCENARIOS_DIR = Path(__file__).resolve().parents[3] / "scenarios"

_STEP_KEYS = {"id", "malicious", "technique", "events"}
_BLOCK_KEYS = {"kind", "count", "start", "interval", "spread", "params"}


class ScenarioError(ValueError):
    """Raised when a scenario file is malformed."""


@dataclass(frozen=True)
class EventBlock:
    """A run of ``count`` events of one kind, placed on the timeline.

    ``interval`` spaces events evenly; ``spread`` scatters them with seeded
    jitter across ``[start, start + spread)``. Exactly one of the two may be
    given; with neither, every event lands at ``start``.
    """

    kind: LogKind
    count: int
    start: float
    interval: float | None
    spread: float | None
    params: Mapping[str, Any]

    def offsets(self, rng: random.Random) -> list[float]:
        """Second offsets from the base time, drawn in a fixed order."""
        if self.spread is not None:
            # Draw first, then sort: the jitter is deterministic and the block's
            # events still come out in time order.
            return sorted(self.start + rng.uniform(0.0, self.spread) for _ in range(self.count))
        step = self.interval or 0.0
        return [self.start + index * step for index in range(self.count)]


@dataclass(frozen=True)
class ScenarioStep:
    """One labelled stage of a scenario."""

    id: str
    malicious: bool
    technique: str | None
    events: tuple[EventBlock, ...]


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    steps: tuple[ScenarioStep, ...]


@dataclass(frozen=True)
class Label:
    """Ground truth for one generated event, written to ``.labels.jsonl``."""

    event_id: str
    time: datetime
    scenario: str
    step: str
    kind: LogKind
    technique: str | None
    malicious: bool

    def as_json(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "time": self.time.isoformat(),
            "scenario": self.scenario,
            "step": self.step,
            "kind": self.kind.value,
            "technique": self.technique,
            "malicious": self.malicious,
        }


@dataclass(frozen=True)
class GeneratedEvent:
    """A structured event paired with its label, ready to render."""

    event: Event
    label: Label


# --- loading ------------------------------------------------------------------


def _require_mapping(value: object, where: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ScenarioError(f"{where} must be a mapping")
    return {str(key): item for key, item in value.items()}


def _unknown(keys: Iterable[str], allowed: set[str], where: str) -> None:
    extra = set(keys) - allowed
    if extra:
        raise ScenarioError(f"{where}: unknown keys {sorted(extra)}")


def _parse_block(raw: object, where: str) -> EventBlock:
    data = _require_mapping(raw, where)
    _unknown(data, _BLOCK_KEYS, where)
    kind_value = data.get("kind")
    if not isinstance(kind_value, str):
        raise ScenarioError(f"{where}: 'kind' is required and must be a string")
    try:
        kind = LogKind(kind_value)
    except ValueError:
        raise ScenarioError(
            f"{where}: unknown kind {kind_value!r}; one of {[k.value for k in LogKind]}"
        ) from None
    count = data.get("count", 1)
    if not isinstance(count, int) or isinstance(count, bool) or count < 1:
        raise ScenarioError(f"{where}: 'count' must be a positive integer")
    if "interval" in data and "spread" in data:
        raise ScenarioError(f"{where}: give at most one of 'interval' and 'spread'")
    start = _opt_float(data.get("start", 0.0), f"{where}.start")
    return EventBlock(
        kind=kind,
        count=count,
        start=start if start is not None else 0.0,
        interval=_opt_float(data.get("interval"), f"{where}.interval"),
        spread=_opt_float(data.get("spread"), f"{where}.spread"),
        params=_require_mapping(data.get("params", {}), f"{where}.params"),
    )


def _opt_float(value: object, where: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScenarioError(f"{where} must be a number")
    return float(value)


def _parse_step(raw: object, where: str) -> ScenarioStep:
    data = _require_mapping(raw, where)
    _unknown(data, _STEP_KEYS, where)
    step_id = data.get("id")
    if not isinstance(step_id, str) or not step_id:
        raise ScenarioError(f"{where}: 'id' is required")
    malicious = data.get("malicious", False)
    if not isinstance(malicious, bool):
        raise ScenarioError(f"{where}: 'malicious' must be a boolean")
    technique = data.get("technique")
    if technique is not None and not isinstance(technique, str):
        raise ScenarioError(f"{where}: 'technique' must be a string or absent")
    events = data.get("events")
    if not isinstance(events, list) or not events:
        raise ScenarioError(f"{where}: 'events' must be a non-empty list")
    blocks = tuple(_parse_block(block, f"{where}.events[{i}]") for i, block in enumerate(events))
    return ScenarioStep(id=step_id, malicious=malicious, technique=technique, events=blocks)


def parse_scenario(raw: object) -> Scenario:
    """Validate a parsed YAML document and build a :class:`Scenario`."""
    data = _require_mapping(raw, "scenario")
    _unknown(data, {"name", "description", "steps"}, "scenario")
    name = data.get("name")
    if not isinstance(name, str) or not name:
        raise ScenarioError("scenario: 'name' is required")
    description = data.get("description", "")
    if not isinstance(description, str):
        raise ScenarioError("scenario: 'description' must be a string")
    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ScenarioError("scenario: 'steps' must be a non-empty list")
    step_ids = [s.get("id") if isinstance(s, dict) else None for s in steps]
    if len(set(step_ids)) != len(step_ids):
        raise ScenarioError("scenario: step ids must be unique")
    parsed = tuple(_parse_step(step, f"steps[{i}]") for i, step in enumerate(steps))
    return Scenario(name=name, description=description, steps=parsed)


def load_scenario(name: str, scenarios_dir: Path = SCENARIOS_DIR) -> Scenario:
    """Load ``<scenarios_dir>/<name>.yaml`` and validate it."""
    path = scenarios_dir / f"{name}.yaml"
    if not path.is_file():
        available = sorted(p.stem for p in scenarios_dir.glob("*.yaml"))
        raise ScenarioError(f"no scenario {name!r} in {scenarios_dir}; available: {available}")
    scenario = parse_scenario(yaml.safe_load(path.read_text(encoding="utf-8")))
    if scenario.name != name:
        raise ScenarioError(f"{path}: name {scenario.name!r} does not match file name {name!r}")
    return scenario


def available_scenarios(scenarios_dir: Path = SCENARIOS_DIR) -> list[str]:
    return sorted(p.stem for p in scenarios_dir.glob("*.yaml"))


# --- expansion ----------------------------------------------------------------


def generate(scenario: Scenario, *, seed: int, base_time: datetime) -> list[GeneratedEvent]:
    """Expand a scenario into labelled events, sorted by time.

    The order of ``rng`` consumption is fixed by the loop below, so the result
    depends only on ``scenario``, ``seed`` and ``base_time``.
    """
    rng = random.Random(seed)  # noqa: S311 - reproducible test data, not cryptographic use
    generated: list[tuple[datetime, int, GeneratedEvent]] = []
    sequence = 0
    for step in scenario.steps:
        for block in step.events:
            builder = TEMPLATES[block.kind]
            offsets = block.offsets(rng)
            for offset in offsets:
                when = base_time + timedelta(seconds=offset)
                event = builder(rng, block.params, when)
                label = Label(
                    event_id=f"{scenario.name}-{seed}-{sequence:05d}",
                    time=when,
                    scenario=scenario.name,
                    step=step.id,
                    kind=block.kind,
                    technique=step.technique,
                    malicious=step.malicious,
                )
                generated.append((when, sequence, GeneratedEvent(event=event, label=label)))
                sequence += 1
    generated.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in generated]


def labels_jsonl(events: Sequence[GeneratedEvent]) -> str:
    """Render labels as newline-terminated JSON, one event per line."""
    return "".join(
        json.dumps(generated.label.as_json(), ensure_ascii=False, sort_keys=True) + "\n"
        for generated in events
    )
