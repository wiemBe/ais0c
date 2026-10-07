"""What the Investigation and Verification scenarios share: they name a recording (T-052).

A `replay` scenario holds no offense, no enrichment and no tool results: it names a recording
(`input.recording`, a directory under harness/recordings/), and the offense, the enrichment and
the event table the agent queries come from it. The scenario adds what the earlier agents of the
chain handed over (Triage's decision and claims, the evidence they cite) and what the run must
produce. The evaluation moment is, as for Triage, the offense's last update plus
`EVALUATION_DELAY`, unless `evaluated_at` says otherwise; the AQL engine's `LAST` counts back from
it and the task's window is `evaluation_window(offense, evaluated_at)`. Its optional `overlay`
removes events matching all fields of a filter and adds complete synthetic events in memory. The
recording on disk is never changed.
"""

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Final

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from ais0c_contracts import ShortText
from ais0c_harness.eval.scenario import ExecutionMode, ScenarioBase
from ais0c_harness.replay.anonymize import foreign_addresses, strings
from ais0c_harness.replay.recording import (
    MANIFEST_FILE,
    RecordedEvent,
    Recording,
    RecordingError,
    RecordingId,
    recording_of,
    recording_path,
)

EVALUATION_DELAY: Final = timedelta(minutes=5)


class EventMatch(BaseModel):
    """All explicitly set fields must equal an event for the overlay to remove it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    starttime: int | None = None
    endtime: int | None = None
    qid: int | None = None
    qidname: str | None = None
    category: int | None = None
    categoryname: str | None = None
    logsourceid: int | None = None
    logsourcename: str | None = None
    logsourcetypename: str | None = None
    devicetype: int | None = None
    sourceip: str | None = None
    destinationip: str | None = None
    sourceport: int | None = None
    destinationport: int | None = None
    username: str | None = None
    eventcount: int | None = None
    magnitude: int | None = None
    payload: str | None = None

    @model_validator(mode="after")
    def has_a_field(self) -> "EventMatch":
        if not self.model_fields_set:
            raise ValueError("an event removal filter must set at least one field")
        return self

    def matches(self, event: RecordedEvent) -> bool:
        fields = self.model_dump(exclude_unset=True)
        return all(getattr(event, name) == value for name, value in fields.items())


class RecordingOverlay(BaseModel):
    """Synthetic changes a scenario applies over an immutable recording."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    add_events: list[RecordedEvent] = Field(default_factory=list[RecordedEvent])
    remove_events: list[EventMatch] = Field(default_factory=list[EventMatch])


class ReplayInput(BaseModel):
    """The fields every replay scenario's `input` has."""

    model_config = ConfigDict(extra="forbid")

    recording: RecordingId
    objective: ShortText
    """The plan step's objective: the Orchestrator's sentence, the task's `objective`."""
    evaluated_at: AwareDatetime | None = None
    overlay: RecordingOverlay = Field(default_factory=RecordingOverlay)


class ReplayScenario[InputT: ReplayInput](ScenarioBase):
    """A scenario that replays a recording; `InputT` is the agent's `input` model."""

    input: InputT

    def execution_mode(self) -> ExecutionMode:
        return "replay"

    def recorded(self, root: Path) -> Recording:
        recording = recording_of(root.resolve(), self.input.recording)
        overlay = self.input.overlay
        if not overlay.add_events and not overlay.remove_events:
            return recording
        leaks = {
            address
            for event in overlay.add_events
            for text in strings(event.model_dump(mode="json"))
            for address in foreign_addresses(text)
        }
        if leaks:
            raise ValueError(
                "overlay events contain addresses outside the documentation ranges: "
                + ", ".join(sorted(leaks))
            )
        kept = [
            event
            for event in recording.events
            if not any(matcher.matches(event) for matcher in overlay.remove_events)
        ]
        events = sorted(
            [*kept, *overlay.add_events],
            key=lambda event: (event.starttime, event.endtime, event.model_dump_json()),
        )
        return replace(recording, events=tuple(events))

    def evaluated_moment(self, recording: Recording) -> AwareDatetime:
        return self.input.evaluated_at or (recording.offense.last_updated_time + EVALUATION_DELAY)

    def check_files(self, root: Path) -> None:
        try:
            recording = self.recorded(root)
        except RecordingError as error:
            raise ValueError(f"recording {self.input.recording}: {error}") from None
        if self.input.evaluated_at is not None and (
            self.input.evaluated_at < recording.offense.last_updated_time
        ):
            raise ValueError("evaluated_at is before the offense's last update")

    def version_parts(self, root: Path) -> list[bytes]:
        try:
            return [(recording_path(root, self.input.recording) / MANIFEST_FILE).read_bytes()]
        except OSError:
            return []

    def scripted_tools(self) -> frozenset[str]:
        return frozenset()
