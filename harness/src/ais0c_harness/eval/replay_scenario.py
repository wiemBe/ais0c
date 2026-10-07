"""What the Investigation and Verification scenarios share: they name a recording (T-052).

A `replay` scenario holds no offense, no enrichment and no tool results: it names a recording
(`input.recording`, a directory under harness/recordings/), and the offense, the enrichment and
the event table the agent queries come from it. The scenario adds what the earlier agents of the
chain handed over (Triage's decision and claims, the evidence they cite) and what the run must
produce. The evaluation moment is, as for Triage, the offense's last update plus
`EVALUATION_DELAY`, unless `evaluated_at` says otherwise; the AQL engine's `LAST` counts back from
it and the task's window is `evaluation_window(offense, evaluated_at)`.
"""

from datetime import timedelta
from pathlib import Path
from typing import Final

from pydantic import AwareDatetime, BaseModel, ConfigDict

from ais0c_contracts import ShortText
from ais0c_harness.eval.scenario import ExecutionMode, ScenarioBase
from ais0c_harness.replay.recording import (
    MANIFEST_FILE,
    Recording,
    RecordingError,
    RecordingId,
    recording_of,
    recording_path,
)

EVALUATION_DELAY: Final = timedelta(minutes=5)


class ReplayInput(BaseModel):
    """The fields every replay scenario's `input` has."""

    model_config = ConfigDict(extra="forbid")

    recording: RecordingId
    objective: ShortText
    """The plan step's objective: the Orchestrator's sentence, the task's `objective`."""
    evaluated_at: AwareDatetime | None = None


class ReplayScenario[InputT: ReplayInput](ScenarioBase):
    """A scenario that replays a recording; `InputT` is the agent's `input` model."""

    input: InputT

    def execution_mode(self) -> ExecutionMode:
        return "replay"

    def recorded(self, root: Path) -> Recording:
        return recording_of(root.resolve(), self.input.recording)

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
