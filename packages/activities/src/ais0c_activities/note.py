"""The activity that writes a QRadar offense note (architecture §9, "QRadar offense notu").

`write_offense_note` runs the executor's `NoteWriter` (`ais0c_executor.note`): it builds the
note from a fixed template, skips a note QRadar already has, checks the kill switch right before
the write and records the outcome in `notes_written`. This module gives the writer QRadar's
notes through the MCP Policy Gateway's `qradar-note-write` profile. The gateway serves that
profile only to runs of the pseudo agent `action-executor` (T-018), so the calls of each attempt
are one system run of it (`ais0c_activities.gateway.system_run`).

The profile's tool list (`GET /v1/tools`) names its write tool as one, so the agents'
`ToolsetProfile` refuses to load it, on purpose. `NoteToolset` is the executor's own reading of
that list. Only the executor holds the profile's token (deploy/compose/secrets/executor/).

The activity's name is in `ais0c_activities.names` since T-045: CaseWorkflow calls it on the
`soc-executor` task queue, in the executor's own process (T-33 (1)).
"""

import os
import re
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, JsonValue, ValidationError, model_validator
from sqlalchemy.ext.asyncio import AsyncEngine
from temporalio import activity
from temporalio.exceptions import ApplicationError

from ais0c_activities.db import SessionFactory
from ais0c_activities.gateway import SystemRun, system_run, utc_now
from ais0c_activities.names import WRITE_OFFENSE_NOTE
from ais0c_activities.runtime import GATEWAY_URL_ENV, RuntimeConfigError, read_token
from ais0c_agents import GatewayClient, GatewayError
from ais0c_agents.gateway_http import TOOLS_PATH, HttpGatewayClient
from ais0c_contracts import Budget, CostClass, TimeWindow, ToolResult, ToolStatus
from ais0c_executor.common import (
    DEFAULT_SECRETS_DIR,
    EXECUTOR_ID,
    EXECUTOR_SECRETS_DIR_ENV,
    KillSwitch,
)
from ais0c_executor.note import (
    InvalidNote,
    NoteOutcome,
    NoteRequest,
    NoteWriter,
    OffenseNotes,
    OffenseNotesError,
)
from ais0c_storage import create_engine, create_session_factory, database_url

NOTE_PROFILE: Final = "qradar-note-write"
ADD_NOTE: Final = "add_offense_note"
READ_NOTES: Final = "get_offense_notes"
# Where the executor's gateway token is: `gateway-token-qradar-note-write` (ais0c_executor.common).
# The window a note call declares: at most this long (the profile allows 31 days).
NOTE_WINDOW: Final = timedelta(days=30)
# Declared, not enforced: one note takes a page or two of reads and one write.
NOTE_BUDGET: Final = Budget(tokens=0, tool_calls=12, seconds=300)
# Notes per read. Only the notes that hold the run marker come back, normally none or one; a
# page the gateway cuts at its size limit is read on from the first note it left out.
NOTES_PAGE_SIZE: Final = 10
MAX_NOTE_READS: Final = 10
# Gateway denials a later attempt may get past: the quota pool had no room.
RETRYABLE_DENIALS: Final = frozenset({"quota_exhausted"})
# What notes_containing may look for: text without LIKE wildcards or quotes.
_SEARCHABLE: Final = re.compile(r"[A-Za-z0-9:-]+")


class NoteTool(BaseModel):
    """A tool of the note profile, as the gateway lists it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    description: str
    schema_version: str
    cost_class: CostClass
    risk: Literal["read", "write"]
    parameters: dict[str, JsonValue]


class NoteToolset(BaseModel):
    """The `qradar-note-write` profile as the gateway lists it (`GET /v1/tools`)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: Literal["qradar-note-write"]
    connector: str
    tools: tuple[NoteTool, ...]

    @model_validator(mode="after")
    def _has_the_note_tools(self) -> Self:
        risks = {tool.id: tool.risk for tool in self.tools}
        for tool_id, risk in ((ADD_NOTE, "write"), (READ_NOTES, "read")):
            if risks.get(tool_id) != risk:
                raise ValueError(f"the profile has no {risk} tool {tool_id}")
        return self


class NoteGatewayClient(HttpGatewayClient):
    """The gateway client with the executor's token; it can list the note profile's tools."""

    async def fetch_note_toolset(self) -> NoteToolset:
        response = await self._request("GET", TOOLS_PATH)
        try:
            return NoteToolset.model_validate_json(response.content)
        except ValidationError:
            raise GatewayError(
                f"the gateway's tool list is not the {NOTE_PROFILE} profile"
            ) from None


class GatewayOffenseNotes:
    """`OffenseNotes` through the gateway, in one system run of `action-executor`."""

    def __init__(self, run: SystemRun) -> None:
        self._run = run

    async def notes_containing(self, offense_id: int, text: str) -> list[str]:
        """The texts of the offense's notes that hold `text`, read a few at a time.

        QRadar filters them (`note_text LIKE`), so only the notes that hold it come back.
        """
        if not _SEARCHABLE.fullmatch(text):
            raise ValueError("notes are looked up by letters, digits, ':' and '-' only")
        texts: list[str] = []
        start = 0
        for _ in range(MAX_NOTE_READS):
            result = await self._send(
                READ_NOTES,
                {
                    "offense_id": offense_id,
                    "filter": f'note_text LIKE "%{text}%"',
                    "fields": "id,note_text",
                    "start": start,
                    "limit": NOTES_PAGE_SIZE,
                },
                reason="Look for this note on the offense before adding it, so it is not added "
                "twice.",
                expected_evidence="The offense's notes that hold the note's run marker, if any.",
            )
            rows = result.data
            for row in rows:
                note_text = row.get("note_text")
                if isinstance(note_text, str):
                    texts.append(note_text)
            if result.truncated and not rows:
                raise OffenseNotesError(
                    f"{READ_NOTES}: a note of offense {offense_id} is too large to read through "
                    "the gateway",
                    retryable=False,
                )
            if not result.truncated and len(rows) < NOTES_PAGE_SIZE:
                return texts
            # A page cut by the gateway's size limit goes on from the first row left out.
            start += len(rows)
        raise OffenseNotesError(
            f"{READ_NOTES}: offense {offense_id} has more notes holding {text} than "
            f"{MAX_NOTE_READS} reads return",
            retryable=False,
        )

    async def add_note(self, offense_id: int, text: str) -> None:
        await self._send(
            ADD_NOTE,
            {"offense_id": offense_id, "note_text": text},
            reason="Add the AI-SOC note of this evaluation to the offense (architecture §9).",
            expected_evidence="The note QRadar created.",
        )

    async def _send(
        self,
        tool_id: str,
        arguments: dict[str, JsonValue],
        *,
        reason: str,
        expected_evidence: str,
    ) -> ToolResult:
        try:
            result = await self._run.send(
                tool_id, arguments, reason=reason, expected_evidence=expected_evidence
            )
        except GatewayError as error:
            raise OffenseNotesError(f"{tool_id}: {error}", retryable=True) from error
        if result.status is ToolStatus.OK:
            return result
        why = result.deny_reason or result.status.value
        retryable = result.status is ToolStatus.ERROR or why.split(":", 1)[0] in RETRYABLE_DENIALS
        raise OffenseNotesError(f"{tool_id}: {result.status.value}: {why}", retryable=retryable)


class NoteActivities:
    """The note activity, with the executor's gateway client and the note profile's tools."""

    def __init__(
        self,
        *,
        sessions: SessionFactory,
        gateway: GatewayClient,
        toolset: NoteToolset,
        kill_switch: KillSwitch | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._sessions = sessions
        self._gateway = gateway
        self._toolset = toolset
        self._clock = clock
        self._writer = NoteWriter(sessions=sessions, kill_switch=kill_switch, clock=clock)

    def activities(self) -> list[Callable[..., object]]:
        return [self.write_offense_note]

    @activity.defn(name=WRITE_OFFENSE_NOTE)
    async def write_offense_note(self, request: NoteRequest) -> NoteOutcome:
        """Write the note of `request` to its offense once (D-18).

        Returns the outcome: written, already there, writes off or refused by the gateway. A
        note that cannot be built fails without a retry (`InvalidNote`); a failure a retry may
        get past (gateway or QRadar unreachable) fails the attempt, and Temporal retries it.
        """
        try:
            return await self._writer.write(request, lambda: self._notes(request))
        except InvalidNote as error:
            raise ApplicationError(str(error), type="InvalidNote", non_retryable=True) from None

    @asynccontextmanager
    async def _notes(self, request: NoteRequest) -> AsyncIterator[OffenseNotes]:
        now = self._clock()
        async with system_run(
            sessions=self._sessions,
            gateway=self._gateway,
            profile=self._toolset,
            agent_id=EXECUTOR_ID,
            case_id=request.case_id,
            objective=(
                f"Write the AI-SOC note of evaluation {request.evaluation_no} on QRadar offense "
                f"{request.offense_id}."
            ),
            window=TimeWindow(start=now - NOTE_WINDOW, end=now),
            budget=NOTE_BUDGET,
            clock=self._clock,
        ) as run:
            yield GatewayOffenseNotes(run)


@dataclass(frozen=True)
class NoteRuntime:
    """The note activity and what it was built from."""

    sessions: SessionFactory
    gateway: NoteGatewayClient
    toolset: NoteToolset
    activities: NoteActivities
    engine: AsyncEngine | None = None
    """The database engine behind `sessions`, when the runtime created it."""

    async def close(self) -> None:
        if self.engine is not None:
            await self.engine.dispose()


async def load_note_runtime(environ: Mapping[str, str] | None = None) -> NoteRuntime:
    """The note activity built from `environ` (default `os.environ`).

    | Variable | Meaning | Default |
    |---|---|---|
    | `AIS0C_DATABASE_URL` | Application database | none |
    | `AIS0C_GATEWAY_URL` | MCP Policy Gateway | none |
    | `AIS0C_EXECUTOR_SECRETS_DIR` | Directory of `gateway-token-qradar-note-write` | `/run/secrets` |

    Asks the gateway for the profile's tools, so the gateway must be up.
    """
    env = os.environ if environ is None else environ
    gateway_url = env.get(GATEWAY_URL_ENV, "").strip()
    if not gateway_url:
        raise RuntimeConfigError(f"{GATEWAY_URL_ENV} is not set")
    secrets_dir = Path(env.get(EXECUTOR_SECRETS_DIR_ENV, "").strip() or DEFAULT_SECRETS_DIR)
    gateway = NoteGatewayClient(
        gateway_url, read_token(secrets_dir / f"gateway-token-{NOTE_PROFILE}")
    )
    toolset = await gateway.fetch_note_toolset()
    engine = create_engine(database_url(env))
    sessions = create_session_factory(engine)
    return NoteRuntime(
        sessions=sessions,
        gateway=gateway,
        toolset=toolset,
        activities=NoteActivities(sessions=sessions, gateway=gateway, toolset=toolset),
        engine=engine,
    )
