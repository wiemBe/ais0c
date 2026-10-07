"""A recording: one closed lab offense's inputs and the events around it (T-052 criterion 2,
decision T-70).

A recording is a directory under `harness/recordings/` with these files:

- `manifest.json`: `RecordingManifest`: the recording's ID, the offense, the event window, the
  row count, the columns, when it was recorded, the gateway and fork versions, and the sha256
  and size of every other file;
- `offense.json`: the offense as the case workflow's offense source reads it (`OffenseSnapshot`);
- `enrichment.json`: the deterministic enrichment against the dev database's catalog
  (`EnrichmentContext`);
- `tools.json`: the read tools' results Triage and Investigation use, one entry per call
  (`RecordedCall`): `get_offense`, `get_rule`, `get_log_source`;
- `events.jsonl.gz`: the event table, one JSON object per line with the columns of
  `EVENT_COLUMNS` (`RecordedEvent`), oldest first;
- `audits.json`: the audit queries: each query with the rows the lab returned for it, so a test
  can show the replay engine returns the same (`AuditQuery`).

Every address is a documentation address (RFC 5737, `2001:db8::/32`) and every lab host or
domain name is under `example.com` (anonymize.py); the mapping is not kept. `load_recording`
reads the files through these models and refuses a recording whose file was changed or whose
row count is wrong, naming the file.
"""

import gzip
import hashlib
import io
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from functools import cache, cached_property
from pathlib import Path
from typing import Annotated, Final

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    ValidationError,
)

from ais0c_contracts import EnrichmentContext, OffenseSnapshot, TimeWindow, ToolResult

SCHEMA_VERSION: Final = 1
RECORDINGS_DIR: Final = "harness/recordings"
MANIFEST_FILE: Final = "manifest.json"
OFFENSE_FILE: Final = "offense.json"
ENRICHMENT_FILE: Final = "enrichment.json"
TOOLS_FILE: Final = "tools.json"
EVENTS_FILE: Final = "events.jsonl.gz"
AUDITS_FILE: Final = "audits.json"
DATA_FILES: Final = (OFFENSE_FILE, ENRICHMENT_FILE, TOOLS_FILE, EVENTS_FILE, AUDITS_FILE)

# The recorded columns, in the order of the recording's query. A query's QIDNAME(qid) reads
# `qidname`, and so on: the recording keeps the function results as columns.
EVENT_COLUMNS: Final = (
    "starttime",
    "endtime",
    "qid",
    "qidname",
    "category",
    "categoryname",
    "logsourceid",
    "logsourcename",
    "logsourcetypename",
    "devicetype",
    "sourceip",
    "destinationip",
    "sourceport",
    "destinationport",
    "username",
    "eventcount",
    "magnitude",
    "payload",
)

RecordingId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")]


class RecordingError(ValueError):
    """A recording is missing a file, or a file is damaged or does not match the manifest."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RecordedEvent(_Strict):
    """One row of the event table; a column the lab returned null for is None."""

    starttime: int
    endtime: int
    qid: int | None
    qidname: str | None
    category: int | None
    categoryname: str | None
    logsourceid: int | None
    logsourcename: str | None
    logsourcetypename: str | None
    devicetype: int | None
    sourceip: str | None
    destinationip: str | None
    sourceport: int | None
    destinationport: int | None
    username: str | None
    eventcount: int | None
    magnitude: int | None
    payload: str | None


class RecordedCall(_Strict):
    """One read call the recording kept: the tool, its arguments and what the gateway gave."""

    tool_id: str
    arguments: dict[str, JsonValue]
    result: ToolResult


class AuditQuery(_Strict):
    """A query the lab answered, and the answer. `query` carries its own START/STOP bounds."""

    name: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]
    query: str
    rows: list[dict[str, JsonValue]]


class RecordedFile(_Strict):
    name: str
    sha256: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    size: Annotated[int, Field(ge=0)]


class RecordingManifest(_Strict):
    schema_version: int
    recording_id: RecordingId
    offense_id: int
    window: TimeWindow
    """The events' window: an hour before the offense's start to an hour after its last update."""
    events: Annotated[int, Field(ge=0)]
    columns: list[str]
    excluded: list[str]
    """Log source type names left out of the table (QRadar's own metrics, which are 98% of the
    lab's events); the recording says so, and the replay engine knows nothing of them."""
    recorded_at: AwareDatetime
    gateway_version: str
    fork_version: str
    files: list[RecordedFile]


@dataclass(frozen=True)
class Recording:
    """A recording read into memory."""

    manifest: RecordingManifest
    offense: OffenseSnapshot
    enrichment: EnrichmentContext
    calls: tuple[RecordedCall, ...]
    events: tuple[RecordedEvent, ...]
    audits: tuple[AuditQuery, ...]

    @property
    def recording_id(self) -> str:
        return self.manifest.recording_id

    @cached_property
    def event_rows(self) -> list[dict[str, JsonValue]]:
        """The table as the replay engine reads it; read-only, shared by the runs."""
        return [event.model_dump(mode="json") for event in self.events]


def recording_path(root: Path, recording_id: str) -> Path:
    return root / RECORDINGS_DIR / recording_id


@cache
def recording_of(root: Path, recording_id: str) -> Recording:
    """The recording `recording_id` under `root`, read once per process."""
    return load_recording(recording_path(root, recording_id))


def events_bytes(events: Iterable[RecordedEvent]) -> bytes:
    """The events file: gzip with a fixed time stamp, so a recording is reproducible."""
    lines = (event.model_dump_json() for event in events)
    raw = ("\n".join(lines) + "\n").encode("utf-8")
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as handle:
        handle.write(raw)
    return buffer.getvalue()


def _json_bytes(value: BaseModel | Sequence[BaseModel]) -> bytes:
    if isinstance(value, BaseModel):
        text = value.model_dump_json(indent=2)
    else:
        text = json.dumps([item.model_dump(mode="json") for item in value], indent=2)
    return (text + "\n").encode("utf-8")


def write_recording(
    directory: Path,
    *,
    recording_id: str,
    offense: OffenseSnapshot,
    enrichment: EnrichmentContext,
    calls: Sequence[RecordedCall],
    events: Sequence[RecordedEvent],
    audits: Sequence[AuditQuery],
    window: TimeWindow,
    excluded: Sequence[str],
    recorded_at: datetime,
    gateway_version: str,
    fork_version: str,
) -> RecordingManifest:
    """Write the files and the manifest into `directory`, which must be empty or not exist."""
    if directory.exists() and any(directory.iterdir()):
        raise RecordingError(f"{directory} is not empty; a recording is never overwritten")
    payloads = {
        OFFENSE_FILE: _json_bytes(offense),
        ENRICHMENT_FILE: _json_bytes(enrichment),
        TOOLS_FILE: _json_bytes(calls),
        EVENTS_FILE: events_bytes(events),
        AUDITS_FILE: _json_bytes(audits),
    }
    manifest = RecordingManifest(
        schema_version=SCHEMA_VERSION,
        recording_id=recording_id,
        offense_id=offense.offense_id,
        window=window,
        events=len(events),
        columns=list(EVENT_COLUMNS),
        excluded=list(excluded),
        recorded_at=recorded_at,
        gateway_version=gateway_version,
        fork_version=fork_version,
        files=[
            RecordedFile(name=name, sha256=hashlib.sha256(data).hexdigest(), size=len(data))
            for name, data in payloads.items()
        ],
    )
    directory.mkdir(parents=True, exist_ok=True)
    for name, data in payloads.items():
        (directory / name).write_bytes(data)
    (directory / MANIFEST_FILE).write_bytes(_json_bytes(manifest))
    return manifest


def load_recording(directory: Path) -> Recording:
    """Read a recording. Raises RecordingError, naming the file, for anything that is wrong."""
    manifest = _validated(directory / MANIFEST_FILE, RecordingManifest)
    if manifest.schema_version != SCHEMA_VERSION:
        raise RecordingError(f"{directory}: schema version {manifest.schema_version} is unknown")
    if manifest.recording_id != directory.name:
        raise RecordingError(
            f"{directory}: the manifest names the recording {manifest.recording_id}"
        )
    if manifest.columns != list(EVENT_COLUMNS):
        raise RecordingError(f"{directory / MANIFEST_FILE}: the columns are not the recorded ones")
    by_name = {item.name: item for item in manifest.files}
    if set(by_name) != set(DATA_FILES):
        raise RecordingError(f"{directory / MANIFEST_FILE}: the file list is not {DATA_FILES}")
    raw: dict[str, bytes] = {}
    for name, entry in by_name.items():
        path = directory / name
        try:
            data = path.read_bytes()
        except OSError as error:
            raise RecordingError(f"{path}: cannot be read: {error.strerror}") from None
        if len(data) != entry.size or hashlib.sha256(data).hexdigest() != entry.sha256:
            raise RecordingError(f"{path}: does not match the manifest (the file was changed)")
        raw[name] = data
    events = _events(directory / EVENTS_FILE, raw[EVENTS_FILE])
    if len(events) != manifest.events:
        raise RecordingError(
            f"{directory / EVENTS_FILE}: {len(events)} rows, the manifest says {manifest.events}"
        )
    return Recording(
        manifest=manifest,
        offense=_parsed(directory / OFFENSE_FILE, raw[OFFENSE_FILE], OffenseSnapshot),
        enrichment=_parsed(directory / ENRICHMENT_FILE, raw[ENRICHMENT_FILE], EnrichmentContext),
        calls=tuple(_listed(directory / TOOLS_FILE, raw[TOOLS_FILE], RecordedCall)),
        events=events,
        audits=tuple(_listed(directory / AUDITS_FILE, raw[AUDITS_FILE], AuditQuery)),
    )


def _validated[ModelT: BaseModel](path: Path, model: type[ModelT]) -> ModelT:
    try:
        return model.model_validate_json(path.read_bytes())
    except OSError as error:
        raise RecordingError(f"{path}: cannot be read: {error.strerror}") from None
    except ValidationError as error:
        raise RecordingError(f"{path}: {_first_error(error)}") from None


def _parsed[ModelT: BaseModel](path: Path, data: bytes, model: type[ModelT]) -> ModelT:
    try:
        return model.model_validate_json(data)
    except ValidationError as error:
        raise RecordingError(f"{path}: {_first_error(error)}") from None


def _listed[ModelT: BaseModel](path: Path, data: bytes, model: type[ModelT]) -> list[ModelT]:
    try:
        items = json.loads(data)
    except json.JSONDecodeError as error:
        raise RecordingError(f"{path}: not JSON: {error.msg}") from None
    if not isinstance(items, list):
        raise RecordingError(f"{path}: a list is expected")
    try:
        return [model.model_validate(item) for item in items]
    except ValidationError as error:
        raise RecordingError(f"{path}: {_first_error(error)}") from None


def _events(path: Path, data: bytes) -> tuple[RecordedEvent, ...]:
    try:
        text = gzip.decompress(data).decode("utf-8")
    except (OSError, EOFError, UnicodeDecodeError) as error:
        raise RecordingError(f"{path}: not a gzip file of UTF-8 text: {error}") from None
    events: list[RecordedEvent] = []
    for number, line in enumerate(text.splitlines(), start=1):
        try:
            events.append(RecordedEvent.model_validate_json(line))
        except ValidationError as error:
            raise RecordingError(f"{path}: line {number}: {_first_error(error)}") from None
    return tuple(events)


def _first_error(error: ValidationError) -> str:
    first = error.errors()[0]
    where = ".".join(str(part) for part in first["loc"]) or "the file"
    return f"{where}: {first['msg']}"
