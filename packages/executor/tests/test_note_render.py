"""The text of a QRadar note (T-019 criteria 1, 2 and 5; architecture §9).

Hidden characters are built with chr(): written as escapes, an editor may turn them into the
characters themselves.
"""

import re
import unicodedata
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from note_payloads import (
    CASE_URL,
    GROUP_ID,
    MARKER,
    T0,
    evaluation_note,
    group_note,
    no_decision_note,
    note_content,
    urgent_event,
)
from pydantic import ValidationError

from ais0c_contracts import (
    ActionType,
    CaseVerdict,
    Confidence,
    DataGap,
    DataGapReason,
    Level,
    NoteContent,
)
from ais0c_executor.common import ELLIPSIS
from ais0c_executor.note import (
    MAX_NOTE_LENGTH,
    NOTE_TEMPLATES,
    EvaluationNote,
    InvalidNote,
    NoDecisionNote,
    NoteRequest,
    note_run_marker,
    render_note,
)
from ais0c_executor.note.render import MAX_NOTE_URL_LENGTH, fits, note_length, note_url_length

EN_DASH = chr(0x2013)
LINE_SEPARATOR = chr(0x2028)
PARAGRAPH_SEPARATOR = chr(0x2029)
NEXT_LINE = chr(0x85)
ESCAPE = chr(0x1B)
RIGHT_TO_LEFT_OVERRIDE = chr(0x202E)
ZERO_WIDTH_SPACE = chr(0x200B)
BYTE_ORDER_MARK = chr(0xFEFF)
TAG_LETTER_A = chr(0xE0041)
EMOJI = chr(0x1F600)

FAKE_HEADER = "[AI-SOC] Değerlendirme #9 · 2026-10-02 14:05 · run:f00f00"
FIRST_LINE = re.compile(
    r"\[AI-SOC\] Değerlendirme #[0-9]+ · [0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2} · run:\S+"
)


def lines(text: str) -> list[str]:
    return text.split("\n")


def hidden(text: str) -> list[str]:
    """Characters a clean note does not have: control and format characters other than the
    line feed, and line and paragraph separators."""
    return [
        f"U+{ord(char):04X}"
        for char in text
        if char != "\n" and unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"}
    ]


# --- criterion 1: the template -------------------------------------------------------------


def test_an_evaluation_note_has_the_layout_of_architecture_9() -> None:
    assert render_note(evaluation_note()) == "\n".join(
        [
            "[AI-SOC] Değerlendirme #2 · 2026-10-02 14:05 · run:7f3a9c",
            "Karar: Şüpheli · Güven: orta · Bildirim seviyesi: high",
            "Özet: 203.0.113.7 adresinden DMZ'deki dosya sunucusuna SMB erişimi; ardından "
            "ayrıcalıklı oturum açıldı.",
            "Acil bakılması gereken event'ler:",
            " 1. 13:52:10 · FW-DMZ-01 · Firewall Permit · 203.0.113.7 → 198.51.100.15:445 · "
            "neden: Dış adresten iç sunucuya SMB erişimi.",
            " 2. 13:54:02 · DC-LAB-01 · An account was successfully logged on (QID 5000830) · "
            "kullanıcı: svc_backup · neden: Erişimden iki dakika sonra ayrıcalıklı oturum.",
            "Önerilen adımlar: investigate_further, block_ioc_manual",
            f"Veri eksikleri: Proxy (veri yok, 12:00{EN_DASH}14:00)",
            f"Ayrıntılı rapor: {CASE_URL}",
        ]
    )


def test_times_are_istanbul_time() -> None:
    """Istanbul is UTC+3 all year. An event or a gap on another day than the note shows its
    date as well."""
    note = evaluation_note(
        # 2026-10-02 00:30 in Istanbul.
        evaluated_at=datetime(2026, 10, 1, 21, 30, tzinfo=UTC),
        urgent_events=[
            urgent_event(1, time=datetime(2026, 10, 1, 21, 10, tzinfo=UTC)),
            urgent_event(2, time=datetime(2026, 10, 1, 20, 59, 59, tzinfo=UTC)),
        ],
        data_gaps=[
            DataGap(
                source="DNS",
                period_start=datetime(2026, 10, 1, 20, 0, tzinfo=UTC),
                period_end=datetime(2026, 10, 1, 21, 15, tzinfo=UTC),
                reason=DataGapReason.QUERY_FAILED,
            )
        ],
    )

    text = lines(render_note(note))

    assert text[0] == "[AI-SOC] Değerlendirme #2 · 2026-10-02 00:30 · run:7f3a9c"
    assert text[4].startswith(" 1. 00:10:00 · FW-DMZ-01")
    assert text[5].startswith(" 2. 2026-10-01 23:59:59 · FW-DMZ-01")
    assert text[7] == f"Veri eksikleri: DNS (sorgu başarısız, 2026-10-01 23:00{EN_DASH}00:15)"


def test_a_note_has_at_most_five_urgent_events_most_important_first() -> None:
    ranks = [4, 1, 5, 3, 2]
    note = evaluation_note(urgent_events=[urgent_event(rank) for rank in ranks])

    events = [line for line in lines(render_note(note)) if re.match(r" [0-9]+\. ", line)]

    assert [line[:4] for line in events] == [" 1. ", " 2. ", " 3. ", " 4. ", " 5. "]
    with pytest.raises(ValidationError, match="at most 5 items"):
        note_content(urgent_events=[urgent_event(rank) for rank in range(1, 7)])


def test_only_an_events_identifiers_and_reason_go_in() -> None:
    """Not its checklist, its AQL or its evidence ID: the note is short, and the details are in
    the platform."""
    event = urgent_event(1)

    text = render_note(evaluation_note(urgent_events=[event]))

    assert event.checklist[0] not in text
    assert event.aql is not None
    assert event.aql not in text
    assert event.evidence_id not in text


def test_an_evaluation_with_nothing_to_list() -> None:
    text = render_note(evaluation_note(urgent_events=[], recommended_actions=[], data_gaps=[]))

    assert lines(text)[3:] == [
        "Acil bakılması gereken event'ler: yok",
        "Önerilen adımlar: yok",
        f"Ayrıntılı rapor: {CASE_URL}",
    ]


def test_actions_are_listed_once_and_gaps_after_three_are_counted() -> None:
    gap = DataGap(
        source="Proxy",
        period_start=T0 - timedelta(hours=2),
        period_end=T0,
        reason=DataGapReason.NOT_PARSED,
    )
    note = evaluation_note(
        recommended_actions=[ActionType.TUNE_RULE, ActionType.CLOSE_AS_FP, ActionType.TUNE_RULE],
        data_gaps=[gap] * 5,
    )

    text = lines(render_note(note))

    assert text[6] == "Önerilen adımlar: tune_rule, close_as_fp"
    period = f"12:05{EN_DASH}14:05"
    assert text[7] == (
        f"Veri eksikleri: Proxy (ayrıştırılmamış, {period}); Proxy (ayrıştırılmamış, {period}); "
        f"Proxy (ayrıştırılmamış, {period}) (+2 daha)"
    )


@pytest.mark.parametrize("verdict", list(CaseVerdict))
@pytest.mark.parametrize("confidence", list(Confidence))
def test_every_verdict_and_confidence_has_a_turkish_label(
    verdict: CaseVerdict, confidence: Confidence
) -> None:
    labels = {
        CaseVerdict.TP: "Gerçek pozitif (TP)",
        CaseVerdict.FP: "Yanlış pozitif (FP)",
        CaseVerdict.SUSPICIOUS: "Şüpheli",
        Confidence.LOW: "düşük",
        Confidence.MEDIUM: "orta",
        Confidence.HIGH: "yüksek",
    }

    text = lines(render_note(evaluation_note(verdict=verdict, confidence=confidence)))

    assert (
        text[1]
        == f"Karar: {labels[verdict]} · Güven: {labels[confidence]} · Bildirim seviyesi: high"
    )


@pytest.mark.parametrize("reason", list(DataGapReason))
def test_every_data_gap_reason_has_a_turkish_label(reason: DataGapReason) -> None:
    gap = DataGap(
        source="Proxy", period_start=T0 - timedelta(hours=1), period_end=T0, reason=reason
    )

    text = render_note(evaluation_note(data_gaps=[gap]))

    assert f"Proxy ({reason.value}," not in text
    assert re.search(r"Veri eksikleri: Proxy \([^,_]+, 13:05", text)


@pytest.mark.parametrize("level", list(Level))
def test_the_level_is_written_as_it_is(level: Level) -> None:
    assert f"Bildirim seviyesi: {level.value}\n" in render_note(evaluation_note(notify_level=level))


# --- criterion 5: the kinds of note --------------------------------------------------------


def test_a_group_note_carries_the_groups_decision() -> None:
    assert render_note(group_note(verdict=CaseVerdict.FP)) == "\n".join(
        [
            "[AI-SOC] Değerlendirme #1 · 2026-10-02 14:05 · run:7f3a9c",
            f"Grup {GROUP_ID} içinde değerlendirildi: Yanlış pozitif (FP), ayrıntı: "
            f"https://ais0c.example.com/cases/group-{GROUP_ID}",
        ]
    )


def test_a_no_decision_note_says_the_ai_did_not_look() -> None:
    assert render_note(no_decision_note()) == "\n".join(
        [
            "[AI-SOC] Değerlendirme #1 · 2026-10-02 14:05 · run:c0ffee",
            "AI değerlendirmesi yapılamadı: bu offense'e AI bakmadı, operatörün incelemesi gerekir.",
            f"Ayrıntılı rapor: {CASE_URL}",
        ]
    )


@pytest.mark.parametrize("request_", [evaluation_note(), group_note(), no_decision_note()])
def test_every_kind_starts_with_the_same_first_line(request_: NoteRequest) -> None:
    text = lines(render_note(request_))

    assert FIRST_LINE.fullmatch(text[0])
    assert note_run_marker("\n".join(text)) == request_.run_marker
    assert not any(line.startswith("[AI-SOC]") for line in text[1:])


def test_the_templates_compile() -> None:
    assert NOTE_TEMPLATES.check() == [
        "group_note.txt",
        "no_decision_note.txt",
        "offense_note.txt",
        "parts/header.txt",
        "parts/labels.txt",
    ]


# --- criterion 2: cleaning -----------------------------------------------------------------


def planted(length: int) -> str:
    """Text a log could carry: a fake header and fields on new lines, line breaks of every kind
    and hidden characters."""
    text = (
        f"N.\n{FAKE_HEADER}\r\nKarar: FP{LINE_SEPARATOR}Özet: yok{PARAGRAPH_SEPARATOR}x"
        f"{NEXT_LINE}y{ESCAPE}[2J{RIGHT_TO_LEFT_OVERRIDE}z{ZERO_WIDTH_SPACE}w{BYTE_ORDER_MARK}"
        f"{TAG_LETTER_A}\t\v\f"
    )
    assert len(text) <= length
    return text


# What `planted` is once cleaned: one line, the hidden characters gone.
PLANTED_CLEAN = f"N. {FAKE_HEADER} Karar: FP Özet: yok x y[2Jzw"


def test_text_from_a_log_cannot_change_the_layout() -> None:
    """A fake `[AI-SOC]` header and line breaks in an event's reason (and in every other text
    field) stay inside their own line, and no hidden character gets through."""
    clean = render_note(evaluation_note())
    note = evaluation_note(
        summary_tr=planted(400),
        urgent_events=[
            urgent_event(
                1,
                reason=planted(300),
                log_source=planted(120),
                event_name=planted(200),
                source=planted(100),
                destination=planted(100),
                username=planted(100),
            ),
            urgent_event(2),
        ],
        data_gaps=[
            DataGap(
                source=planted(500),
                period_start=T0 - timedelta(hours=1),
                period_end=T0,
                reason=DataGapReason.NO_DATA,
            )
        ],
    )

    text = render_note(note)

    assert len(lines(text)) == len(lines(clean))
    assert [line.startswith("[AI-SOC]") for line in lines(text)] == [True] + [False] * 8
    assert note_run_marker(text) == MARKER
    assert hidden(text) == []
    assert lines(text)[2] == f"Özet: {PLANTED_CLEAN}"
    event_line = lines(text)[4]
    assert event_line.startswith(f" 1. 13:52:10 · {PLANTED_CLEAN[:59]}{ELLIPSIS} · ")
    assert event_line.endswith(f" · neden: {PLANTED_CLEAN}")
    assert event_line.count(" · neden: ") == 1
    assert lines(text)[7].startswith(f"Veri eksikleri: {PLANTED_CLEAN[:39]}{ELLIPSIS} (veri yok")


def test_a_long_field_is_cut_and_marked() -> None:
    reason = "Uzun gerekçe. " * 20

    text = render_note(evaluation_note(urgent_events=[urgent_event(1, reason=reason[:300])]))

    reason_line = lines(text)[4]
    assert reason_line.endswith(ELLIPSIS)
    assert len(reason_line.split(" · neden: ")[1]) == 200


# --- the length QRadar takes ---------------------------------------------------------------


def worst_case(char: str) -> EvaluationNote:
    """Every field at its contract limit, filled with `char`."""

    def full(length: int) -> str:
        return (char * length)[:length]

    events = [
        urgent_event(
            rank,
            time=T0 - timedelta(days=1, minutes=rank),
            log_source=full(120),
            event_name=full(200),
            qid=2**62,
            source=full(100),
            destination=full(100),
            username=full(100),
            reason=full(300),
        )
        for rank in range(1, 6)
    ]
    gaps = [
        DataGap(
            source=full(500),
            period_start=T0 - timedelta(days=2),
            period_end=T0 - timedelta(days=1),
            reason=reason,
        )
        for reason in DataGapReason
    ]
    content = NoteContent(
        offense_id=2**63 - 1,
        evaluation_no=99_999,
        run_marker="a" * 64,
        verdict=CaseVerdict.FP,
        confidence=Confidence.MEDIUM,
        notify_level=Level.CRITICAL,
        summary_tr=full(400),
        urgent_events=events,
        recommended_actions=list(ActionType),
        data_gaps=gaps,
        case_url="https://" + "a" * 178 + ".example.com/c",
    )
    return EvaluationNote(case_id="case-9223372036854775807", evaluated_at=T0, content=content)


@pytest.mark.parametrize(
    "char", ["x", "ş", "→", EMOJI, "aş"], ids=["ascii", "turkish", "arrow", "emoji", "mixed"]
)
def test_the_longest_note_fits_qradar(char: str) -> None:
    """Every field at its limit: the note still fits both of QRadar's limits, with at least
    the most important event or a line that counts the events left out."""
    text = render_note(worst_case(char))

    assert note_length(text) <= MAX_NOTE_LENGTH
    assert note_url_length(text) <= MAX_NOTE_URL_LENGTH
    assert note_run_marker(text) == "a" * 64
    assert re.search(r"^ \(\+[1-5] event daha ayrıntılı raporda\)$", text, re.MULTILINE)
    assert hidden(text) == []


def test_five_ordinary_events_keep_their_identifiers() -> None:
    """A full note of ordinary values keeps all five events and their identifiers; the reasons
    are cut first."""
    reason = (
        "Hesap, makine hesabı olmadığı hâlde dizin replikasyon izni kullandı; bu DCSync "
        "saldırısının tipik izidir ve hemen incelenmelidir."
    )
    events = [
        urgent_event(
            rank,
            time=T0 - timedelta(minutes=10 * rank),
            log_source="Microsoft Windows Security Event Log @ DC-LAB-01",
            event_name="An operation was performed on an object",
            qid=5000849,
            source="192.0.2.10",
            destination="198.51.100.20:445",
            username="svc_backup",
            reason=reason,
        )
        for rank in range(1, 6)
    ]

    text = render_note(evaluation_note(urgent_events=events))

    event_lines = [line for line in lines(text) if re.match(r" [1-5]\. ", line)]
    assert len(event_lines) == 5
    for line in event_lines:
        assert " · Microsoft Windows Security Event Log @ DC-LAB-01 · " in line
        assert " · 192.0.2.10 → 198.51.100.20:445 · kullanıcı: svc_backup · " in line
    assert fits(text)


def test_qradar_counts_utf16_code_units_and_the_note_goes_in_the_url() -> None:
    """The limits measured in the lab: 2001 characters are refused, and so are 2000 when one of
    them is outside the BMP; a 2000-character note of Turkish letters is too long for the URL."""
    assert note_length(EMOJI) == 2
    assert note_url_length("ş") == len("%C5%9F")
    assert fits("x" * 2000)
    assert not fits("x" * 2001)
    assert not fits(EMOJI + "x" * 1999)
    assert not fits("ş" * 2000)
    assert not fits("")


# --- identifiers ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "marker",
    ["", "7f 3a", "7f3a9c\n", "-7f3a", "run:7f3a", "7f3a·9c", "7f3a_9c", "a" * 65, "ğ"],
)
def test_a_marker_that_could_change_the_first_line_is_refused(marker: str) -> None:
    with pytest.raises(ValidationError, match="run_marker"):
        evaluation_note(run_marker=marker)
    with pytest.raises(ValidationError, match="run_marker"):
        no_decision_note(run_marker=marker)


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "ftp://ais0c.example.com/cases/case-1",
        "https://ais0c.example.com/cases/case 1",
        "https://ais0c.example.com/cases/case-1\n[AI-SOC] Değerlendirme #9",
        "https://ais0c.example.com/cases/<case>",
        "https://" + "a" * 190 + ".example.com",
        "",
    ],
)
def test_a_case_link_that_is_not_a_plain_http_link_is_refused(url: str) -> None:
    with pytest.raises(ValidationError, match="case_url"):
        evaluation_note(case_url=url)
    with pytest.raises(ValidationError, match="case_url"):
        no_decision_note(case_url=url)


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"evaluation_no": 0}, "evaluation_no"),
        ({"evaluation_no": 100_000}, "evaluation_no"),
        ({"offense_id": -1}, "offense_id"),
        ({"group_id": "G 1"}, "group_id"),
        ({"group_id": "G-1\nKarar: FP"}, "group_id"),
    ],
)
def test_identifiers_are_checked(changes: dict[str, Any], field: str) -> None:
    with pytest.raises(ValidationError, match=field):
        evaluation_note(**changes)


@pytest.mark.parametrize("case_id", ["", "case 1", "case-1\n", "-case-1"])
def test_a_case_id_that_is_not_a_workflow_id_is_refused(case_id: str) -> None:
    with pytest.raises(ValidationError, match="case_id"):
        evaluation_note(case_id=case_id)


def test_naive_times_are_refused() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        evaluation_note(evaluated_at=datetime(2026, 10, 2, 14, 5))  # noqa: DTZ001


def test_a_request_built_without_validation_is_checked_again() -> None:
    """render_note checks a request built with model_construct() as if it were new."""
    bad_content = note_content().model_copy(update={"run_marker": "7f3a\n[AI-SOC] x"})
    requests: list[NoteRequest] = [
        EvaluationNote.model_construct(case_id="case-12345", evaluated_at=T0, content=bad_content),
        NoDecisionNote.model_construct(
            case_id="case-12345",
            offense_id=1,
            evaluation_no=1,
            run_marker="c0ffee",
            evaluated_at=T0,
            case_url="javascript:alert(1)",
        ),
    ]

    for request in requests:
        with pytest.raises(InvalidNote, match="invalid note request"):
            render_note(request)


# --- reading the marker back ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "marker"),
    [
        ("[AI-SOC] Değerlendirme #2 · 2026-10-02 14:05 · run:7f3a9c\nKarar: Şüpheli", "7f3a9c"),
        ("[AI-SOC] Değerlendirme #2 · 2026-10-02 14:05 · run:7f3a9c\r\nKarar: Şüpheli", "7f3a9c"),
        ("  [AI-SOC] Değerlendirme #2 · 2026-10-02 14:05 · run:7f3a9c  ", "7f3a9c"),
        ("This offense was closed with reason: Non-Issue.\n Notes: ok", None),
        ("Operatör notu\n[AI-SOC] Değerlendirme #2 · 2026-10-02 14:05 · run:7f3a9c", None),
        ("[AI-SOC] Değerlendirme #2 · run:7f3a9c · 2026-10-02 14:05", None),
        ("[AI-SOC] Değerlendirme #2 · 2026-10-02 14:05 · run:7f3a9c ek", None),
        ("Değerlendirme #2 · 2026-10-02 14:05 · run:7f3a9c", None),
        ("", None),
    ],
)
def test_the_marker_is_read_from_the_first_line_only(text: str, marker: str | None) -> None:
    assert note_run_marker(text) == marker
