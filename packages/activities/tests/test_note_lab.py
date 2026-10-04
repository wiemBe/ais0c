"""T-019 criterion 7: the note activity writes a note to an offense of the lab QRadar.

`@pytest.mark.lab`: skipped unless QRADAR_LAB_URL and QRADAR_LAB_TOKEN are set. It writes a
note only when QRADAR_LAB_OFFENSE_ID names the offense to write to, and it runs against the dev
stack's path to QRadar; without either it is skipped and says why.

| Variable | Meaning |
|---|---|
| `QRADAR_LAB_OFFENSE_ID` | The lab offense the note is written to |
| `AIS0C_DATABASE_URL` | The dev stack's application database |
| `AIS0C_GATEWAY_URL` | The dev stack's gateway, started with the `qradar` compose profile and the lab override (deploy/compose/README.md) |
| `AIS0C_EXECUTOR_SECRETS_DIR` | The directory of `gateway-token-qradar-note-write` (`deploy/compose/secrets/executor`) |

The test never holds a QRadar token: it writes and reads through the gateway, as the executor
does. It writes one note about as long as a note gets (five urgent events, Turkish text) under
a marker of its own. It then makes the database forget that the note was written, as an
attempt that lost QRadar's answer would, and runs the activity again: the activity finds the
note in QRadar and does not write it twice. Last, it reads the note back and checks that QRadar
kept it as it was sent. Writes are switched on for the test and set back afterwards.
"""

import json
import os
import secrets
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from temporalio.testing import ActivityEnvironment

from ais0c_activities import NoteRuntime, SessionFactory, load_note_runtime, system_run
from ais0c_activities.note import NOTE_BUDGET, NOTE_WINDOW, GatewayOffenseNotes
from ais0c_agents import GatewayError
from ais0c_contracts import (
    ActionType,
    CaseSource,
    CaseVerdict,
    Confidence,
    DataGap,
    DataGapReason,
    Level,
    NoteContent,
    TimeWindow,
    UrgentEvent,
)
from ais0c_executor.note import (
    EXECUTOR_ID,
    EvaluationNote,
    NoteResult,
    note_run_marker,
    render_note,
)
from ais0c_executor.note.render import note_length, note_url_length
from ais0c_storage import ActorKind, NoteStatus, PlatformFlag
from ais0c_storage.repositories import (
    create_case,
    get_case,
    get_platform_flag,
    set_platform_flag,
    update_note_status,
)

pytestmark = [pytest.mark.lab, pytest.mark.anyio]

REQUIRED = (
    "QRADAR_LAB_OFFENSE_ID",
    "AIS0C_DATABASE_URL",
    "AIS0C_GATEWAY_URL",
    "AIS0C_EXECUTOR_SECRETS_DIR",
)


@pytest.fixture
async def runtime() -> AsyncIterator[NoteRuntime]:
    missing = [name for name in REQUIRED if not os.environ.get(name, "").strip()]
    if missing:
        pytest.skip(f"set {', '.join(missing)} to write a note to the lab QRadar")
    try:
        runtime = await load_note_runtime()
    except GatewayError as error:
        pytest.skip(f"the gateway's note profile cannot be reached: {error}")
    try:
        yield runtime
    finally:
        await runtime.close()


async def test_a_note_reaches_the_lab_offense_once(runtime: NoteRuntime) -> None:
    offense_id = int(os.environ["QRADAR_LAB_OFFENSE_ID"])
    case_id = f"case-{offense_id}"
    marker = f"t019-{secrets.token_hex(4)}"
    now = datetime.now(UTC)
    request = lab_note(case_id, offense_id, marker, now)
    text = render_note(request)
    await open_case(runtime.sessions, case_id, offense_id, now)
    previous = await writes_enabled(runtime.sessions)

    await switch(runtime.sessions, True, "T-019 lab testi: Action Executor'ın not yazma yolu.")
    try:
        first = await ActivityEnvironment().run(runtime.activities.write_offense_note, request)
        await forget(runtime.sessions, offense_id, marker, now)
        second = await ActivityEnvironment().run(runtime.activities.write_offense_note, request)
        kept = await notes_with(runtime, case_id, offense_id, marker)
    finally:
        await switch(
            runtime.sessions, previous, "T-019 lab testi bitti; anahtar eski hâline döndü."
        )

    print(
        json.dumps(
            {
                "offense_id": offense_id,
                "run_marker": marker,
                "length": note_length(text),
                "url_length": note_url_length(text),
                "lines": text.count("\n") + 1,
                "first": first.result.value,
                "retry": second.result.value,
            },
            indent=2,
        )
    )
    assert first.result is NoteResult.WRITTEN
    assert second.result is NoteResult.SKIPPED_DUPLICATE
    assert kept == [text]


def lab_note(case_id: str, offense_id: int, marker: str, now: datetime) -> EvaluationNote:
    """A note near QRadar's limit, with Turkish text and every part of the template."""
    reason = (
        "Hesap, makine hesabı olmadığı hâlde dizin replikasyon izni kullandı; bu DCSync "
        "saldırısının tipik izidir ve hemen incelenmelidir."
    )
    events = [
        UrgentEvent(
            rank=rank,
            time=now - timedelta(minutes=10 * rank),
            log_source="Microsoft Windows Security Event Log @ DC-LAB-01",
            event_name="An operation was performed on an object",
            qid=5000849,
            source="192.0.2.10",
            destination="198.51.100.20:445",
            username="svc_t019",
            reason=reason,
            checklist=["Hesabın aynı saatte başka bir oturumu var mı?"],
            aql=None,
            evidence_id="ev_01JBT019LABTEST01",
        )
        for rank in range(1, 6)
    ]
    content = NoteContent(
        offense_id=offense_id,
        evaluation_no=1,
        run_marker=marker,
        verdict=CaseVerdict.SUSPICIOUS,
        confidence=Confidence.MEDIUM,
        notify_level=Level.HIGH,
        summary_tr=(
            "T-019 lab testi: bu not Action Executor'ın not yazma yolunu dener ve gerçek bir "
            "değerlendirme değildir. Alanlar sentetiktir; adresler belgeleme aralığındandır."
        ),
        urgent_events=events,
        recommended_actions=[ActionType.INVESTIGATE_FURTHER, ActionType.RESET_CREDENTIALS_MANUAL],
        data_gaps=[
            DataGap(
                source="Proxy",
                period_start=now - timedelta(hours=2),
                period_end=now - timedelta(hours=1),
                reason=DataGapReason.NO_DATA,
            )
        ],
        case_url=f"https://ais0c.example.com/cases/{case_id}",
    )
    return EvaluationNote(case_id=case_id, evaluated_at=now, content=content)


async def open_case(sessions: SessionFactory, case_id: str, offense_id: int, now: datetime) -> None:
    """`notes_written` rows belong to a case; the offense's case is opened if it is not there."""
    async with sessions.begin() as session:
        if await get_case(session, case_id) is None:
            await create_case(
                session,
                case_id=case_id,
                source=CaseSource.OFFENSE,
                offense_id=offense_id,
                sla_due_at=now,
                workflow_id=case_id,
                run_id="t019-lab",
            )


async def writes_enabled(sessions: SessionFactory) -> bool:
    async with sessions() as session:
        flag = await get_platform_flag(session, PlatformFlag.WRITES_ENABLED)
    return flag is not None and flag.enabled


async def switch(sessions: SessionFactory, enabled: bool, reason: str) -> None:
    async with sessions.begin() as session:
        await set_platform_flag(
            session,
            PlatformFlag.WRITES_ENABLED,
            enabled=enabled,
            reason=reason,
            actor_kind=ActorKind.USER,
            actor_id="t019-lab-test",
        )


async def forget(sessions: SessionFactory, offense_id: int, marker: str, now: datetime) -> None:
    """What the database holds when an attempt wrote the note but lost QRadar's answer."""
    async with sessions.begin() as session:
        await update_note_status(
            session,
            offense_id=offense_id,
            run_marker=marker,
            status=NoteStatus.FAILED,
            written_at=now,
            error="T-019 lab test: the first attempt's answer was lost",
        )


async def notes_with(runtime: NoteRuntime, case_id: str, offense_id: int, marker: str) -> list[str]:
    """The offense's notes with the marker on their first line, read through the gateway."""
    now = datetime.now(UTC)
    async with system_run(
        sessions=runtime.sessions,
        gateway=runtime.gateway,
        profile=runtime.toolset,
        agent_id=EXECUTOR_ID,
        case_id=case_id,
        objective="Read back the T-019 lab test note.",
        window=TimeWindow(start=now - NOTE_WINDOW, end=now),
        budget=NOTE_BUDGET,
    ) as run:
        texts = await GatewayOffenseNotes(run).notes_containing(offense_id, f"run:{marker}")
    return [text for text in texts if note_run_marker(text) == marker]
