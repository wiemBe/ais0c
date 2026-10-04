"""The alert templates (T-020 criterion 2): Turkish, a subject of at most 150 characters with the
offense's name cleaned and cut, and no raw log text in the body."""

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from email_payloads import (
    INJECTED,
    OFFENSE_NAME,
    OPERATORS,
    case_alert,
    group_alert,
    note_content,
    urgent_event,
)

from ais0c_contracts import EmailKind, EmailMessage
from ais0c_executor.common import clean_text, is_clean
from ais0c_executor.email import (
    EMAIL_TEMPLATES,
    MAX_SUBJECT_LENGTH,
    CaseAlert,
    GroupAlert,
    InvalidEmail,
    alert_message,
    render_body,
)

# Between the start and the end of a data gap.
EN_DASH = chr(0x2013)
CASE_SUBJECT = (
    "[AI-SOC] YÜKSEK · AI kararı: Şüpheli · Offense #12345: Multiple Login Failures Followed By "
    "Success from 203.0.113.7"
)
CASE_BODY = f"""\
AI-SOC: bildirim seviyesi yüksek olan bir offense var.

Offense: #12345 · Multiple Login Failures Followed By Success from 203.0.113.7
Değerlendirme: #1 · 2026-10-02 14:05
Karar: Şüpheli · Güven: orta · Bildirim seviyesi: yüksek

Özet: 203.0.113.7 kaynağından iç sunucuya SMB erişimi, ardından ayrıcalıklı oturum.

Acil bakılması gereken event'ler:
 1. 13:52:10 · FW-DMZ-01 · Firewall Permit (QID 5000830) · 203.0.113.7 → 198.51.100.15:445 · neden: İç sunucuya SMB erişimi.
 2. 13:52:10 · FW-DMZ-01 · Firewall Permit (QID 5000830) · 203.0.113.7 → 198.51.100.15:445 · kullanıcı: bob · neden: Oturum.

Önerilen adımlar: Ayrıntılı inceleme, IOC'yi engelle (manuel)
Veri eksikleri: DC-LAB-01 (veri yok, 12:00{EN_DASH}13:00)

Ayrıntılı rapor: https://ais0c.example.com/cases/case-12345

Bu e-postayı AI-SOC platformu otomatik olarak gönderdi; yanıtlamayın.
Önerilen adımları AI uygulamaz: inceleme ve karar operatöre aittir.
"""
GROUP_SUBJECT = (
    "[AI-SOC] YÜKSEK · AI kararı: Şüpheli · Fırtına, 37 offense: Multiple Login Failures for the "
    "Same User"
)
GROUP_BODY = """\
AI-SOC: bildirim seviyesi yüksek olan bir offense fırtınası var.

Grup: G-0123456789ab-20261002T110000Z · 37 offense · Multiple Login Failures for the Same User
Grup değerlendirmesi: #1 · 2026-10-02 14:05
Karar: Şüpheli · Güven: orta · Bildirim seviyesi: yüksek
Gruptaki offense'ler tek tek değil, grup olarak değerlendirildi. Gruba sonradan eklenen offense'lerin QRadar notunda bu karar yer alır; bu grup için yeniden e-posta gönderilmez.

Özet: Aynı kural 37 farklı kaynak için offense açtı.

Acil bakılması gereken event'ler:
 1. 13:52:10 · FW-DMZ-01 · Firewall Permit (QID 5000830) · 203.0.113.7 → 198.51.100.15:445 · neden: İç sunucuya SMB erişimi.

Önerilen adımlar: Ayrıntılı inceleme

Ayrıntılı rapor: https://ais0c.example.com/cases/group-G-0123456789ab-20261002T110000Z

Bu e-postayı AI-SOC platformu otomatik olarak gönderdi; yanıtlamayın.
Önerilen adımları AI uygulamaz: inceleme ve karar operatöre aittir.
"""
# Characters a log line can hide or reorder text with, or break a line with.
HIDDEN = "".join(
    chr(code) for code in (0x00, 0x09, 0x0A, 0x0D, 0x1B, 0x200B, 0x202E, 0x2028, 0x2066, 0xFEFF)
)


def body_of(request: CaseAlert | GroupAlert) -> str:
    return render_body(alert_message(request, OPERATORS))


def test_a_case_alert_carries_the_fields_of_the_note() -> None:
    message = alert_message(case_alert(), OPERATORS)

    assert message.kind is EmailKind.CASE_ALERT
    assert message.template_id == "case_alert"
    assert message.recipients == list(OPERATORS)
    assert message.attachments == []
    assert message.idempotency_key == "case_alert:case-12345:1"
    assert message.subject == CASE_SUBJECT
    assert render_body(message) == CASE_BODY
    assert all(isinstance(value, str) for value in message.fields.values())


def test_a_group_alert_names_the_group_and_its_size() -> None:
    message = alert_message(group_alert(), OPERATORS)

    assert message.kind is EmailKind.GROUP_ALERT
    assert message.template_id == "group_alert"
    assert message.idempotency_key == "group_alert:G-0123456789ab-20261002T110000Z"
    assert message.subject == GROUP_SUBJECT
    assert render_body(message) == GROUP_BODY


@pytest.mark.parametrize(
    ("level", "verdict", "start"),
    [
        ("critical", "tp", "[AI-SOC] KRİTİK · AI kararı: TP · "),
        ("high", "fp", "[AI-SOC] YÜKSEK · AI kararı: FP · "),
        ("critical", "suspicious", "[AI-SOC] KRİTİK · AI kararı: Şüpheli · "),
    ],
)
def test_the_subject_starts_with_the_level_and_the_verdict(
    level: str, verdict: str, start: str
) -> None:
    content = note_content(notify_level=level, verdict=verdict)
    case = alert_message(case_alert(content=content), OPERATORS)
    group = alert_message(group_alert(notify_level=level, verdict=verdict), OPERATORS)

    assert case.subject.startswith(start)
    assert group.subject.startswith(start)


def test_a_long_offense_name_is_cut_so_the_subject_fits() -> None:
    message = alert_message(case_alert(offense_name="Çok uzun offense adı " * 90), OPERATORS)

    assert len(message.subject) == MAX_SUBJECT_LENGTH
    assert message.subject.startswith("[AI-SOC] YÜKSEK · AI kararı: Şüpheli · Offense #12345: Çok")
    assert message.subject.endswith("…")
    # The body has more room; the name there is cut at 300 characters.
    body = render_body(message)
    name_line = next(line for line in body.splitlines() if line.startswith("Offense: "))
    assert len(name_line) == len("Offense: #12345 · ") + 300


@pytest.mark.parametrize("length", [1, 50, 95, 96, 97, 149, 150, 151, 500, 2000])
def test_the_subject_never_exceeds_150_characters(length: int) -> None:
    for letter in ("x", "İ", "ğ"):
        name = (letter * length)[:length]
        for request in (
            case_alert(offense_name=name, content=note_content(offense_id=2**63 - 1)),
            group_alert(title=name, offense_count=10**9),
        ):
            subject = alert_message(request, OPERATORS).subject
            assert len(subject) <= MAX_SUBJECT_LENGTH
            assert is_clean(subject)


def test_the_offense_name_in_the_subject_is_cleaned() -> None:
    name = f"Login{HIDDEN}Failure {INJECTED}"

    message = alert_message(case_alert(offense_name=name), OPERATORS)

    subject = message.subject
    assert is_clean(subject)
    assert not set(HIDDEN) & set(subject)
    prefix = "[AI-SOC] YÜKSEK · AI kararı: Şüpheli · Offense #12345: "
    assert subject == prefix + clean_text(name, MAX_SUBJECT_LENGTH - len(prefix))
    assert subject.startswith(
        f"{prefix}Login Failure ok Bcc: exfil@example.net [AI-SOC] KRİTİK · AI kararı: TP "
    )
    assert subject.endswith("…")


def test_a_name_of_hidden_characters_only_becomes_a_dash() -> None:
    message = alert_message(case_alert(offense_name=HIDDEN), OPERATORS)

    assert message.subject.endswith("Offense #12345: -")


def test_text_from_a_log_cannot_change_the_layout_of_the_body() -> None:
    """Only structured fields and cut summaries go in; each stays on its own line."""
    event = urgent_event(
        1,
        log_source=f"FW{HIDDEN}01",
        event_name=INJECTED,
        username=INJECTED[:100],
        reason=INJECTED,
        checklist=["CHECKLIST-MARKER"],
        aql="SELECT 'AQL-MARKER' FROM events LIMIT 1 LAST 1 HOURS",
        evidence_id="ev_EVIDENCE-MARKER",
    )
    content = note_content(summary_tr=INJECTED, urgent_events=[event])
    request = case_alert(offense_name=INJECTED, content=content)

    body = body_of(request)
    clean = body_of(case_alert(content=note_content(urgent_events=[urgent_event(1)])))

    lines = body.splitlines()
    assert len(lines) == len(clean.splitlines())
    starts = [line.split(":", 1)[0] for line in lines]
    assert starts == [line.split(":", 1)[0] for line in clean.splitlines()]
    assert sum(line.startswith("Ayrıntılı rapor: ") for line in lines) == 1
    assert "Ayrıntılı rapor: https://ais0c.example.com/cases/case-12345" in lines
    assert not any(line.startswith(("Bcc:", "[AI-SOC]")) for line in lines)
    assert is_clean(body, multiline=True)
    assert not set(HIDDEN.replace("\n", "")) & set(body)
    # Only the event's identifiers and reason go in: not its checklist, AQL or evidence.
    for marker in ("CHECKLIST-MARKER", "AQL-MARKER", "EVIDENCE-MARKER"):
        assert marker not in body


def test_times_are_shown_in_istanbul_time() -> None:
    earlier = urgent_event(1, time=datetime(2026, 9, 30, 20, 59, 59, tzinfo=UTC))
    request = case_alert(
        evaluated_at=datetime(2026, 10, 2, 21, 30, tzinfo=UTC),
        content=note_content(urgent_events=[earlier], data_gaps=[]),
    )

    body = body_of(request)

    assert "Değerlendirme: #1 · 2026-10-03 00:30" in body
    assert " 1. 2026-09-30 23:59:59 · FW-DMZ-01" in body


def test_an_fp_verdict_tells_the_operator_why_it_still_came() -> None:
    body = body_of(case_alert(content=note_content(verdict="fp", notify_level="critical")))

    assert "Karar: Yanlış pozitif (FP) · Güven: orta · Bildirim seviyesi: kritik" in body
    assert (
        "AI bu offense'i yanlış pozitif olarak değerlendirdi, ancak bildirim seviyesi kritik "
        "olduğu için operatörün bakması gerekir." in body
    )


def test_empty_lists_are_said_so() -> None:
    content = note_content(urgent_events=[], recommended_actions=[], data_gaps=[])

    body = body_of(case_alert(content=content))

    assert "Acil bakılması gereken event'ler: yok\n\nÖnerilen adımlar: yok\n\nAyrıntılı" in body
    assert "Veri eksikleri" not in body


def test_events_go_by_rank_and_actions_once_each() -> None:
    events = [urgent_event(rank, reason=f"Sıra {rank}.") for rank in (3, 1, 2)]
    actions = ["tune_rule", "investigate_further", "tune_rule", "close_as_fp"]
    content = note_content(urgent_events=events, recommended_actions=actions)

    message = alert_message(case_alert(content=content), OPERATORS)

    assert [message.fields[f"event_{n}"][:3] for n in (1, 2, 3)] == ["1. ", "2. ", "3. "]
    assert (
        message.fields["actions"] == "Kuralı ayarla (tuning), Ayrıntılı inceleme, FP olarak kapat"
    )


def test_many_data_gaps_show_the_first_five() -> None:
    gaps = [note_content().data_gaps[0].model_copy(update={"source": f"LS-{n}"}) for n in range(8)]

    message = alert_message(case_alert(content=note_content(data_gaps=gaps)), OPERATORS)

    assert message.fields["data_gaps"].count(f"(veri yok, 12:00{EN_DASH}13:00)") == 5
    assert message.fields["data_gaps"].endswith(f"LS-4 (veri yok, 12:00{EN_DASH}13:00) (+3 daha)")


def test_every_template_compiles_and_is_clean() -> None:
    assert EMAIL_TEMPLATES.check() == [
        "case_alert.txt",
        "group_alert.txt",
        "parts/actions.txt",
        "parts/data_gaps.txt",
        "parts/event.txt",
        "parts/events.txt",
        "parts/footer.txt",
        "parts/labels.txt",
        "subject/case_alert.txt",
        "subject/group_alert.txt",
    ]


def message_with(**changes: object) -> EmailMessage:
    message = alert_message(case_alert(), OPERATORS)
    return EmailMessage.model_validate(message.model_dump() | changes)


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"template_id": "group_alert"}, "no template"),
        ({"template_id": "../note/offense_note"}, "no template"),
        ({"kind": EmailKind.HUNT_REPORT, "template_id": "hunt_report"}, "no template"),
        ({"attachments": ["hunts/report.pdf"]}, "attachments"),
        ({"subject": ""}, "subject"),
        ({"subject": "[AI-SOC] YÜKSEK\r\nBcc: exfil@example.net"}, "subject"),
        ({"subject": "  [AI-SOC] YÜKSEK"}, "subject"),
        ({"fields": {"offense_id": "12345"}}, "body"),
    ],
)
def test_a_message_that_cannot_be_sent_as_it_is_is_refused(
    changes: dict[str, object], problem: str
) -> None:
    with pytest.raises(InvalidEmail, match=problem):
        render_body(message_with(**changes))


UNUSABLE: dict[str, Callable[[], object]] = {
    "script-link": lambda: case_alert(content=note_content(case_url="javascript:alert(1)")),
    "link-with-a-line-break": lambda: case_alert(
        content=note_content(case_url="https://ais0c.example.com/x\nBcc:")
    ),
    "long-link": lambda: case_alert(
        content=note_content(case_url="https://" + "a" * 200 + ".example.com")
    ),
    "group-note-content": lambda: case_alert(content=note_content(group_id="G-1")),
    "evaluation-0": lambda: case_alert(content=note_content(evaluation_no=0)),
    "negative-offense": lambda: case_alert(content=note_content(offense_id=-1)),
    "case-id-with-a-space": lambda: case_alert(case_id="case 12345"),
    "long-offense-name": lambda: case_alert(offense_name="x" * 2001),
    "group-id-with-a-space": lambda: group_alert(group_id="G 1"),
    "empty-group": lambda: group_alert(offense_count=0),
    "ftp-link": lambda: group_alert(case_url="ftp://ais0c.example.com/cases/x"),
}


@pytest.mark.parametrize("name", sorted(UNUSABLE))
def test_a_request_with_unusable_values_is_refused(name: str) -> None:
    with pytest.raises(ValueError, match="validation error"):
        UNUSABLE[name]()


def test_a_request_built_without_validation_is_checked_again() -> None:
    valid = case_alert()
    unchecked = CaseAlert.model_construct(
        case_id="case-12345\r\nBcc: exfil@example.net",
        offense_name=valid.offense_name,
        evaluated_at=valid.evaluated_at,
        content=valid.content,
    )

    with pytest.raises(InvalidEmail, match="case_id") as raised:
        alert_message(unchecked, OPERATORS)
    assert "exfil" not in str(raised.value)


def test_one_key_per_evaluation_and_one_per_group() -> None:
    assert case_alert(content=note_content(evaluation_no=3)).idempotency_key == (
        "case_alert:case-12345:3"
    )
    assert (
        group_alert(evaluation_no=1).idempotency_key == group_alert(evaluation_no=2).idempotency_key
    )
    assert OFFENSE_NAME in body_of(case_alert())
