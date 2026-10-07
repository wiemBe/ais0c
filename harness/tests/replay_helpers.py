"""A small synthetic recording and the pieces the replay tests share (T-052).

The events are a made-up domain controller's: three DCSync events (4662 by `svc_backup` from
three servers), a few logons, a QRadar notification. Every address is a documentation address.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from pydantic import JsonValue

from ais0c_contracts import (
    CatalogContext,
    CatalogLogSource,
    CatalogMode,
    CatalogRule,
    EnrichmentContext,
    OffenseSnapshot,
    TimeWindow,
    ToolCoverage,
    ToolResult,
    ToolStatus,
)
from ais0c_harness.replay.record import RawRecording, build_recording, event_window
from ais0c_harness.replay.recording import (
    AuditQuery,
    RecordedCall,
    RecordedEvent,
    Recording,
    load_recording,
)

START: Final = datetime(2026, 10, 5, 14, 57, 43, 905000, tzinfo=UTC)
NOW: Final = START + timedelta(minutes=5)
BASE_MS: Final = int(START.timestamp() * 1000)
OFFENSE: Final = OffenseSnapshot(
    offense_id=77,
    description="AIS0C - DCSync by a non-machine account",
    offense_type="Username",
    offense_source="svc_backup",
    rule_ids=[100501],
    rule_names=["AIS0C - DCSync by a non-machine account"],
    categories=["Object Access"],
    magnitude=7,
    start_time=START,
    last_updated_time=START,
    event_count=3,
    log_source_ids=[10],
    source_ips=["198.51.100.23", "198.51.100.24", "198.51.100.25"],
    destination_ips=["192.0.2.10"],
    usernames=["svc_backup"],
)
ENRICHMENT: Final = EnrichmentContext(
    catalog=CatalogContext(
        rules=[CatalogRule(rule_id=100501, mode=CatalogMode.ANALYZE)],
        log_sources=[
            CatalogLogSource(
                log_source_id=10,
                type_name="Microsoft Windows Security Event Log",
                description="Domain controller, Windows security events",
            )
        ],
    ),
    critical_asset_hits=[],
    ioc_hits=[],
    entity_resolutions=[],
)
DC_TYPE: Final = "Microsoft Windows Security Event Log"
OBJECT: Final = "Success Audit: An operation was performed on an object"
LOGON: Final = "Success Audit: An account was successfully logged on"


def event(
    offset_ms: int,
    *,
    qid: int = 5000849,
    qidname: str = OBJECT,
    logsourceid: int = 10,
    logsourcename: str = "DC-01",
    typename: str = DC_TYPE,
    devicetype: int = 12,
    sourceip: str | None = "198.51.100.23",
    username: str | None = "svc_backup",
    payload: str | None = None,
    eventcount: int = 1,
) -> RecordedEvent:
    return RecordedEvent(
        starttime=BASE_MS + offset_ms,
        endtime=BASE_MS + offset_ms,
        qid=qid,
        qidname=qidname,
        category=8052,
        categoryname="Object Access",
        logsourceid=logsourceid,
        logsourcename=logsourcename,
        logsourcetypename=typename,
        devicetype=devicetype,
        sourceip=sourceip,
        destinationip="192.0.2.10",
        sourceport=0,
        destinationport=0,
        username=username,
        eventcount=eventcount,
        magnitude=5,
        payload=payload
        if payload is not None
        else f"EventID=4662 Account Name: {username} Source Network Address: {sourceip}",
    )


EVENTS: Final = (
    event(0, sourceip="198.51.100.23"),
    event(1000, sourceip="198.51.100.24"),
    event(2000, sourceip="198.51.100.25"),
    event(
        3000,
        qid=4624,
        qidname=LOGON,
        sourceip="203.0.113.7",
        username="analyst1",
        payload="EventID=4624 Logon Type: 3 Account Name: analyst1",
    ),
    event(
        4000,
        qid=4624,
        qidname=LOGON,
        sourceip="203.0.113.8",
        username=None,
        payload="EventID=4624 Logon Type: 3",
    ),
    event(
        60_000,
        qid=38750003,
        qidname="Information Message",
        logsourceid=65,
        logsourcename="System Notification-2",
        typename="System Notification",
        devicetype=147,
        sourceip="192.0.2.50",
        username=None,
        payload="qflow: [INFO] current interval",
    ),
)


def tool_result(evidence_id: str, rows: Sequence[dict[str, JsonValue]]) -> ToolResult:
    return ToolResult(
        status=ToolStatus.OK,
        evidence_id=evidence_id,
        data=list(rows),
        truncated=False,
        coverage=ToolCoverage(complete=True, gaps=[]),
    )


CALLS: Final = (
    RecordedCall(
        tool_id="get_offense",
        arguments={"offense_id": 77},
        result=tool_result("ev_recorded_offense", [{"id": 77, "offense_source": "svc_backup"}]),
    ),
    RecordedCall(
        tool_id="get_rule",
        arguments={"rule_id": 100501},
        result=tool_result("ev_recorded_rule", [{"id": 100501, "name": "DCSync"}]),
    ),
)


def raw_recording(
    events: Sequence[RecordedEvent] = EVENTS, audits: Sequence[AuditQuery] = ()
) -> RawRecording:
    return RawRecording(
        offense=OFFENSE,
        enrichment=ENRICHMENT,
        calls=list(CALLS),
        events=[event.model_dump(mode="json") for event in events],
        audits=list(audits),
        window=event_window(OFFENSE),
    )


def write_synthetic(directory: Path, events: Sequence[RecordedEvent] = EVENTS) -> Recording:
    """Write the synthetic recording into `directory` (named like its ID) and read it back."""
    build_recording(raw_recording(events), directory, recorded_at=NOW)
    return load_recording(directory)


def window_of(recording: Recording) -> TimeWindow:
    return recording.manifest.window
