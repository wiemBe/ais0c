"""Note requests for the note tests: synthetic values, RFC 5737 addresses, example.com links."""

from datetime import UTC, datetime

from ais0c_contracts import (
    ActionType,
    CaseVerdict,
    Confidence,
    DataGap,
    DataGapReason,
    Level,
    NoteContent,
    UrgentEvent,
)
from ais0c_executor.note import EvaluationNote, NoDecisionNote

# 14:05 in Istanbul (UTC+3).
T0 = datetime(2026, 10, 2, 11, 5, tzinfo=UTC)
OFFENSE_ID = 12345
CASE_ID = "case-12345"
GROUP_CASE_ID = "group-G-1a2b3c4d5e6f-20261002T110500Z"
GROUP_ID = "G-1a2b3c4d5e6f-20261002T110500Z"
CASE_URL = "https://ais0c.example.com/cases/case-12345"
MARKER = "7f3a9c"


def urgent_event(rank: int = 1, **changes: object) -> UrgentEvent:
    values: dict[str, object] = {
        "rank": rank,
        "time": datetime(2026, 10, 2, 10, 52, 10, tzinfo=UTC),
        "log_source": "FW-DMZ-01",
        "event_name": "Firewall Permit",
        "qid": None,
        "source": "203.0.113.7",
        "destination": "198.51.100.15:445",
        "username": None,
        "reason": "Dış adresten iç sunucuya SMB erişimi.",
        "checklist": ["Bu adresten başka bir sunucuya erişim var mı?"],
        "aql": "SELECT sourceip FROM events WHERE sourceip = '203.0.113.7' LAST 1 HOURS",
        "evidence_id": "ev_01JBNOTETEST0001",
    }
    return UrgentEvent.model_validate(values | changes)


def note_content(**changes: object) -> NoteContent:
    values: dict[str, object] = {
        "offense_id": OFFENSE_ID,
        "evaluation_no": 2,
        "run_marker": MARKER,
        "verdict": CaseVerdict.SUSPICIOUS,
        "confidence": Confidence.MEDIUM,
        "notify_level": Level.HIGH,
        "summary_tr": "203.0.113.7 adresinden DMZ'deki dosya sunucusuna SMB erişimi; ardından "
        "ayrıcalıklı oturum açıldı.",
        "urgent_events": [
            urgent_event(1),
            urgent_event(
                2,
                time=datetime(2026, 10, 2, 10, 54, 2, tzinfo=UTC),
                log_source="DC-LAB-01",
                event_name="An account was successfully logged on",
                qid=5000830,
                source=None,
                destination=None,
                username="svc_backup",
                reason="Erişimden iki dakika sonra ayrıcalıklı oturum.",
            ),
        ],
        "recommended_actions": [ActionType.INVESTIGATE_FURTHER, ActionType.BLOCK_IOC_MANUAL],
        "data_gaps": [
            DataGap(
                source="Proxy",
                period_start=datetime(2026, 10, 2, 9, 0, tzinfo=UTC),
                period_end=datetime(2026, 10, 2, 11, 0, tzinfo=UTC),
                reason=DataGapReason.NO_DATA,
            )
        ],
        "case_url": CASE_URL,
        "group_id": None,
    }
    return NoteContent.model_validate(values | changes)


def evaluation_note(
    *, case_id: str = CASE_ID, evaluated_at: datetime = T0, **changes: object
) -> EvaluationNote:
    """The note of an evaluation; `changes` go to its NoteContent."""
    return EvaluationNote(
        case_id=case_id, evaluated_at=evaluated_at, content=note_content(**changes)
    )


def group_note(**changes: object) -> EvaluationNote:
    """The short note of offense 12346, added to the group whose case is GROUP_CASE_ID."""
    group: dict[str, object] = {
        "offense_id": 12346,
        "evaluation_no": 1,
        "group_id": GROUP_ID,
        "case_url": "https://ais0c.example.com/cases/" + GROUP_CASE_ID,
    }
    return EvaluationNote(
        case_id=GROUP_CASE_ID, evaluated_at=T0, content=note_content(**(group | changes))
    )


def no_decision_note(**changes: object) -> NoDecisionNote:
    values: dict[str, object] = {
        "case_id": CASE_ID,
        "offense_id": OFFENSE_ID,
        "evaluation_no": 1,
        "run_marker": "c0ffee",
        "evaluated_at": T0,
        "case_url": CASE_URL,
    }
    return NoDecisionNote.model_validate(values | changes)
